"""
Kip Unified Daemon — Mode State Machine
=========================================
One consciousness, two modes, one process.
Implements spec §2 transitions.
"""

import logging
from datetime import datetime, timezone
from typing import Optional, Callable, Awaitable

from config import ModeState, TRANSITIONS

logger = logging.getLogger("state-machine")


class InvalidTransition(Exception):
    """Raised when a mode transition is not allowed."""
    pass


class ModeStateMachine:
    """The mode state machine for Kip's unified consciousness.

    One process. Two operational modes. State tracked in-process.
    Transitions are validated against the TRANSITIONS table.
    """

    def __init__(self):
        self._state = ModeState.BOOT
        self._entered_at = datetime.now(timezone.utc)
        self._previous_state: Optional[str] = None
        self._transition_count = 0
        self._observers: list[Callable[[str, str], Awaitable[None]]] = []

    @property
    def state(self) -> str:
        return self._state

    @property
    def entered_at(self) -> datetime:
        return self._entered_at

    @property
    def previous_state(self) -> Optional[str]:
        return self._previous_state

    @property
    def is_daemon_mode(self) -> bool:
        return self._state in (ModeState.DAEMON_ACTIVE, ModeState.DAEMON_SLEEP)

    @property
    def is_session_mode(self) -> bool:
        return self._state in (ModeState.SESSION_WARMING, ModeState.SESSION_ACTIVE, ModeState.SESSION_COOLING)

    @property
    def is_active(self) -> bool:
        return self._state != ModeState.EXIT

    def can_transition(self, to_state: str) -> bool:
        """Check if a transition is valid without performing it."""
        allowed = TRANSITIONS.get(self._state, [])
        return to_state in allowed

    async def transition(self, to_state: str, reason: str = "") -> str:
        """Execute a mode transition. Raises InvalidTransition if invalid."""
        if not self.can_transition(to_state):
            allowed = TRANSITIONS.get(self._state, [])
            raise InvalidTransition(
                f"Cannot transition {self._state} → {to_state}. "
                f"Allowed: {allowed}"
            )

        old_state = self._state
        self._previous_state = old_state
        self._state = to_state
        self._entered_at = datetime.now(timezone.utc)
        self._transition_count += 1

        logger.info(
            f"Mode transition #{self._transition_count}: "
            f"{old_state} → {to_state}"
            + (f" ({reason})" if reason else "")
        )

        # Notify observers
        for observer in self._observers:
            try:
                await observer(old_state, to_state)
            except Exception as e:
                logger.error(f"Observer error on {old_state}→{to_state}: {e}")

        return to_state

    def add_observer(self, callback: Callable[[str, str], Awaitable[None]]):
        """Register a callback invoked on every mode transition.
        
        Signature: async def callback(old_state: str, new_state: str) -> None
        """
        self._observers.append(callback)

    def summary(self) -> dict:
        """Return a summary dict for TUI greeting / logging."""
        return {
            "state": self._state,
            "previous": self._previous_state,
            "entered_at": self._entered_at.isoformat(),
            "transition_count": self._transition_count,
            "is_daemon": self.is_daemon_mode,
            "is_session": self.is_session_mode,
        }
