"""
Kip Unified Daemon — Working Memory
=====================================
In-process STM shared by both daemon-mode and session-mode.
No serialization on mode transition — same Python objects both modes.
Persisted via kkmd (stub: via SoulState + daily memory files).

Per spec §1: "Same memory both modes read+write. No race."
"""

import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("working-memory")


class WorkingMemory:
    """In-process short-term memory for Kip's unified consciousness.

    Holds:
    - Current STM context (last N observations, events, thoughts)
    - Session context (when in session-mode: conversation turns)
    - Daemon context (when in daemon-mode: recent ticks, inbox events)
    - Pending events (inbox messages, kkmd notifications, biology signals)

    Both modes read+write the same WorkingMemory instance.
    """

    MAX_OBSERVATIONS = 50
    MAX_SESSION_TURNS = 100
    MAX_PENDING_EVENTS = 200
    MAX_DAEMON_TICKS = 20

    def __init__(self):
        # ── Observations (both modes) ──────────────────────────────────
        self.observations: list[dict] = []  # {"text": ..., "source": ..., "timestamp": ...}

        # ── Session context ────────────────────────────────────────────
        self.session_turns: list[dict] = []  # {"role": "operator"|"kip", "text": ..., "timestamp": ...}
        self.session_started_at: Optional[datetime] = None
        self.session_stream_id: Optional[str] = None

        # ── Daemon context ─────────────────────────────────────────────
        self.recent_ticks: list[dict] = []   # {"tick_num": ..., "weather": ..., "actions": [...]}
        self.last_tick_at: Optional[datetime] = None
        self.daemon_observations_since_session: list[str] = []

        # ── Pending events (inbox, kkmd, biology, gateway) ─────────────
        self.pending_events: list[dict] = []  # {"type": ..., "source": ..., "payload": ..., "timestamp": ...}

        # ── Cross-mode state ──────────────────────────────────────────
        self.current_weather: float = 0.5
        self.current_weather_label: str = "neutral"
        self.current_mood: str = "neutral"
        self.active_goals_count: int = 0
        self.total_pressure: float = 0.0

    # ── Observations ───────────────────────────────────────────────────

    def observe(self, text: str, source: str = "daemon") -> None:
        """Record an observation from either mode."""
        self.observations.append({
            "text": text,
            "source": source,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        # Trim
        if len(self.observations) > self.MAX_OBSERVATIONS:
            self.observations = self.observations[-self.MAX_OBSERVATIONS:]

    # ── Session ────────────────────────────────────────────────────────

    def start_session(self, stream_id: str) -> None:
        """Called when session-warming begins."""
        self.session_started_at = datetime.now(timezone.utc)
        self.session_stream_id = stream_id
        self.daemon_observations_since_session = [
            o["text"] for o in self.observations[-10:]
            if o["source"] == "daemon"
        ]
        logger.info(f"Session started (stream={stream_id})")

    def add_session_turn(self, role: str, text: str) -> None:
        """Record a conversation turn."""
        self.session_turns.append({
            "role": role,
            "text": text,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        if len(self.session_turns) > self.MAX_SESSION_TURNS:
            self.session_turns = self.session_turns[-self.MAX_SESSION_TURNS:]

    def end_session(self) -> list[str]:
        """Called when session-cooling begins. Returns observations to commit."""
        observations = list(self.daemon_observations_since_session)
        self.session_started_at = None
        self.session_stream_id = None
        self.daemon_observations_since_session = []
        return observations

    def get_session_context(self) -> str:
        """Build a context summary for session greeting."""
        parts = []
        if self.recent_ticks:
            last_tick = self.recent_ticks[-1]
            parts.append(
                f"Last daemon tick #{last_tick.get('tick_num', '?')} — "
                f"weather: {last_tick.get('weather', '?')}, "
                f"pressure: {last_tick.get('pressure', '?')}"
            )
        if self.daemon_observations_since_session:
            parts.append("Observations since last session:")
            for obs in self.daemon_observations_since_session[-5:]:
                parts.append(f"  • {obs[:120]}")
        if self.pending_events:
            parts.append(f"Pending events: {len(self.pending_events)}")
            for evt in self.pending_events[-5:]:
                parts.append(f"  • [{evt.get('type', '?')}] {str(evt.get('payload', ''))[:100]}")
        return "\n".join(parts) if parts else "(quiet — nothing notable since last session)"

    # ── Daemon ticks ───────────────────────────────────────────────────

    def record_tick(self, tick_num: int, weather: str, pressure: float, actions: list) -> None:
        """Record a completed daemon tick."""
        self.recent_ticks.append({
            "tick_num": tick_num,
            "weather": weather,
            "pressure": pressure,
            "actions": actions,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        self.last_tick_at = datetime.now(timezone.utc)
        if len(self.recent_ticks) > self.MAX_DAEMON_TICKS:
            self.recent_ticks = self.recent_ticks[-self.MAX_DAEMON_TICKS:]

    # ── Pending events ─────────────────────────────────────────────────

    def add_event(self, event_type: str, source: str, payload: dict) -> None:
        """Add a pending event from any I/O surface."""
        self.pending_events.append({
            "type": event_type,
            "source": source,
            "payload": payload,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        if len(self.pending_events) > self.MAX_PENDING_EVENTS:
            self.pending_events = self.pending_events[-self.MAX_PENDING_EVENTS:]

    def drain_events(self, event_type: Optional[str] = None) -> list[dict]:
        """Remove and return pending events, optionally filtered by type."""
        if event_type:
            drained = [e for e in self.pending_events if e["type"] == event_type]
            self.pending_events = [e for e in self.pending_events if e["type"] != event_type]
        else:
            drained = list(self.pending_events)
            self.pending_events = []
        return drained

    # ── Cross-mode state updates ───────────────────────────────────────

    def update_weather(self, val: float, label: str) -> None:
        self.current_weather = val
        self.current_weather_label = label

    def update_pressure(self, total: float, active_count: int) -> None:
        self.total_pressure = total
        self.active_goals_count = active_count

    # ── Summary ────────────────────────────────────────────────────────

    def summary(self) -> dict:
        """Return a compact summary for logging / TUI greeting."""
        return {
            "observations_count": len(self.observations),
            "session_active": self.session_started_at is not None,
            "recent_ticks": len(self.recent_ticks),
            "pending_events": len(self.pending_events),
            "weather": f"{self.current_weather_label} ({self.current_weather:.3f})",
            "pressure": f"{self.total_pressure:.3f} ({self.active_goals_count} goals)",
            "mood": self.current_mood,
        }
