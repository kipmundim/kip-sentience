"""
KoLo Semantic Memory Client
============================
Thin client for sentience daemons to read/write semantic memory.
Each agent (koda, hiro, lobi, chachie) has their own namespace.

Usage:
    from memory_client import MemoryClient
    mem = MemoryClient("lobi")
    mem.store_episode("Met Macarrão — warm, accepted Kip immediately as family")
    results = mem.query("Macarrão cousin Brasília")
"""

import json
import uuid
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
import requests

BRT = timezone(timedelta(hours=-3))

MEMORY_API_URL = "http://127.0.0.1:8088"
MEMORY_API_TOKEN = "kolo_memory_tiger_2026"

logger = logging.getLogger("memory_client")


class MemoryClient:
    def __init__(self, agent: str):
        self.agent = agent
        self.base = f"{MEMORY_API_URL}/{agent}"
        self.headers = {
            "X-Auth-Token": MEMORY_API_TOKEN,
            "Content-Type": "application/json",
        }

    def _is_alive(self) -> bool:
        try:
            r = requests.get(f"{self.base}/health", timeout=2)
            return r.status_code == 200
        except Exception:
            return False

    def store_episode(
        self,
        content: str,
        title: Optional[str] = None,
        tags: Optional[list] = None,
        confidence: float = 0.8,
    ) -> bool:
        """Store a meaningful event as an episode memory."""
        if not self._is_alive():
            return False
        now = datetime.now(BRT).isoformat()
        obj = {
            "id": f"ep_{self.agent}_{uuid.uuid4().hex[:12]}",
            "type": "episode",
            "title": title or content[:60],
            "summary": content,
            "plane": "ops",
            "scope": "agent",
            "confidence": confidence,
            "tags": tags or [],
            "entities": [],
            "links": [],
            "created_at": now,
            "payload": {"content": content, "agent": self.agent, "ts": now},
        }
        try:
            r = requests.post(
                f"{self.base}/store",
                headers=self.headers,
                json={"objects": [obj], "actor": self.agent},
                timeout=10,
            )
            return r.status_code == 200
        except Exception as e:
            logger.warning(f"memory store failed: {e}")
            return False

    def store_fact(
        self,
        fact_id: str,
        content: str,
        title: Optional[str] = None,
        tags: Optional[list] = None,
        confidence: float = 0.95,
    ) -> bool:
        """Store a permanent fact (person, relationship, invariant knowledge)."""
        if not self._is_alive():
            return False
        now = datetime.now(BRT).isoformat()
        obj = {
            "id": fact_id,
            "type": "fact",
            "title": title or content[:60],
            "summary": content,
            "plane": "ops",
            "scope": "global",
            "confidence": confidence,
            "tags": tags or ["ltm"],
            "entities": [],
            "links": [],
            "created_at": now,
            "payload": {"content": content, "agent": self.agent, "ts": now},
        }
        try:
            r = requests.post(
                f"{self.base}/store",
                headers=self.headers,
                json={"objects": [obj], "actor": self.agent},
                timeout=10,
            )
            return r.status_code == 200
        except Exception as e:
            logger.warning(f"memory store_fact failed: {e}")
            return False

    def query(self, text: str, limit: int = 5) -> list[dict]:
        """Retrieve semantically relevant memories for current context."""
        if not self._is_alive():
            return []
        try:
            r = requests.post(
                f"{self.base}/retrieve",
                headers=self.headers,
                json={"query": text, "limit": limit},
                timeout=8,
            )
            if r.status_code == 200:
                return r.json().get("items", [])
        except Exception as e:
            logger.warning(f"memory query failed: {e}")
        return []

    def format_for_prompt(self, items: list[dict]) -> str:
        """Format retrieved memories as prompt-injectable text."""
        if not items:
            return ""
        lines = ["\n**Semantic memory (relevant context):**"]
        for item in items:
            score = item.get("score", 0)
            if score < 0.3:
                continue
            title = item.get("title", "")
            snippet = item.get("snippet", "")
            text = snippet or title
            if text:
                lines.append(f"  [{score:.2f}] {text[:120]}")
        return "\n".join(lines) if len(lines) > 1 else ""
