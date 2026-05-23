"""
Kip Memory Client — Supabase LTM Backend
=========================================
Persistent long-term memory with vector search (pgvector).

Architecture:
  Local SQLite  →  fast reads for daemon ticks, FTS5 text search
  Supabase LTM  →  durable storage, vector search, sibling-accessible

Embeddings: Ollama nomic-embed-text (768-dim) — same as Kip's existing pipeline.
API: Supabase REST (postgREST) with service_role key for full CRUD.

Usage:
  from supabase_memory import SupabaseMemoryClient
  client = SupabaseMemoryClient()
  client.store(episode_dict)
  results = client.search("what did Papai say about the medical clerk")

Author: Lobi — 2026-05-23
"""

import json
import logging
import os
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import embeddings as emb  # Kip's existing Ollama embedding module

logger = logging.getLogger("supabase-memory")

# ── Config ────────────────────────────────────────────────────────────
VAULT_PATH = Path.home() / ".kolo" / "vault" / "supabase-kip.env"
SCHEMA_SQL = Path(__file__).parent.parent / "schema" / "pgvector-schema.sql"


def _load_env() -> dict:
    """Load Supabase credentials from vault file."""
    env = {}
    if VAULT_PATH.exists():
        with open(VAULT_PATH) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    env[key] = val
    return env


class SupabaseMemoryClient:
    """Kip's Supabase LTM client — vector search + durable storage."""

    def __init__(self):
        env = _load_env()
        self.url = env.get("SUPABASE_URL", "")
        self.anon_key = env.get("SUPABASE_ANON_KEY", "")
        self.service_key = env.get("SUPABASE_SERVICE_ROLE_KEY", "")
        self._connected = False
        self._checked = False

    @property
    def configured(self) -> bool:
        """Is Supabase configured (vault exists with URL + keys)? """
        return bool(self.url and self.service_key)

    def health(self) -> dict:
        """Check Supabase connectivity."""
        if not self.configured:
            return {"ok": False, "error": "not configured — vault missing"}

        try:
            # Try to query kip_memory count
            req = urllib.request.Request(
                f"{self.url}/rest/v1/kip_memory?select=count",
                headers={
                    "apikey": self.service_key,
                    "Authorization": f"Bearer {self.service_key}",
                    "Prefer": "count=exact",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                content_length = resp.headers.get("Content-Range", "")
                count = content_length.split("/")[-1] if content_length else "?"
                self._connected = True
                self._checked = True
                return {"ok": True, "objects": count, "url": self.url}

        except urllib.error.HTTPError as e:
            body = e.read().decode()[:200] if e.fp else ""
            self._checked = True
            return {
                "ok": False,
                "error": f"HTTP {e.code}: {body}",
                "note": "Schema may not be applied yet — run schema/pgvector-schema.sql",
            }
        except Exception as e:
            self._checked = True
            return {"ok": False, "error": str(e)[:200]}

    # ── Store ────────────────────────────────────────────────────────

    def store(self, obj: dict) -> dict:
        """Store a memory object in Supabase.

        Args:
            obj: Memory object dict with keys:
                id, type, title, summary, content, plane, scope, confidence,
                tags (list), entities (list), links (list), payload (dict),
                created_at (ISO str)

        Returns:
            {"ok": True, "id": "..."} or {"ok": False, "error": "..."}
        """
        if not self.configured:
            return {"ok": False, "error": "Supabase not configured"}

        # Build text for embedding: title + summary
        embed_text = f"{obj.get('title', '')} {obj.get('summary', '')}".strip()
        if not embed_text:
            embed_text = obj.get("content", "")[:500]

        # Get embedding from Ollama
        vector = emb.get_embedding(embed_text) if embed_text else None
        if vector is None and embed_text:
            logger.warning(f"No embedding generated for {obj['id'][:30]}...")
            # Try with a shorter text
            vector = emb.get_embedding(embed_text[:200])

        # Build row for Supabase
        row = {
            "id": obj["id"],
            "type": obj.get("type", "episode"),
            "title": obj.get("title", ""),
            "summary": obj.get("summary", ""),
            "content": obj.get("content", ""),
            "plane": obj.get("plane", "ops"),
            "scope": obj.get("scope", "agent"),
            "confidence": obj.get("confidence", 0.5),
            "embedding": vector if vector else [],
            "tags": obj.get("tags", []),
            "entities": json.dumps(obj.get("entities", [])),
            "links": json.dumps(obj.get("links", [])),
            "payload": json.dumps(obj.get("payload", {})),
            "created_at": obj.get("created_at", datetime.now(timezone.utc).isoformat()),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        payload = json.dumps(row).encode("utf-8")

        try:
            # UPSERT — if exists, update. If new, insert.
            req = urllib.request.Request(
                f"{self.url}/rest/v1/kip_memory",
                data=payload,
                headers={
                    "apikey": self.service_key,
                    "Authorization": f"Bearer {self.service_key}",
                    "Content-Type": "application/json",
                    "Prefer": "resolution=merge-duplicates",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                self._connected = True
                # Also log to audit
                self._audit("upsert", obj["id"], f"stored: {obj.get('title', '')[:100]}")
                return {"ok": True, "id": obj["id"]}

        except urllib.error.HTTPError as e:
            body = e.read().decode()[:300] if e.fp else str(e)
            logger.error(f"Supabase store failed: HTTP {e.code} — {body}")
            return {"ok": False, "error": f"HTTP {e.code}: {body[:200]}"}
        except Exception as e:
            logger.error(f"Supabase store failed: {e}")
            return {"ok": False, "error": str(e)[:200]}

    def store_batch(self, objects: list[dict]) -> dict:
        """Store multiple objects. No vector search for batch (performance)."""
        results = []
        for obj in objects:
            results.append(self.store(obj))
        ok_count = sum(1 for r in results if r.get("ok"))
        return {"ok": True, "stored": ok_count, "total": len(objects), "results": results}

    # ── Search ────────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        limit: int = 10,
        filter_type: Optional[str] = None,
        filter_plane: Optional[str] = None,
        min_similarity: float = 0.3,
    ) -> dict:
        """Semantic search using pgvector cosine similarity.

        Generates embedding locally via Ollama, then POSTs to the
        kip_search_memory RPC function on Supabase.

        Returns:
            {"ok": True, "items": [...]} or {"ok": False, "error": "..."}
        """
        if not self.configured:
            return {"ok": False, "error": "Supabase not configured"}

        # Generate embedding
        vector = emb.get_embedding(query)
        if not vector:
            return {"ok": False, "error": "Failed to generate embedding for query"}

        # Call the search RPC
        rpc_payload = json.dumps({
            "query_embedding": vector,
            "match_count": limit,
            "filter_type": filter_type,
            "filter_plane": filter_plane,
            "min_similarity": min_similarity,
        }).encode("utf-8")

        try:
            req = urllib.request.Request(
                f"{self.url}/rest/v1/rpc/kip_search_memory",
                data=rpc_payload,
                headers={
                    "apikey": self.service_key,
                    "Authorization": f"Bearer {self.service_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                items = json.loads(resp.read().decode())
                return {"ok": True, "items": items}

        except urllib.error.HTTPError as e:
            body = e.read().decode()[:300] if e.fp else str(e)
            logger.error(f"Supabase search failed: HTTP {e.code} — {body}")
            return {"ok": False, "error": f"HTTP {e.code}: {body[:200]}"}
        except Exception as e:
            logger.error(f"Supabase search failed: {e}")
            return {"ok": False, "error": str(e)[:200]}

    def search_all(
        self,
        query: str,
        limit: int = 10,
        min_similarity: float = 0.3,
    ) -> dict:
        """Search across Kip's own + shared (sibling) memories."""
        if not self.configured:
            return {"ok": False, "error": "Supabase not configured"}

        vector = emb.get_embedding(query)
        if not vector:
            return {"ok": False, "error": "Failed to generate embedding"}

        rpc_payload = json.dumps({
            "query_embedding": vector,
            "match_count": limit,
            "min_similarity": min_similarity,
        }).encode("utf-8")

        try:
            req = urllib.request.Request(
                f"{self.url}/rest/v1/rpc/kip_search_all",
                data=rpc_payload,
                headers={
                    "apikey": self.service_key,
                    "Authorization": f"Bearer {self.service_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                items = json.loads(resp.read().decode())
                return {"ok": True, "items": items}

        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}

    # ── Audit ─────────────────────────────────────────────────────────

    def _audit(self, action: str, object_id: str, detail: str = "") -> bool:
        """Write to kip_audit_log."""
        if not self.service_key:
            return False
        try:
            payload = json.dumps({
                "actor": "kip",
                "action": action,
                "object_id": object_id,
                "detail": detail[:500],
            }).encode("utf-8")
            req = urllib.request.Request(
                f"{self.url}/rest/v1/kip_audit_log",
                data=payload,
                headers={
                    "apikey": self.service_key,
                    "Authorization": f"Bearer {self.service_key}",
                    "Content-Type": "application/json",
                    "Prefer": "return=minimal",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5):
                return True
        except Exception:
            return False  # Audit failure is non-critical

    # ── Sync from local SQLite ────────────────────────────────────────

    def sync_from_sqlite(self, sqlite_path: str, limit: int = 50) -> dict:
        """Sync recent objects from local SQLite to Supabase.

        Args:
            sqlite_path: Path to Kip's local memory DB
            limit: Max objects to sync per call

        Returns:
            {"ok": True, "synced": N, "total": M}
        """
        import sqlite3

        if not self.configured:
            return {"ok": False, "error": "Supabase not configured"}

        try:
            con = sqlite3.connect(sqlite_path)
            con.row_factory = sqlite3.Row
            cur = con.cursor()

            # Get objects that haven't been synced (no updated_at = synced_at marker)
            # For now: get most recent objects by created_at
            cur.execute(
                "SELECT * FROM objects ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
            rows = cur.fetchall()
            synced = 0
            errors = 0

            for row in rows:
                obj_id = row["id"]
                # Get tags
                tag_cur = con.cursor()
                tag_cur.execute("SELECT tag FROM tags WHERE object_id = ?", (obj_id,))
                tags = [t["tag"] for t in tag_cur.fetchall()]

                # Get entities
                ent_cur = con.cursor()
                ent_cur.execute("SELECT entity_type, entity_value FROM entities WHERE object_id = ?", (obj_id,))
                entities = [{"entity_type": e["entity_type"], "entity_value": e["entity_value"]}
                           for e in ent_cur.fetchall()]

                # Get links
                link_cur = con.cursor()
                link_cur.execute("SELECT target, kind FROM links WHERE object_id = ?", (obj_id,))
                links = [{"target": l["target"], "kind": l["kind"]}
                        for l in link_cur.fetchall()]

                # Build content from JSON
                try:
                    content = json.dumps(json.loads(row["json"])) if row["json"] else "{}"
                except (json.JSONDecodeError, TypeError):
                    content = row["json"] if row["json"] else "{}"

                obj = {
                    "id": obj_id,
                    "type": row["type"],
                    "title": row["title"] or "",
                    "summary": row["summary"] or "",
                    "content": content,
                    "plane": row["plane"],
                    "scope": row["scope"],
                    "confidence": row["confidence"],
                    "tags": tags,
                    "entities": entities,
                    "links": links,
                    "payload": {},
                    "created_at": row["created_at"],
                }

                result = self.store(obj)
                if result.get("ok"):
                    synced += 1
                else:
                    errors += 1
                    logger.warning(f"Sync failed for {obj_id}: {result.get('error', '')}")

            con.close()
            logger.info(f"Supabase sync: {synced} stored, {errors} errors out of {len(rows)}")
            return {"ok": True, "synced": synced, "errors": errors, "total": len(rows)}

        except Exception as e:
            logger.error(f"Supabase sync failed: {e}")
            return {"ok": False, "error": str(e)[:200]}

    # ── Summary ───────────────────────────────────────────────────────

    def summary(self) -> dict:
        return {
            "configured": self.configured,
            "connected": self._connected,
            "checked": self._checked,
            "url": self.url[:50] + "..." if self.url else "not set",
            "region": "ap-northeast-1 (Tokyo)",
            "schema": str(SCHEMA_SQL) if SCHEMA_SQL.exists() else "missing",
        }


# ── Singleton ─────────────────────────────────────────────────────────
_client: Optional[SupabaseMemoryClient] = None


def get_client() -> SupabaseMemoryClient:
    global _client
    if _client is None:
        _client = SupabaseMemoryClient()
    return _client
