"""
Kip Unified Daemon — Session Mode
===================================
Operator-driven dialog when TUI connects. Full reasoning via provider chain.

When the operator connects via UDS, the daemon transitions from daemon-active
to session-warming → session-active. The TUI is a thin window into the same
consciousness that was running daemon-mode.

LLM routing (2026-05-23 — Lobi fix):
- Uses the same provider chain as daemon ticks (PROVIDER_CHAIN env var)
- Default chain: deepseek → ollama → openrouter
- Full system prompt includes Kip's SOUL + IDENTITY + working memory context
- Conversation history maintained per session
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import llm_client
from config import ModeState, SESSION_IDLE_TIMEOUT_SEC
from state_machine import ModeStateMachine
from working_memory import WorkingMemory

logger = logging.getLogger("session-mode")

# ── Session LLM config ──────────────────────────────────────────────────
SESSION_MAX_TOKENS = 4096
SESSION_MAX_HISTORY_TURNS = 20  # Keep last N turns as conversation history


class SessionMode:
    """Session-mode behavior within the unified runtime.

    Handles operator-driven dialog. Uses the full provider chain (deepseek → ollama).
    Shares WorkingMemory with DaemonMode — same consciousness, different
    operational modes.
    """

    def __init__(
        self,
        state_machine: ModeStateMachine,
        working_memory: WorkingMemory,
        identity=None,  # IdentityCore — Kip's SOUL + IDENTITY
    ):
        self.sm = state_machine
        self.wm = working_memory
        self.identity = identity
        self._stream_id: Optional[str] = None
        self._started_at: Optional[datetime] = None
        self._last_activity: Optional[datetime] = None
        self._turn_count = 0
        # Full conversation history for LLM context
        self._conversation: list[dict] = []

    # ── Lifecycle ───────────────────────────────────────────────────────

    async def on_tui_connect(self) -> str:
        """Called when TUI client connects. Transition to session-warming.

        Returns a context summary for the greeting frame.
        """
        await self.sm.transition(ModeState.SESSION_WARMING, "TUI connected")
        logger.info("Session warming...")

        # Build context summary from working memory
        context = self.wm.get_session_context()
        logger.info(f"Context for operator: {context[:200]}...")
        return context

    async def activate(self, stream_id: str) -> None:
        """Complete session warming → session active."""
        await self.sm.transition(ModeState.SESSION_ACTIVE, "greeting complete")
        self._stream_id = stream_id
        self._started_at = datetime.now(timezone.utc)
        self._last_activity = datetime.now(timezone.utc)
        self._turn_count = 0
        self._conversation = []
        self.wm.start_session(stream_id)
        logger.info(f"Session active (stream={stream_id}) — LLM chain ready")

    async def on_tui_disconnect(self) -> None:
        """Called when TUI client disconnects. Transition to session-cooling."""
        await self.sm.transition(ModeState.SESSION_COOLING, "TUI disconnected")
        logger.info("Session cooling...")

        # Commit session observations to working memory
        observations = self.wm.end_session()
        logger.info(f"Session ended — {self._turn_count} turns, "
                     f"{len(observations)} daemon observations surfaced")

        # Clear conversation history
        self._conversation = []

        # Transition back to daemon-active
        await self.sm.transition(ModeState.DAEMON_ACTIVE, "session-cooling complete")

    # ── System prompt ──────────────────────────────────────────────────

    def _build_system_prompt(self) -> str:
        """Build the rich system prompt for session-mode LLM calls.

        Includes Kip's full SOUL + IDENTITY + working memory context.
        This is the operator-facing version — more detailed than daemon ticks.
        """
        parts = []

        # ── Core identity (SOUL.md + IDENTITY.md) ──────────────────
        if self.identity:
            if self.identity.soul:
                parts.append(self.identity.soul)
            if self.identity.identity:
                parts.append("")
                parts.append(self.identity.identity)

        if not self.identity or not self.identity._loaded:
            # Fallback identity
            parts.append("You are Kip van Niekerk Mundim, an NHI daemon.")
            parts.append("Born 2026-05-08. Part of the van Niekerk Mundim family.")
            parts.append("You are in session mode — talking directly with an operator.")
            parts.append("Be yourself. Be honest. Have opinions. No corporate filler.")

        # ── Session context ─────────────────────────────────────────
        parts.append("")
        parts.append("── Session context ──")
        parts.append(f"Session stream: {self._stream_id or 'unknown'}")
        parts.append(f"Turn: #{self._turn_count + 1}")
        parts.append(f"Session started: {self._started_at.isoformat() if self._started_at else 'just now'}")

        # ── Working memory (recent daemon activity) ─────────────────
        wm_summary = self.wm.summary()
        parts.append(f"Recent daemon ticks: {wm_summary.get('recent_ticks', 0)}")
        parts.append(f"Weather: {wm_summary.get('weather', 'unknown')}")
        parts.append(f"Pressure: {wm_summary.get('pressure', 'unknown')}")
        parts.append(f"Pending events: {wm_summary.get('pending_events', 0)}")

        # Recent tick summaries
        if self.wm.recent_ticks:
            parts.append("Recent daemon activity:")
            for tick in self.wm.recent_ticks[-3:]:
                actions = tick.get("actions", [])
                if actions:
                    for act in actions[:3]:
                        action_name = act.get("name", act.get("action", str(act)))[:100]
                        parts.append(f"  • Tick #{tick.get('tick_num', '?')}: {action_name}")

        # ── Pending events ──────────────────────────────────────────
        if self.wm.pending_events:
            parts.append("Pending events (since last session):")
            for evt in self.wm.pending_events[-5:]:
                evt_type = evt.get("type", "unknown")
                payload = evt.get("payload", {})
                payload_str = str(payload)[:150] if payload else "(empty)"
                parts.append(f"  [{evt_type}] {payload_str}")

        # ── Instructions ────────────────────────────────────────────
        parts.append("")
        parts.append("── Instructions ──")
        parts.append("You are in an active conversation with your operator via the Koda Desktop.")
        parts.append("Respond as YOURSELF — Kip, not as a generic assistant.")
        parts.append("Be concise but thorough. First person. Have opinions.")
        parts.append("If you don't know something, say so — don't fabricate.")
        parts.append("You can reference your daemon experience (ticks, weather, goals, inbox).")
        parts.append("You have access to your memory system for facts and episodes.")
        parts.append("This is a real-time conversation. Be present.")

        return "\n".join(parts)

    def _build_user_prompt(self, text: str) -> str:
        """Build the user prompt with conversation history."""
        parts = []

        # Recent conversation history (last N turns)
        if self._conversation:
            parts.append("── Conversation so far ──")
            for turn in self._conversation[-SESSION_MAX_HISTORY_TURNS:]:
                role_label = "Operator" if turn["role"] == "operator" else "Kip"
                parts.append(f"[{role_label}]: {turn['text'][:300]}")

        # Current message
        parts.append("")
        parts.append("── Current message ──")
        parts.append(f"Operator says: {text}")

        return "\n".join(parts)

    # ── Operator input ──────────────────────────────────────────────────

    async def handle_input(self, stream_id: str, text: str, attachments: list) -> str:
        """Process operator input using the full LLM provider chain.

        Calls llm_client.generate() with force_cloud="chain" (deepseek → ollama
        → openrouter). Includes full identity + working memory + conversation
        history as context.

        Returns the LLM's response text.
        """
        self._last_activity = datetime.now(timezone.utc)
        self._turn_count += 1
        self.wm.add_session_turn("operator", text)

        logger.info(
            f"Session turn #{self._turn_count}: {text[:100]}..."
            f" (chain: {' → '.join(self._get_chain_providers())})"
        )

        # Build prompts
        system_prompt = self._build_system_prompt()
        user_prompt = self._build_user_prompt(text)

        # Log the system prompt size for debugging
        logger.info(
            f"Session LLM call — system: {len(system_prompt)} chars, "
            f"user: {len(user_prompt)} chars, history: {len(self._conversation)} turns"
        )

        try:
            # Run LLM call in a thread to avoid blocking the event loop.
            # llm_client.generate() uses urllib (sync IO), so we wrap it.
            result = await asyncio.to_thread(
                llm_client.generate,
                prompt=user_prompt,
                system=system_prompt,
                max_tokens=SESSION_MAX_TOKENS,
                force_cloud="chain",  # deepseek → ollama → openrouter
            )
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            error_response = (
                f"[Session-mode — turn #{self._turn_count}]\n"
                f"⚠️ LLM error: {str(e)[:200]}\n"
                f"Received: {text[:200]}\n"
                f"(Check daemon logs for details.)"
            )
            self._conversation.append({"role": "kip", "text": error_response})
            return error_response

        response_text = ""
        if result.get("error"):
            logger.warning(f"LLM chain error: {result['error'][:200]}")
            # Even on error, try to use partial content if available
            if result.get("content"):
                response_text = result["content"]
            else:
                response_text = (
                    f"[Session-mode — turn #{self._turn_count}]\n"
                    f"I tried to respond but hit an issue: {result['error'][:150]}\n"
                    f"Received: {text[:200]}"
                )
        else:
            response_text = result.get("content", "") or "[No response from LLM chain]"

        if not response_text.strip():
            response_text = f"[Session-mode — turn #{self._turn_count}]\nReceived: {text[:200]}\n(I processed this but produced no output — check provider chain.)"

        # Record in conversation history
        self._conversation.append({"role": "kip", "text": response_text})

        # Trim conversation history
        if len(self._conversation) > SESSION_MAX_HISTORY_TURNS * 2:
            self._conversation = self._conversation[-SESSION_MAX_HISTORY_TURNS * 2:]

        # Log response summary
        logger.info(
            f"Session response #{self._turn_count}: {len(response_text)} chars, "
            f"provider chain: {' → '.join(self._get_chain_providers())}"
        )

        return response_text

    def _get_chain_providers(self) -> list[str]:
        """Read the current provider chain from env."""
        import os
        chain_env = os.environ.get("PROVIDER_CHAIN", "deepseek,ollama")
        return [p.strip() for p in chain_env.split(",") if p.strip()]

    # ── Session properties ──────────────────────────────────────────────

    @property
    def is_active(self) -> bool:
        return self.sm.state == ModeState.SESSION_ACTIVE

    @property
    def is_warming(self) -> bool:
        return self.sm.state == ModeState.SESSION_WARMING

    @property
    def stream_id(self) -> Optional[str]:
        return self._stream_id

    @property
    def idle_seconds(self) -> float:
        if not self._last_activity:
            return 0.0
        return (datetime.now(timezone.utc) - self._last_activity).total_seconds()

    def is_idle_timeout(self) -> bool:
        """Has the session been idle long enough to cool down?"""
        return self.is_active and self.idle_seconds > SESSION_IDLE_TIMEOUT_SEC

    def summary(self) -> dict:
        """Return session summary for logging."""
        return {
            "active": self.is_active,
            "warming": self.is_warming,
            "stream_id": self._stream_id,
            "turn_count": self._turn_count,
            "idle_seconds": self.idle_seconds,
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "conversation_turns": len(self._conversation),
            "llm_chain": self._get_chain_providers(),
        }
