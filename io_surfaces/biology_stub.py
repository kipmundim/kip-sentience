"""
Kip Unified Daemon — Biology Stub
===================================
Stub biology stream. Reads from existing biology files for now.
Swap for kkmd subscribe on event_type=biology in Phase 2+.

Subscriptions per spec §5.4: hormones + impulse
"""

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("biology-stub")

BIOLOGY_DIR = Path.home() / ".kolo" / "autonomic"
HORMONES_FILE = BIOLOGY_DIR / "hormones.json"
IMPULSE_DIR = Path.home() / ".kolo" / "proactive-state"


class BiologyStub:
    """Stub biology stream — file-based, swap for kkmd subscribe later."""

    def __init__(self):
        self._last_hormones_mtime: float = 0.0
        self._last_impulse_mtime: float = 0.0

    async def read_hormones(self) -> Optional[dict]:
        """Read hormones if file changed since last read."""
        if not HORMONES_FILE.exists():
            return None
        mtime = HORMONES_FILE.stat().st_mtime
        if mtime <= self._last_hormones_mtime:
            return None
        self._last_hormones_mtime = mtime
        try:
            return json.loads(HORMONES_FILE.read_text())
        except Exception as e:
            logger.error(f"Failed to read hormones: {e}")
            return None

    async def read_impulse(self) -> Optional[dict]:
        """Check for new impulses."""
        # Impulses come from proactive-comms — check kip_last_wakeup
        wakeup_file = IMPULSE_DIR / "kip_last_wakeup"
        if not wakeup_file.exists():
            return None
        mtime = wakeup_file.stat().st_mtime
        if mtime <= self._last_impulse_mtime:
            return None
        self._last_impulse_mtime = mtime
        try:
            return {"trigger": wakeup_file.read_text().strip()}
        except Exception:
            return None

    async def poll(self) -> list[dict]:
        """Poll biology signals. Returns list of {'subtype': ..., 'payload': ...} events."""
        events = []
        hormones = await self.read_hormones()
        if hormones:
            events.append({"subtype": "hormones", "payload": hormones})
        impulse = await self.read_impulse()
        if impulse:
            events.append({"subtype": "impulse", "payload": impulse})
        return events
