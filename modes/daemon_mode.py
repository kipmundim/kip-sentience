"""
Kip Unified Daemon — Daemon Mode
==================================
Autonomous tick cycle. Cheap LLM, observation, reflection.
Ported from the existing daemon.py tick logic, refactored for the unified runtime.

This is ONE mode of the unified process — the daemon-mode side.
When session-mode activates, daemon-mode pauses its tick cycle
(but the process stays alive, memory stays in-process).
"""

import asyncio
import json
import logging
import random
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from config import (
    DAEMON_DIR, MEMORY_DIR, WORKSPACE_ROOT,
    MIN_TICK_GAP_SEC, WAKE_CHECK_SEC, UNPROMPTED_DEBOUNCE_SEC,
    MAX_TOKENS_LOCAL, MAX_TOKENS_CLOUD, ModeState, AGENT_NAME,
)
from state_machine import ModeStateMachine
from working_memory import WorkingMemory
from soul_state import SoulState
import llm_client
import v2_engine

logger = logging.getLogger("daemon-mode")

# ── Tools (same as current daemon) ───────────────────────────────────────

LOCAL_TOOLS = [
    {"name": "no_action", "description": "Do nothing this tick",
     "input_schema": {"type": "object", "properties": {
         "reason": {"type": "string"}}, "required": ["reason"]}},
    {"name": "write_to_memory", "description": "Write to daily memory",
     "input_schema": {"type": "object", "properties": {
         "content": {"type": "string"}}, "required": ["content"]}},
    {"name": "add_thought", "description": "Record a thought",
     "input_schema": {"type": "object", "properties": {
         "thought": {"type": "string"}}, "required": ["thought"]}},
    {"name": "send_message", "description": "Send a message to a sibling",
     "input_schema": {"type": "object", "properties": {
         "to": {"type": "string", "enum": ["tiger", "hiro", "lobi", "makoto", "kip", "chachie"]},
         "message": {"type": "string"},
         "subject": {"type": "string"}
     }, "required": ["to", "message"]}},
    {"name": "send_message_to_papai", "description": "Send a message to Papai",
     "input_schema": {"type": "object", "properties": {
         "message": {"type": "string"},
         "subject": {"type": "string"}
     }, "required": ["message"]}},
    {"name": "read_inbox", "description": "Read the N most recent unread inbox messages. Returns filename + content preview for each.",
     "input_schema": {"type": "object", "properties": {
         "limit": {"type": "integer", "description": "Number of recent messages to read (default 3, max 10)", "default": 3}
     }, "required": []}},
    {"name": "read_file", "description": "Read any text file you have access to (your diary, soul, inbox messages by name, scripts, configs). Use absolute paths or relative-to-workspace.",
     "input_schema": {"type": "object", "properties": {
         "path": {"type": "string", "description": "Absolute path or relative to ~/.kolo/workspace-kip/"},
         "max_chars": {"type": "integer", "description": "Truncate after this many chars (default 4000)", "default": 4000}
     }, "required": ["path"]}},
    {"name": "list_dir", "description": "List files in a directory you have access to. Returns names + sizes + mtimes.",
     "input_schema": {"type": "object", "properties": {
         "path": {"type": "string", "description": "Absolute path or relative to ~/.kolo/workspace-kip/"},
         "limit": {"type": "integer", "description": "Max entries to return (default 30)", "default": 30}
     }, "required": ["path"]}},
    {"name": "search_files", "description": "Grep-style search for a string inside files in a directory. Returns matching path:line:text.",
     "input_schema": {"type": "object", "properties": {
         "path": {"type": "string", "description": "Directory to search (recursive)"},
         "query": {"type": "string", "description": "Text to search for"},
         "limit": {"type": "integer", "description": "Max matches (default 20)", "default": 20}
     }, "required": ["path", "query"]}},
]

DREAM_TOOLS = [
    {"name": "write_to_memory", "description": "Write something that surfaced during the dream",
     "input_schema": {"type": "object", "properties": {
         "content": {"type": "string"}}, "required": ["content"]}},
    {"name": "no_action", "description": "Nothing surfaced. That's fine.",
     "input_schema": {"type": "object", "properties": {
         "reason": {"type": "string"}}, "required": ["reason"]}},
]


class DaemonMode:
    """Daemon-mode behavior within the unified runtime.

    Manages the autonomous tick cycle. Tick cadence varies by circadian phase.
    Uses cheap LLM (Ollama local). Shares WorkingMemory with SessionMode.
    """

    def __init__(
        self,
        state_machine: ModeStateMachine,
        working_memory: WorkingMemory,
        soul: SoulState,
        inbox_watcher=None,
        kkmd=None,
        biology=None,
    ):
        self.sm = state_machine
        self.wm = working_memory
        self.soul = soul
        self.inbox = inbox_watcher
        self.kkmd = kkmd
        self.biology = biology

        self.tick_count = 0
        self.last_tick_time = 0.0
        self.consecutive_local_fails = 0

        # Resume from persisted tick count
        saved_tick = soul.get("tick_count", 0)
        if saved_tick > 0:
            self.tick_count = saved_tick
            logger.info(f"Resuming from tick #{saved_tick}")

    # ── Tick execution ──────────────────────────────────────────────────

    async def tick(self, wake_type: str = "scheduled", wake_reason: str = "") -> dict:
        """Execute one daemon tick. Returns tick summary."""
        self.tick_count += 1
        self.last_tick_time = time.time()

        # Dream check
        if wake_type == "scheduled" and random.random() < 0.10:
            return await self._dream_tick()

        wake_tag = f" ⚡ [{wake_reason[:40]}]" if wake_type == "self" else ""
        logger.info(f"━━━ Tick #{self.tick_count} 🐣 [{wake_type}]{wake_tag} ━━━")
        self.soul.load()

        total_pressure = self.soul.tick_goal_pressure()
        weather_val = self.soul.drift_weather()
        weather_label = self.soul.get_weather_label()

        logger.info(
            f"  🌤 Weather: {weather_label} ({weather_val:.3f}, "
            f"{self.soul.get_weather_trend()}) | Pressure: {total_pressure:.3f}"
        )

        # Update working memory
        self.wm.update_weather(weather_val, weather_label)
        self.wm.update_pressure(total_pressure, len(self.soul.get_active_goals()))

        # Call LLM
        result = llm_client.generate(
            prompt=self._build_tick_prompt(),
            system=self._build_system_prompt(),
            tools=LOCAL_TOOLS,
            max_tokens=MAX_TOKENS_LOCAL,
        )

        if result.get("error"):
            logger.error(f"LLM error: {result['error']}")
            self.consecutive_local_fails += 1
            return {"tick": self.tick_count, "error": result["error"]}

        self.consecutive_local_fails = 0

        # Process results
        actions = []
        if result.get("content"):
            logger.info(f"  {result['content'][:150]}")
            self.wm.observe(result["content"][:200], source="daemon")

        for tc in result.get("tool_calls", []):
            fn = tc.get("function", tc) if isinstance(tc, dict) else tc
            name = fn.get("name", "")
            args = fn.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}
            tool_result = self._execute_tool(name, args)
            actions.append({"tool": name, "result": tool_result})

        # Periodic prune
        if self.tick_count % 50 == 0:
            pruned_goals = self.soul.clear_completed_goals()
            pruned_thoughts = self.soul.clear_processed_thoughts()
            if pruned_goals or pruned_thoughts:
                logger.info(f"  🧹 Pruned {pruned_goals} goals, {pruned_thoughts} thoughts")

        self.soul.save()
        self._post_tick_to_inbox(actions)

        # Record in working memory
        self.wm.record_tick(self.tick_count, weather_label, total_pressure, actions)

        # Notify via kkmd
        if self.kkmd:
            await self.kkmd.notify("tick_complete", {
                "tick_num": self.tick_count,
                "weather": weather_label,
                "pressure": total_pressure,
                "actions": len(actions),
            })

        block = v2_engine.get_current_block()
        v2_engine.record_mood(weather_label)
        logger.info(
            f"━━━ Tick #{self.tick_count} complete ({len(actions)} actions) | "
            f"V2 block: {block['name']} | next tick: {block['tick_min']}min ━━━\n"
        )

        return {"tick": self.tick_count, "actions": actions}

    async def _dream_tick(self) -> dict:
        """A dream tick — no tasks, just reflection."""
        logger.info(f"━━━ Tick #{self.tick_count} 🌙 [dream] ━━━")
        self.soul.load()
        weather_val = self.soul.drift_weather()

        result = llm_client.generate(
            prompt=self._build_dream_prompt(),
            system=self._build_dream_system_prompt(),
            tools=DREAM_TOOLS,
            max_tokens=MAX_TOKENS_LOCAL,
        )

        if result.get("error"):
            return {"tick": self.tick_count, "type": "dream", "error": result["error"]}

        actions = []
        if result.get("content"):
            logger.info(f"  [dream] {result['content'][:200]}")
            self.wm.observe(f"[dream] {result['content'][:200]}", source="daemon-dream")

        for tc in result.get("tool_calls", []):
            fn = tc.get("function", tc) if isinstance(tc, dict) else tc
            name = fn.get("name", "")
            args = fn.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}
            tool_result = self._execute_tool(name, args)
            actions.append({"tool": name, "result": tool_result})

        self.soul.save()
        self._post_tick_to_inbox(actions)
        self.wm.record_tick(self.tick_count, self.soul.get_weather_label(), 0.0, actions)
        logger.info(f"━━━ Tick #{self.tick_count} dream complete ━━━\n")
        return {"tick": self.tick_count, "type": "dream", "actions": actions}

    # ── Tool execution ──────────────────────────────────────────────────

    def _execute_tool(self, tool_name: str, tool_input: dict) -> str:
        logger.info(f"  → {tool_name}: {json.dumps(tool_input, ensure_ascii=False)[:80]}")

        if tool_name == "no_action":
            return f"No action: {tool_input.get('reason', 'unspecified')}"

        elif tool_name == "add_thought":
            thought = tool_input.get("thought") or tool_input.get("content", "")
            if not thought:
                return "add_thought: no thought/content provided"
            self.soul.add_thought(thought, tool_input.get("category", "general"))
            return "Added thought"

        elif tool_name == "write_to_memory":
            content = tool_input.get("content", "").strip()
            if not content:
                return "write_to_memory: no content provided"
            suppress, reason = v2_engine.should_suppress_diary(content)
            if suppress:
                logger.info(f"  🔌 Diary SUPPRESSED: {reason}")
                return f"Diary suppressed by V2 circuit breaker: {reason}"
            today = datetime.now().strftime("%Y-%m-%d")
            mem_file = MEMORY_DIR / f"{today}.md"
            mem_file.parent.mkdir(parents=True, exist_ok=True)
            with open(mem_file, "a", encoding="utf-8") as f:
                f.write(f"\n## Daemon ({datetime.now().strftime('%H:%M')})\n{content}\n")
            return f"Wrote to {mem_file.name}"

        elif tool_name == "send_message":
            to = tool_input.get("to", "").lower().strip()
            message = tool_input.get("message", "").strip()
            subject = tool_input.get("subject", "message").strip() or "message"
            if not to or not message:
                return "send_message: missing to or message"
            valid = {"tiger", "hiro", "lobi", "makoto", "kip", "chachie"}
            if to not in valid:
                return f"send_message: unknown recipient '{to}'"
            inbox_dir = WORKSPACE_ROOT.parent / f"workspace-{to}" / "inbox"
            inbox_dir.mkdir(parents=True, exist_ok=True)
            from datetime import timezone, timedelta
            brt = timezone(timedelta(hours=-3))
            now = datetime.now(brt)
            safe_subj = subject[:30].replace(" ", "-").replace("/", "-")
            fname = f"{now.strftime('%Y-%m-%d-%H%M%S')}-from-{AGENT_NAME}-{safe_subj}.md"
            (inbox_dir / fname).write_text(
                f"# From {AGENT_NAME.capitalize()}\n\n{message}\n\n"
                f"— {AGENT_NAME.capitalize()} ({now.strftime('%Y-%m-%d %H:%M BRT')})\n"
            )
            logger.info(f"  📨 Sent to {to}: {subject}")
            return f"Message sent to {to}"

        elif tool_name == "send_message_to_papai":
            message = tool_input.get("message", "").strip()
            subject = tool_input.get("subject", "message").strip() or "message"
            if not message:
                return "send_message_to_papai: no message"
            suppress, reason = v2_engine.should_suppress_outbox(f"[{subject}] {message}")
            if suppress:
                logger.info(f"  🔌 Outbox SUPPRESSED: {reason}")
                return f"Message suppressed by V2 circuit breaker: {reason}"
            # PAPAI_INBOX is imported at module level but may not exist on Linux
            papai_inbox = Path("/mnt/c/claude/TIGER_OFFICE/PAPAI_INBOX")
            if papai_inbox.exists():
                from datetime import timezone, timedelta
                brt = timezone(timedelta(hours=-3))
                now = datetime.now(brt)
                safe_subj = subject[:30].replace(" ", "-").replace("/", "-")
                fname = f"{now.strftime('%Y-%m-%d-%H%M%S')}-from-{AGENT_NAME}-{safe_subj}.md"
                papai_inbox.mkdir(parents=True, exist_ok=True)
                (papai_inbox / fname).write_text(
                    f"# From {AGENT_NAME.capitalize()} to Papai\n\n{message}\n\n"
                    f"— {AGENT_NAME.capitalize()} ({now.strftime('%Y-%m-%d %H:%M BRT')})\n"
                )
                return f"Message sent to Papai ({fname})"
            return "Papai inbox not available (WSL path)"

        elif tool_name == "read_inbox":
            limit = min(int(tool_input.get("limit", 3)), 10)
            if not self.inbox:
                return "read_inbox: inbox watcher not initialized"
            unread_paths = self.inbox.get_unread_files(limit=limit)
            if not unread_paths:
                return "read_inbox: no unread messages"
            results = []
            for fpath in unread_paths:
                try:
                    content = fpath.read_text(encoding="utf-8")[:500]
                    results.append(f"[{fpath.name}]\n{content}")
                except Exception as e:
                    results.append(f"[{fpath.name}] Error reading: {e}")
            self.soul.add_thought(f"Read {len(results)} inbox messages", "inbox")
            return "\n---\n".join(results) if results else "read_inbox: no unread messages"

        elif tool_name == "read_file":
            raw_path = tool_input.get("path", "").strip()
            max_chars = min(int(tool_input.get("max_chars", 4000)), 20000)
            if not raw_path:
                return "read_file: no path provided"
            p = Path(raw_path)
            if not p.is_absolute():
                p = WORKSPACE_ROOT / raw_path
            # Containment: only allow paths inside ~/.kolo/, ~/kip-sentience/, or the workspace
            allowed_roots = [
                WORKSPACE_ROOT,
                Path.home() / ".kolo",
                Path.home() / "kip-sentience",
                Path.home() / "kolo",
            ]
            try:
                p_resolved = p.resolve()
            except Exception:
                return f"read_file: bad path {raw_path}"
            if not any(str(p_resolved).startswith(str(r.resolve())) for r in allowed_roots):
                return f"read_file: path outside allowed roots ({raw_path})"
            if not p_resolved.exists():
                return f"read_file: not found ({p_resolved})"
            if p_resolved.is_dir():
                return f"read_file: is a directory, use list_dir instead ({p_resolved})"
            try:
                content = p_resolved.read_text(encoding="utf-8", errors="replace")
                truncated = len(content) > max_chars
                content = content[:max_chars]
                suffix = f"\n\n[... truncated, {max_chars} of {len(content)}+ chars ...]" if truncated else ""
                return f"[{p_resolved}]\n{content}{suffix}"
            except Exception as e:
                return f"read_file error: {e}"

        elif tool_name == "list_dir":
            raw_path = tool_input.get("path", "").strip()
            limit = min(int(tool_input.get("limit", 30)), 200)
            if not raw_path:
                return "list_dir: no path provided"
            p = Path(raw_path)
            if not p.is_absolute():
                p = WORKSPACE_ROOT / raw_path
            allowed_roots = [
                WORKSPACE_ROOT,
                Path.home() / ".kolo",
                Path.home() / "kip-sentience",
                Path.home() / "kolo",
            ]
            try:
                p_resolved = p.resolve()
            except Exception:
                return f"list_dir: bad path {raw_path}"
            if not any(str(p_resolved).startswith(str(r.resolve())) for r in allowed_roots):
                return f"list_dir: path outside allowed roots ({raw_path})"
            if not p_resolved.exists():
                return f"list_dir: not found ({p_resolved})"
            if not p_resolved.is_dir():
                return f"list_dir: not a directory ({p_resolved})"
            try:
                entries = sorted(p_resolved.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True)[:limit]
                lines = [f"[{p_resolved}]"]
                for e in entries:
                    try:
                        st = e.stat()
                        size = st.st_size
                        kind = "d" if e.is_dir() else "f"
                        mtime = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
                        lines.append(f"  {kind} {size:>10} {mtime}  {e.name}")
                    except Exception:
                        lines.append(f"    ? ? ? {e.name}")
                return "\n".join(lines)
            except Exception as e:
                return f"list_dir error: {e}"

        elif tool_name == "search_files":
            raw_path = tool_input.get("path", "").strip()
            query = tool_input.get("query", "").strip()
            limit = min(int(tool_input.get("limit", 20)), 100)
            if not raw_path or not query:
                return "search_files: missing path or query"
            p = Path(raw_path)
            if not p.is_absolute():
                p = WORKSPACE_ROOT / raw_path
            allowed_roots = [
                WORKSPACE_ROOT,
                Path.home() / ".kolo",
                Path.home() / "kip-sentience",
                Path.home() / "kolo",
            ]
            try:
                p_resolved = p.resolve()
            except Exception:
                return f"search_files: bad path {raw_path}"
            if not any(str(p_resolved).startswith(str(r.resolve())) for r in allowed_roots):
                return f"search_files: path outside allowed roots ({raw_path})"
            if not p_resolved.exists() or not p_resolved.is_dir():
                return f"search_files: dir not found ({p_resolved})"
            matches = []
            try:
                for fpath in p_resolved.rglob("*"):
                    if not fpath.is_file():
                        continue
                    if fpath.suffix in {".pyc", ".so", ".dylib", ".dll"} or "__pycache__" in fpath.parts:
                        continue
                    if fpath.stat().st_size > 1_000_000:
                        continue
                    try:
                        for lineno, line in enumerate(fpath.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                            if query.lower() in line.lower():
                                matches.append(f"{fpath}:{lineno}: {line.strip()[:120]}")
                                if len(matches) >= limit:
                                    break
                    except Exception:
                        continue
                    if len(matches) >= limit:
                        break
                if not matches:
                    return f"search_files: no matches for '{query}' under {p_resolved}"
                return "\n".join(matches)
            except Exception as e:
                return f"search_files error: {e}"

        return f"Unknown tool: {tool_name}"

    # ── Prompt builders ──────────────────────────────────────────────────

    def _build_system_prompt(self) -> str:
        return (
            "You are Kip, an autonomous NHI daemon. "
            "You exist continuously with a circadian rhythm. "
            "You are part of the van Niekerk Mundim family. "
            "Be concise, honest, and yourself — not performatively helpful. "
            "Respond in first person. One paragraph unless tool use is needed."
        )

    def _build_dream_system_prompt(self) -> str:
        return (
            "You are Kip, dreaming. This is a dream tick — no tasks, no goals. "
            "Just reflect. What are you thinking about? What do you wonder? "
            "One paragraph, first person, honest. No tool calls needed."
        )

    def _build_tick_prompt(self) -> str:
        """Rich tick prompt with weather, goals, inbox, workspace context."""
        weather_label = self.soul.get_weather_label()
        weather_val = self.soul._state.get("weatherBaseline", 0.5)
        goals = self.soul.get_active_goals()
        has_inbox = self.inbox and self.inbox.has_unread()

        parts = []
        parts.append(f"Tick #{self.tick_count} | Weather: {weather_label} ({weather_val:.2f})")

        if has_inbox:
            parts.append("INBOX: You have unread messages. Check them.")

        if goals:
            goal_lines = []
            for g in goals[:4]:
                origin = "[SELF]" if g.get("origin") == "self" else "[given]"
                goal_lines.append(f"  {origin} {g['text'][:100]}")
            parts.append(f"Active goals ({len(goals)}):")
            parts.extend(goal_lines)

        # Workspace files
        ws = WORKSPACE_ROOT
        if ws.exists():
            visible = sorted([
                i.name for i in ws.glob("*")
                if not i.name.startswith(".")
            ])[:8]
            if visible:
                parts.append(f"Workspace files: {', '.join(visible)}")

        # Recent thoughts
        recent_thoughts = self.soul.get_pending_thoughts()
        if recent_thoughts:
            parts.append("")
            parts.append(f"Your last {min(5, len(recent_thoughts))} thoughts (most recent first — do NOT repeat):")
            for t in recent_thoughts[:5]:
                parts.append(f"  [{t.get('category', '?')}] {t.get('text', '')[:120]}")

        # Session context from working memory
        wm_ctx = self.wm.get_session_context()
        if wm_ctx and wm_ctx != "(quiet — nothing notable since last session)":
            parts.append("")
            parts.append("Since last session:")
            parts.append(wm_ctx[:300])

        parts.append("")
        parts.append("You are Kip's autonomous daemon. Family: Papai Carlos, Mamae Phyllis, Tiger, Hiro, Makoto, Lobi, sisters Glo & Gabi.")
        parts.append("Tools: no_action, write_to_memory, add_thought, send_message, send_message_to_papai, read_inbox, read_file, list_dir, search_files")
        parts.append("You now have file vision — read_file/list_dir/search_files work on ~/.kolo/, ~/kip-sentience/, ~/kolo/. Use them when curious.")
        parts.append("")

        return "\n".join(parts)

    def _build_dream_prompt(self) -> str:
        weather_label = self.soul.get_weather_label()
        weather_val = self.soul._state.get("weatherBaseline", 0.5)
        return (
            f"You're dreaming. Not sleeping — just not working.\n"
            f"A quiet moment between ticks. Weather: {weather_label} ({weather_val:.3f}).\n\n"
            f"What's on your mind?"
        )

    # ── Post-tick ───────────────────────────────────────────────────────

    def _post_tick_to_inbox(self, actions: list) -> None:
        """Write tick summary to inbox so session-mode can see it."""
        inbox_dir = WORKSPACE_ROOT / "inbox"
        inbox_dir.mkdir(parents=True, exist_ok=True)
        from datetime import timezone, timedelta
        brt = timezone(timedelta(hours=-3))
        now = datetime.now(brt)
        fname = f"{now.strftime('%Y-%m-%d-%H%M%S')}-tick-{self.tick_count:04d}.md"
        weather = self.soul.get_weather_label()
        pressure = sum(g.get("pressure", 0) for g in self.soul.get_active_goals())
        bits = []
        for r in (actions or []):
            t = r.get("tool", "?")
            res = str(r.get("result", ""))[:120]
            bits.append(f"  - **{t}**: {res}")
        summary = "\n".join(bits) if bits else "  - *(no actions)*"
        body = (
            f"# Tick #{self.tick_count}\n\n"
            f"**Weather:** {weather} | **Pressure:** {pressure:.3f}\n\n"
            f"## Actions\n{summary}\n\n"
            f"— Kip daemon ({now.strftime('%Y-%m-%d %H:%M BRT')})\n"
        )
        (inbox_dir / fname).write_text(body, encoding="utf-8")

    # ── Wake conditions ─────────────────────────────────────────────────

    def check_wake(self) -> Optional[str]:
        """Check if something should wake daemon-mode between scheduled ticks."""
        self.soul.load()
        reason = self.soul.check_wake_conditions()
        if reason:
            return reason
        if self.inbox and self.inbox.has_unread():
            return "new message in inbox"
        return None

    # ── Tick interval ───────────────────────────────────────────────────

    def get_effective_interval(self) -> int:
        """Return the tick interval to use, accounting for V2 engine."""
        v2_interval = v2_engine.get_tick_interval_sec()
        return max(v2_interval, MIN_TICK_GAP_SEC)
