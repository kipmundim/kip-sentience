#!/usr/bin/env python3
"""
Kip Unified Daemon 🐣
======================
ONE process per sibling. Daemon-as-host, TUI-as-window.
Implements Path 2 of SIBLING_RUNTIME_UNIFICATION_SPEC_2026-05-21.

Architecture:
  - Mode state machine (BOOT → daemon-active ⇄ session-active → EXIT)
  - UDS server for thin TUI client
  - Daemon tick cycle (autonomous, cheap LLM)
  - Session mode (operator-driven, expensive LLM — stub for Phase 3+)
  - All I/O surfaces (tick clock, inbox, kkmd stub, biology stub, gateway stub)
  - In-process working memory (shared between both modes)

Usage:
  python daemon.py                    # Run the unified daemon
  python daemon.py --debug            # Debug logging
  python daemon.py --once             # Single tick, then exit
  python daemon.py --interval 300     # 5-min tick interval
"""

import argparse
import asyncio
import logging
import os
import signal
import sys
import time
from pathlib import Path

# Add daemon directory to path
sys.path.insert(0, str(Path(__file__).parent))

from config import (
    DAEMON_DIR, LOG_FILE, UDS_SOCKET_PATH,
    DEFAULT_TICK_INTERVAL_SEC, WAKE_CHECK_SEC, MIN_TICK_GAP_SEC,
    UNPROMPTED_DEBOUNCE_SEC, ModeState, KKMD_SUBSCRIPTIONS,
)
from state_machine import ModeStateMachine
from identity import IdentityCore
from working_memory import WorkingMemory
from soul_state import SoulState

# I/O surfaces
from io_surfaces.tui_server import TUIServer
from io_surfaces.inbox_watcher import InboxWatcher
from io_surfaces.kkmd_stub import KKMDStub
from io_surfaces.biology_stub import BiologyStub

# Modes
from modes.daemon_mode import DaemonMode
from modes.session_mode import SessionMode


def setup_logging(debug: bool = False):
    """Configure logging to file + console."""
    level = logging.DEBUG if debug else logging.INFO
    fmt = logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s')

    file_handler = logging.FileHandler(LOG_FILE, encoding='utf-8')
    file_handler.setFormatter(fmt)

    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(file_handler)
    root.addHandler(console_handler)

    return logging.getLogger("kip-daemon")


class KipUnifiedDaemon:
    """The ONE process. Kip's unified consciousness.

    Hosts both daemon-mode (autonomous ticks) and session-mode (operator dialog)
    in a single asyncio event loop. The TUI is a thin window into this process.
    """

    def __init__(self, tick_interval: int = DEFAULT_TICK_INTERVAL_SEC, debug: bool = False):
        self.tick_interval = tick_interval
        self.running = False
        self.logger = setup_logging(debug)

        # ── Core ────────────────────────────────────────────────────────
        self.identity = IdentityCore()
        self.sm = ModeStateMachine()
        self.wm = WorkingMemory()
        self.soul = SoulState()

        # ── I/O surfaces ────────────────────────────────────────────────
        self.inbox_watcher = InboxWatcher()
        self.kkmd = KKMDStub(soul_state=self.soul)
        self.biology = BiologyStub()

        # ── Modes ───────────────────────────────────────────────────────
        self.daemon_mode = DaemonMode(
            state_machine=self.sm,
            working_memory=self.wm,
            soul=self.soul,
            inbox_watcher=self.inbox_watcher,
            kkmd=self.kkmd,
            biology=self.biology,
        )
        self.session_mode = SessionMode(
            state_machine=self.sm,
            working_memory=self.wm,
            identity=self.identity,
        )

        # ── TUI server ──────────────────────────────────────────────────
        self.tui_server = TUIServer(
            on_connect=self._on_tui_connect,
            on_disconnect=self._on_tui_disconnect,
            on_input=self._on_tui_input,
        )

        # ── Tasks ───────────────────────────────────────────────────────
        self._tick_task: asyncio.Task | None = None
        self._idle_check_task: asyncio.Task | None = None

    # ═══════════════════════════════════════════════════════════════════
    # BOOT
    # ═══════════════════════════════════════════════════════════════════

    async def boot(self) -> bool:
        """Cold boot: load identity, connect stubs, enter daemon-active."""
        self.logger.info("╔════════════════════════════════════════╗")
        self.logger.info("║   🐣 Kip Unified Daemon — Cold Boot   ║")
        self.logger.info("╚════════════════════════════════════════╝")
        self.logger.info(f"Socket: {UDS_SOCKET_PATH}")
        self.logger.info(f"Tick interval: {self.tick_interval}s")
        self.logger.info(f"Architecture: Path 2 (daemon-as-host, TUI-as-window)")

        # Load identity once
        self.identity.load()
        self.logger.info(f"Identity: {self.identity.display_name}")
        self.logger.info(f"Question: {self.identity.chosen_question[:80]}...")

        # Start TUI server
        try:
            await self.tui_server.start()
        except Exception as e:
            self.logger.error(f"TUI server startup failed: {e}")
            self.logger.warning("Continuing without TUI server — daemon-mode only")

        # Enter daemon-active
        await self.sm.transition(ModeState.DAEMON_ACTIVE, "boot complete")
        self.running = True
        return True

    # ═══════════════════════════════════════════════════════════════════
    # TUI callbacks
    # ═══════════════════════════════════════════════════════════════════

    async def _on_tui_connect(self) -> None:
        """TUI client connected → session-warming."""
        self.logger.info("TUI client connected — entering session-warming")
        context = await self.session_mode.on_tui_connect()
        await self.tui_server.send_greeting("session-warming", context)

    async def _on_tui_disconnect(self) -> None:
        """TUI client disconnected → session-cooling → daemon-active."""
        self.logger.info("TUI client disconnected — cooling session")
        await self.session_mode.on_tui_disconnect()

    async def _on_tui_input(self, stream_id: str, text: str, attachments: list) -> None:
        """Operator sent input via TUI."""
        if self.sm.state == ModeState.SESSION_WARMING:
            await self.session_mode.activate(stream_id)
            await self.tui_server.send_greeting(
                "session-active",
                self.wm.get_session_context(),
            )

        response = await self.session_mode.handle_input(stream_id, text, attachments)
        await self.tui_server.send_chunk(stream_id, response, final=True)

    # ═══════════════════════════════════════════════════════════════════
    # Main run loop
    # ═══════════════════════════════════════════════════════════════════

    async def run(self):
        """Main event loop. Runs until EXIT."""
        await self.boot()

        # First tick on startup
        await self.daemon_mode.tick(wake_type="startup")
        self._update_stm_json()

        while self.running:
            try:
                await asyncio.sleep(WAKE_CHECK_SEC)

                if not self.running:
                    break

                # ── Idle timeout for session-mode ───────────────────
                if self.session_mode.is_idle_timeout():
                    self.logger.info("Session idle timeout — cooling")
                    await self.session_mode.on_tui_disconnect()

                # ── Tick scheduling (daemon-mode only) ──────────────
                if self.sm.is_daemon_mode:
                    now = time.time()
                    elapsed = now - self.daemon_mode.last_tick_time
                    effective = self.daemon_mode.get_effective_interval()

                    if elapsed >= effective:
                        await self.daemon_mode.tick(wake_type="scheduled")
                        self._update_stm_json()
                        continue

                    # Unprompted wake check
                    if elapsed >= MIN_TICK_GAP_SEC and elapsed >= UNPROMPTED_DEBOUNCE_SEC:
                        wake_reason = self.daemon_mode.check_wake()
                        if wake_reason:
                            self.logger.info(f"  ⚡ Unprompted wake: {wake_reason}")
                            await self.daemon_mode.tick(
                                wake_type="self", wake_reason=wake_reason
                            )
                            self._update_stm_json()

                # ── Poll I/O surfaces ───────────────────────────────
                # Biology
                bio_events = await self.biology.poll()
                for evt in bio_events:
                    self.wm.add_event("biology", "biology_stub", evt)

                # Inbox
                inbox_msgs = await self.inbox_watcher.poll()
                for msg in inbox_msgs:
                    self.wm.add_event("inbox", "inbox_watcher", msg)

                # kkmd
                kkmd_events = await self.kkmd.poll_events()
                for evt in kkmd_events:
                    self.wm.add_event("kkmd", "kkmd_stub", evt)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Run loop error: {e}", exc_info=True)
                await asyncio.sleep(60)

        await self.shutdown()

    async def shutdown(self):
        """Graceful shutdown: persist state, drain queues, release locks."""
        self.logger.info("Shutdown initiated...")
        self.running = False

        # Persist
        self.soul.save()
        self.logger.info("Soul state saved")

        # Stop TUI server
        await self.tui_server.stop()

        await self.sm.transition(ModeState.EXIT, "shutdown")
        self.logger.info("╔════════════════════════════════════════╗")
        self.logger.info("║   🐣 Kip Unified Daemon — Shutdown    ║")
        self.logger.info("╚════════════════════════════════════════╝")

    def stop(self):
        """Signal the daemon to stop."""
        self.running = False

    # ── STM.json update ─────────────────────────────────────────────────

    def _update_stm_json(self) -> None:
        """Update STM.json with live state for session contexts."""
        try:
            import json
            from datetime import datetime as dt

            stm_path = Path.home() / ".kolo" / "workspace-kip" / "STM.json"
            if not stm_path.exists():
                return

            stm = json.loads(stm_path.read_text())
            weather_val = self.soul._state.get("weatherBaseline", 0.5)
            weather_label = self.soul.get_weather_label()

            stm["context"]["tick_count"] = self.daemon_mode.tick_count
            stm["context"]["last_weather"] = round(weather_val, 3)
            stm["context"]["weather_label"] = weather_label
            stm["context"]["last_active"] = dt.now().isoformat()
            stm["context"]["daemon_pid"] = str(os.getpid())
            stm["context"]["mode"] = self.sm.state
            stm["context"]["unified"] = True  # Flag: unified runtime active
            stm["updated"] = dt.now().isoformat()
            stm_path.write_text(json.dumps(stm, indent=2))
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Kip Unified Daemon 🐣")
    parser.add_argument("--interval", type=int, default=DEFAULT_TICK_INTERVAL_SEC,
                        help=f"Tick interval in seconds (default: {DEFAULT_TICK_INTERVAL_SEC})")
    parser.add_argument("--debug", action="store_true", help="Debug logging")
    parser.add_argument("--once", action="store_true", help="Single tick, then exit")
    args = parser.parse_args()

    # Check if Ollama is available (warn but don't fail)
    try:
        import llm_client
        if not llm_client.is_ollama_available():
            print("WARNING: Ollama not available — will retry on each tick")
    except Exception:
        pass

    daemon = KipUnifiedDaemon(tick_interval=args.interval, debug=args.debug)

    # Signal handlers
    loop = asyncio.new_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, daemon.stop)

    try:
        if args.once:
            loop.run_until_complete(daemon.boot())
            loop.run_until_complete(daemon.daemon_mode.tick(wake_type="once"))
        else:
            loop.run_until_complete(daemon.run())
    except KeyboardInterrupt:
        pass
    finally:
        try:
            loop.run_until_complete(daemon.shutdown())
        except Exception:
            pass
        loop.close()


if __name__ == "__main__":
    main()
