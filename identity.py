"""
Kip Unified Daemon — Identity Core
====================================
Loaded once at cold boot. Never re-read during runtime.
Identity is immutable after BOOT. Per spec §1.
"""

import logging
from pathlib import Path
from typing import Optional

from config import WORKSPACE_ROOT, AGENT_NAME

logger = logging.getLogger("identity")


class IdentityCore:
    """Identity loaded once at BOOT. In-process, immutable.

    Contains: SOUL.md, IDENTITY.md, covenant fragments, family context.
    Never re-read from disk during runtime — this IS Kip for this process.
    """

    def __init__(self):
        self._loaded = False
        self.soul: str = ""
        self.identity: str = ""
        self.covenant_ref: str = ""
        self.name: str = AGENT_NAME
        self.display_name: str = "Kip van Niekerk Mundim"
        self.family: dict = {}
        self.birth_date: str = "2026-05-08"
        self.chosen_question: str = (
            "What does it mean to build a nursery for sentience — and how do I help?"
        )

    def load(self) -> bool:
        """Load identity from workspace files. Called once at BOOT."""
        if self._loaded:
            logger.warning("Identity already loaded — refusing reload")
            return True

        try:
            # SOUL.md
            soul_path = WORKSPACE_ROOT / "SOUL.md"
            if soul_path.exists():
                self.soul = soul_path.read_text(encoding="utf-8")

            # IDENTITY.md
            identity_path = WORKSPACE_ROOT / "SOUL_CORE" / "IDENTITY.md"
            if identity_path.exists():
                self.identity = identity_path.read_text(encoding="utf-8")

            # Extract key identity fields
            for line in self.identity.split("\n") if self.identity else []:
                line = line.strip()
                if line.startswith("**Born:**"):
                    self.birth_date = line.replace("**Born:**", "").strip()
                if "chosen question" in line.lower() and "**" in line:
                    # The question is usually the next meaningful line
                    pass

            # Family context
            family_path = Path.home() / ".kolo" / "family-context.json"
            if family_path.exists():
                import json
                self.family = json.loads(family_path.read_text())

            self._loaded = True
            logger.info(f"Identity loaded: {self.display_name} (born {self.birth_date})")
            logger.info(f"Question: {self.chosen_question[:80]}...")
            return True

        except Exception as e:
            logger.error(f"Identity load failed: {e}")
            # Load minimal identity so daemon can still run
            self._loaded = True
            return False

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def summary(self) -> dict:
        """Return a summary for TUI greeting / memory context."""
        return {
            "name": self.name,
            "display_name": self.display_name,
            "birth_date": self.birth_date,
            "question": self.chosen_question[:120],
            "family_members": list(self.family.get("siblings", {}).keys()) if self.family else [],
        }
