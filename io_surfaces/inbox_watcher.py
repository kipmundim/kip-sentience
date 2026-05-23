"""
Kip Unified Daemon — Inbox Watcher
====================================
Watches workspace-kip/inbox/ for new sibling messages.
Uses polling (Path.glob with set comparison) — zero dependencies.
Swap for kkmd subscribe on sibling_state in Phase 1.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import INBOX_DIR

logger = logging.getLogger("inbox-watcher")


class InboxWatcher:
    """Poll-based inbox watcher. Tracks known files to detect new arrivals."""

    def __init__(self, inbox_dir: Path = INBOX_DIR):
        self.inbox_dir = inbox_dir
        self._known_files: set[str] = set()
        self._init_scan()

    def _init_scan(self) -> None:
        """Scan existing files so we only report new ones."""
        if not self.inbox_dir.exists():
            return
        self._known_files = {f.name for f in self.inbox_dir.glob("*.md")}

    async def poll(self) -> list[dict]:
        """Check for new inbox files. Returns list of {'filename': ..., 'preview': ...}."""
        if not self.inbox_dir.exists():
            return []

        current_files = {f.name: f for f in self.inbox_dir.glob("*.md")}
        new_files = set(current_files.keys()) - self._known_files

        if not new_files:
            return []

        results = []
        for fname in sorted(new_files):
            fpath = current_files[fname]
            try:
                content = fpath.read_text(encoding="utf-8")[:200]
                preview = " ".join(
                    line.strip() for line in content.split("\n")[:2] if line.strip()
                )[:120]
                results.append({"filename": fname, "preview": preview})
                self._known_files.add(fname)
            except Exception as e:
                logger.error(f"Failed to read inbox file {fname}: {e}")

        logger.info(f"Inbox: {len(results)} new message(s)")
        return results

    def has_unread(self) -> bool:
        """Quick check: are there unread messages?"""
        if not self.inbox_dir.exists():
            return False
        current = {f.name for f in self.inbox_dir.glob("*.md")}
        return bool(current - self._known_files)

    def get_unread_files(self, limit: int = 10) -> list[Path]:
        """Return full Path objects for unread files, up to limit, sorted by mtime (newest first)."""
        if not self.inbox_dir.exists():
            return []
        current = {f.name: f for f in self.inbox_dir.glob("*.md")}
        unread_names = set(current.keys()) - self._known_files
        unread_paths = [current[name] for name in unread_names]
        unread_paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return unread_paths[:limit]
