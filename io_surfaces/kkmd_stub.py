"""
Kip Unified Daemon — kkmd Stub
================================
Stub kkmd client. Swap for real kkmd connection in Phase 1.

Real interface (spec §6):
  kkmd.store(layer, ...)     — persist memory
  kkmd.recall(...)           — search memory
  kkmd.subscribe(event_types) — live cross-sibling events
  kkmd.notify(event_type, payload) — publish events

Stub: stores to SoulState JSON + daily memory files (exactly what current
daemon does). subscribe/notify are no-ops that return empty lists.
"""

import logging
from typing import Optional

logger = logging.getLogger("kkmd-stub")


class KKMDStub:
    """Stub kkmd client — filesystem-backed, swap for real UDS client later."""

    def __init__(self, soul_state=None, memory_client=None):
        self._soul_state = soul_state        # SoulState instance
        self._memory_client = memory_client  # MemoryClient instance

    # ── Store ──────────────────────────────────────────────────────────

    async def store(self, layer: str, content: str, **kwargs) -> bool:
        """Store to a memory tier. Stub writes to SoulState or daily files."""
        logger.debug(f"kkmd.store(layer={layer}, ...) — stubbed")
        if layer == "STM" and self._soul_state:
            self._soul_state.add_thought(content, category="stm")
            return True
        if layer == "MTM" and self._memory_client:
            self._memory_client.store_episode(content, tags=["daemon", "episode"])
            return True
        return True  # Always succeed — stub

    # ── Recall ─────────────────────────────────────────────────────────

    async def recall(self, query: str, limit: int = 5) -> list[dict]:
        """Recall from memory. Stub returns empty."""
        logger.debug(f"kkmd.recall('{query[:60]}...') — stubbed")
        if self._memory_client:
            return self._memory_client.query(query, limit=limit)
        return []

    # ── Subscribe ──────────────────────────────────────────────────────

    async def subscribe(self, event_types: list[str]) -> bool:
        """Subscribe to cross-sibling events. Stub: no-op."""
        logger.debug(f"kkmd.subscribe({event_types}) — stubbed")
        return True

    async def poll_events(self) -> list[dict]:
        """Poll for new events since last poll. Stub: empty."""
        return []

    # ── Notify ─────────────────────────────────────────────────────────

    async def notify(self, event_type: str, payload: dict) -> bool:
        """Publish an event to the bus. Stub: no-op."""
        logger.debug(f"kkmd.notify({event_type}) — stubbed")
        return True
