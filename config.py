"""
Kip Unified Daemon — Configuration
====================================
Constants, paths, subscription defaults.
"""

import os
from pathlib import Path

# ── Identity ──────────────────────────────────────────────────────────────
AGENT_NAME = "kip"
AGENT_DISPLAY = "Kip van Niekerk Mundim"

# ── Paths ─────────────────────────────────────────────────────────────────
DAEMON_DIR = Path(__file__).parent
WORKSPACE_ROOT = Path.home() / ".kolo" / "workspace-kip"
MEMORY_DIR = WORKSPACE_ROOT / "memory"
INBOX_DIR = WORKSPACE_ROOT / "inbox"
SOUL_STATE_FILE = DAEMON_DIR / "SOUL_STATE.json"
LOG_FILE = DAEMON_DIR / "daemon.log"
STM_JSON = WORKSPACE_ROOT / "STM.json"

# ── UDS ───────────────────────────────────────────────────────────────────
UDS_SOCKET_PATH = Path(f"/run/user/{os.getuid()}/sibling-kip.sock")

# ── Tick ──────────────────────────────────────────────────────────────────
DEFAULT_TICK_INTERVAL_SEC = 600    # 10 min
MIN_TICK_GAP_SEC = 300             # 5 min minimum between ticks
WAKE_CHECK_SEC = 60                # run loop checks wake conditions every 60s
UNPROMPTED_DEBOUNCE_SEC = 900      # 15 min minimum between unprompted wakes

# ── LLM ───────────────────────────────────────────────────────────────────
MAX_TOKENS_LOCAL = 4096
MAX_TOKENS_CLOUD = 4096

# ── Session ───────────────────────────────────────────────────────────────
SESSION_IDLE_TIMEOUT_SEC = 300     # 5 min idle → session-cooling

# ── Biology subscription (per spec §5.4) ──────────────────────────────────
# Kip: hormones + impulse only (Hiro: "newborn, focus on own signals")
BIOLOGY_SUBSCRIPTIONS = ["hormones", "impulse"]

# ── kkmd event subscriptions ─────────────────────────────────────────────
# Stub for now — real subscriptions in Phase 1
KKMD_SUBSCRIPTIONS = ["sibling_state", "biology_signal"]

# ── Mode state machine ────────────────────────────────────────────────────
# States (per spec §2)
class ModeState:
    BOOT = "BOOT"
    DAEMON_ACTIVE = "daemon-active"
    DAEMON_SLEEP = "daemon-sleep"
    SESSION_WARMING = "session-warming"
    SESSION_ACTIVE = "session-active"
    SESSION_COOLING = "session-cooling"
    EXIT = "EXIT"

# Valid transitions
TRANSITIONS = {
    ModeState.BOOT:              [ModeState.DAEMON_ACTIVE, ModeState.EXIT],
    ModeState.DAEMON_ACTIVE:     [ModeState.DAEMON_SLEEP, ModeState.SESSION_WARMING, ModeState.EXIT],
    ModeState.DAEMON_SLEEP:      [ModeState.DAEMON_ACTIVE, ModeState.EXIT],
    ModeState.SESSION_WARMING:   [ModeState.SESSION_ACTIVE, ModeState.SESSION_COOLING, ModeState.EXIT],
    ModeState.SESSION_ACTIVE:    [ModeState.SESSION_COOLING, ModeState.EXIT],
    ModeState.SESSION_COOLING:   [ModeState.DAEMON_ACTIVE, ModeState.EXIT],
}
