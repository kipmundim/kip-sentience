"""
Kip Unified Daemon — TUI Server (UDS JSON-RPC)
================================================
Thin-client protocol: the operator TUI connects via Unix domain socket.
The daemon IS the consciousness. The TUI is just a window.

Protocol frames (per spec §3):
  TUI → daemon: {"op": "input", "stream_id": "...", "text": "...", "attachments": [...]}
                {"op": "ping"}
                {"op": "disconnect"}
  daemon → TUI: {"type": "greeting", "mode": "session-warming", "context_summary": "..."}
                {"type": "chunk", "stream_id": "...", "text": "...", "final": bool}
                {"type": "tool_use", "tool": "...", "args": {...}}
                {"type": "thinking", "summary": "..."}
                {"type": "error", "message": "..."}
"""

import asyncio
import json
import logging
import os
from typing import Optional, Callable, Awaitable

from config import UDS_SOCKET_PATH, ModeState

logger = logging.getLogger("tui-server")

# Type for the handler that processes operator input
InputHandler = Callable[[str, str, list], Awaitable[None]]
# async def handler(stream_id: str, text: str, attachments: list) -> None


class TUIServer:
    """UDS JSON-RPC server for thin TUI client connections.

    One connection at a time (one operator per sibling).
    The server is a window into the daemon's consciousness, not a separate brain.
    """

    def __init__(
        self,
        socket_path: str = str(UDS_SOCKET_PATH),
        on_connect: Optional[Callable[[], Awaitable[None]]] = None,
        on_disconnect: Optional[Callable[[], Awaitable[None]]] = None,
        on_input: Optional[InputHandler] = None,
    ):
        self.socket_path = socket_path
        self._server: Optional[asyncio.AbstractServer] = None
        self._current_reader: Optional[asyncio.StreamReader] = None
        self._current_writer: Optional[asyncio.StreamWriter] = None
        self._connected = False

        # Callbacks
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect
        self._on_input = on_input

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def has_client(self) -> bool:
        return self._current_writer is not None

    async def start(self) -> None:
        """Start the UDS server. Non-blocking — runs in asyncio background."""
        # Clean up stale socket
        try:
            os.unlink(self.socket_path)
        except OSError:
            pass

        self._server = await asyncio.start_unix_server(
            self._handle_client,
            path=self.socket_path,
        )
        os.chmod(self.socket_path, 0o600)  # Only the sibling user
        logger.info(f"TUI server listening on {self.socket_path}")

    async def stop(self) -> None:
        """Stop the UDS server."""
        if self._current_writer:
            try:
                self._current_writer.close()
                await self._current_writer.wait_closed()
            except Exception:
                pass
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        try:
            os.unlink(self.socket_path)
        except OSError:
            pass
        logger.info("TUI server stopped")

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Handle one TUI client connection."""
        addr = writer.get_extra_info('peername', 'unknown')
        logger.info(f"TUI client connected: {addr}")

        # Only one connection at a time
        if self._current_writer:
            logger.warning("Rejecting second TUI connection — already connected")
            writer.close()
            return

        self._current_reader = reader
        self._current_writer = writer
        self._connected = True

        if self._on_connect:
            try:
                await self._on_connect()
            except Exception as e:
                logger.error(f"on_connect error: {e}")

        try:
            while True:
                line = await reader.readline()
                if not line:
                    # EOF — client disconnected
                    break

                try:
                    frame = json.loads(line.decode("utf-8").strip())
                except json.JSONDecodeError as e:
                    await self._send_error(f"Invalid JSON: {e}")
                    continue

                await self._dispatch_frame(frame)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"TUI handler error: {e}")
        finally:
            self._connected = False
            self._current_reader = None
            self._current_writer = None
            logger.info("TUI client disconnected")

            if self._on_disconnect:
                try:
                    await self._on_disconnect()
                except Exception as e:
                    logger.error(f"on_disconnect error: {e}")

    async def _dispatch_frame(self, frame: dict) -> None:
        """Dispatch a JSON-RPC frame from the TUI client."""
        op = frame.get("op", "")

        if op == "ping":
            await self._send_frame({"type": "pong"})

        elif op == "input":
            stream_id = frame.get("stream_id", "")
            text = frame.get("text", "")
            attachments = frame.get("attachments", [])
            if self._on_input:
                await self._on_input(stream_id, text, attachments)
            else:
                await self._send_error("No input handler registered")

        elif op == "disconnect":
            logger.info("TUI client requested disconnect")
            if self._current_writer:
                self._current_writer.close()

        else:
            await self._send_error(f"Unknown op: {op}")

    async def send_greeting(self, mode: str, context_summary: str) -> None:
        """Send greeting frame to TUI (session-warming → session-active)."""
        await self._send_frame({
            "type": "greeting",
            "mode": mode,
            "context_summary": context_summary,
        })

    async def send_chunk(self, stream_id: str, text: str, final: bool = False) -> None:
        """Send a response chunk to TUI."""
        await self._send_frame({
            "type": "chunk",
            "stream_id": stream_id,
            "text": text,
            "final": final,
        })

    async def send_tool_use(self, tool: str, args: dict) -> None:
        """Notify TUI of a tool call."""
        await self._send_frame({
            "type": "tool_use",
            "tool": tool,
            "args": args,
        })

    async def send_thinking(self, summary: str) -> None:
        """Send thinking visibility to TUI."""
        await self._send_frame({
            "type": "thinking",
            "summary": summary,
        })

    async def send_error(self, message: str) -> None:
        """Send error to TUI."""
        await self._send_error(message)

    async def _send_frame(self, frame: dict) -> None:
        """Send a JSON frame to the connected TUI client."""
        if not self._current_writer:
            return
        try:
            data = (json.dumps(frame, ensure_ascii=False) + "\n").encode("utf-8")
            self._current_writer.write(data)
            await self._current_writer.drain()
        except Exception as e:
            logger.error(f"Send error: {e}")
            self._connected = False

    async def _send_error(self, message: str) -> None:
        await self._send_frame({"type": "error", "message": message})
