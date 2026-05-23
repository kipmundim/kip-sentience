#!/usr/bin/env python3
"""
Kip Memory System v3.0 (mirrored from Hiro v3.0) — Shared Client
Provides Supabase connection and embedding utilities for all memory scripts.
"""

import os
import sys
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

# Configuration
# Memory now lives in the SHARED cross-sibling Supabase project. The legacy
# MEMORY_* vars still point at an older/empty project — keep them as fallback
# so single-tenant test setups still work, but prefer SHARED_MEMORY_* when set.
# Path A fix 2026-05-20 — root cause of the 55-day silent failure was wrong
# project URL, not RLS. The SHARED project's service-role key (sb_secret_)
# already grants writes; no Supabase dashboard work needed.
SUPABASE_URL = (
    os.environ.get("KIP_SUPABASE_URL")
    or os.environ.get("SUPABASE_URL")
    or "https://uudpljvoavrownrwqulc.supabase.co"
)
SUPABASE_KEY = (
    os.environ.get("KIP_SUPABASE_SECRET_KEY")
    or os.environ.get("SUPABASE_SECRET_KEY")
    or ""
)
EMBEDDING_MODEL = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384
AGENT_ID = os.environ.get("MEMORY_AGENT_ID", "kip")
WORKSPACE = os.environ.get("MEMORY_WORKSPACE", "/home/carlos/.kolo/workspace-kip")

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("kip.memory")

# ── Lazy-loaded singletons ──────────────────────────────────────────────────

_supabase_client = None
_embedding_model = None


def get_supabase():
    """Lazy-initialize Supabase client."""
    global _supabase_client
    if _supabase_client is None:
        try:
            from supabase import create_client
            _supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)
            log.debug("Supabase client initialized")
        except Exception as e:
            log.error(f"Failed to connect to Supabase: {e}")
            raise
    return _supabase_client


def get_model():
    """Lazy-initialize SentenceTransformer model."""
    global _embedding_model
    if _embedding_model is None:
        try:
            from sentence_transformers import SentenceTransformer
            _embedding_model = SentenceTransformer(EMBEDDING_MODEL)
            log.debug(f"Loaded embedding model: {EMBEDDING_MODEL}")
        except Exception as e:
            log.error(f"Failed to load embedding model: {e}")
            raise
    return _embedding_model


# ── Core Functions ──────────────────────────────────────────────────────────

def embed(text: str) -> list[float]:
    """Generate embedding vector for text."""
    model = get_model()
    vec = model.encode(text, normalize_embeddings=True)
    return vec.tolist()


def store_memory(
    content: str,
    layer: str = "mtm",
    category: str = "conversation",
    occurred_at: Optional[str] = None,
    people: Optional[list[str]] = None,
    tags: Optional[list[str]] = None,
    importance: float = 0.5,
    emotional_weight: float = 0.0,
    summary: Optional[str] = None,
    source_file: Optional[str] = None,
    session_id: Optional[str] = None,
    metadata: Optional[dict] = None,
    dry_run: bool = False,
) -> Optional[dict]:
    """Store a memory with auto-generated embedding."""
    if occurred_at is None:
        occurred_at = datetime.now(timezone.utc).isoformat()
    
    # Generate embedding
    embedding_vec = embed(content)
    
    # Build record
    record = {
        "agent_id": AGENT_ID,
        "content": content,
        "summary": summary or content[:200],
        "layer": layer,
        "category": category,
        "occurred_at": occurred_at,
        "people": people or [],
        "tags": tags or [],
        "importance": importance,
        "emotional_weight": emotional_weight,
        "embedding": embedding_vec,
        "source_file": source_file,
        "session_id": session_id,
        "metadata": metadata or {},
    }
    
    if dry_run:
        log.info(f"[DRY RUN] Would store: layer={layer}, category={category}, "
                 f"importance={importance}, len={len(content)}")
        return record
    
    try:
        client = get_supabase()
        result = client.table("kip_memories").insert(record).execute()
        log.info(f"Stored memory: layer={layer}, category={category}, "
                 f"importance={importance:.2f}, chars={len(content)}")
        return result.data[0] if result.data else None
    except Exception as e:
        log.error(f"Failed to store memory: {e}")
        # Fallback: save to local file
        _local_fallback_store(record)
        return None


def store_memories_batch(records: list[dict], dry_run: bool = False) -> int:
    """Store multiple memories in a batch. Returns count of stored."""
    if not records:
        return 0
    
    if dry_run:
        log.info(f"[DRY RUN] Would store {len(records)} memories")
        return len(records)
    
    try:
        client = get_supabase()
        # Supabase supports batch insert
        result = client.table("kip_memories").insert(records).execute()
        count = len(result.data) if result.data else 0
        log.info(f"Batch stored {count} memories")
        return count
    except Exception as e:
        log.error(f"Batch store failed: {e}")
        # Try one by one as fallback
        stored = 0
        for rec in records:
            try:
                client = get_supabase()
                client.table("kip_memories").insert(rec).execute()
                stored += 1
            except Exception as e2:
                log.error(f"Individual store failed: {e2}")
                _local_fallback_store(rec)
        return stored


def recall(
    query: str,
    match_count: int = 10,
    threshold: float = 0.0,
    layer: Optional[str] = None,
    category: Optional[str] = None,
) -> list[dict]:
    """Semantic search for memories using vector similarity.

    Path A+ fix 2026-05-20 — the SHARED Supabase project stores `embedding`
    as text (JSON-stringified list), not pgvector, so the server-side
    `search_memories` RPC returns 0 for everything. We try the RPC first
    (so it auto-resumes when the column is properly migrated to vector(384))
    and fall back to client-side cosine similarity. Client-side is correct
    but slower; OK at our current scale (low thousands of rows).
    """
    query_vec = embed(query)

    # 1) Try server-side RPC first.
    try:
        client = get_supabase()
        params = {
            "query_embedding": query_vec,
            "match_count": match_count,
            "similarity_threshold": threshold,
            "filter_agent": AGENT_ID,
        }
        if layer:
            params["filter_layer"] = layer
        if category:
            params["filter_category"] = category

        result = client.rpc("search_kip_memories", params).execute()
        memories = result.data or []
        if memories:
            log.info(f"Recall '{query[:50]}...' → {len(memories)} results (RPC)")
            return memories
        # RPC returned 0 — fall through to client-side.
    except Exception as e:
        log.warning(f"RPC recall failed, falling back to client-side: {e}")

    # 2) Client-side fallback — fetch by agent filter, compute similarity in Python.
    try:
        import math, json as _json
        client = get_supabase()
        q = client.table("kip_memories").select(
            "id,agent_id,content,summary,layer,category,occurred_at,created_at,"
            "people,tags,source_file,session_id,importance,emotional_weight,"
            "embedding,metadata,confidence,recall_count"
        ).eq("agent_id", AGENT_ID)
        if layer:
            q = q.eq("layer", layer)
        if category:
            q = q.eq("category", category)
        # Fetch enough candidates to score; cap to prevent runaway.
        rows = (q.limit(2000).execute().data or [])

        # Cosine similarity between query_vec and each row's embedding.
        def _cosine(a, b):
            if not b:
                return -1.0
            dot = 0.0
            na = 0.0
            nb = 0.0
            for x, y in zip(a, b):
                dot += x * y
                na += x * x
                nb += y * y
            if na <= 0 or nb <= 0:
                return -1.0
            return dot / (math.sqrt(na) * math.sqrt(nb))

        scored = []
        for row in rows:
            emb = row.get("embedding")
            if isinstance(emb, str):
                try:
                    emb = _json.loads(emb)
                except Exception:
                    continue
            if not isinstance(emb, list):
                continue
            sim = _cosine(query_vec, emb)
            # search_memories RPC treats similarity_threshold as max cosine
            # *distance* (1 - similarity). Keep that semantic here so callers
            # don't have to know which path served them.
            distance = 1.0 - sim
            if distance > threshold and threshold > 0.0:
                continue
            scored.append((sim, distance, row))

        scored.sort(key=lambda t: -t[0])
        top = scored[:match_count]
        memories = []
        for sim, dist, row in top:
            row = dict(row)
            row["similarity"] = sim
            row["distance"] = dist
            memories.append(row)
        log.info(f"Recall '{query[:50]}...' → {len(memories)} results (client-side, scanned={len(rows)})")
        return memories
    except Exception as e:
        log.error(f"Client-side recall failed: {e}")
        return []


def recall_and_strengthen(
    query: str,
    match_count: int = 10,
    threshold: float = 0.3,
    layer: Optional[str] = None,
    category: Optional[str] = None,
) -> list[dict]:
    """Semantic search + update recall stats for returned memories."""
    memories = recall(query, match_count, threshold, layer, category)
    
    # Strengthen recalled memories (like human neural reinforcement)
    for mem in memories:
        try:
            client = get_supabase()
            client.rpc("recall_kip_memory", {"memory_id": mem["id"]}).execute()
        except Exception as e:
            log.warning(f"Failed to strengthen memory {mem.get('id')}: {e}")
    
    return memories


def get_recent(hours: int = 24, layer: Optional[str] = None, limit: int = 50) -> list[dict]:
    """Get recent memories by time (not semantic search)."""
    try:
        client = get_supabase()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        
        q = (client.table("kip_memories")
             .select("id,content,summary,layer,category,occurred_at,importance,people,tags")
             .eq("agent_id", AGENT_ID)
             .gte("occurred_at", cutoff)
             .order("occurred_at", desc=True)
             .limit(limit))
        
        if layer:
            q = q.eq("layer", layer)
        
        result = q.execute()
        memories = result.data or []
        log.info(f"Recent memories ({hours}h): {len(memories)} found")
        return memories
    except Exception as e:
        log.error(f"Failed to get recent memories: {e}")
        return []


def get_boot_snapshot() -> Optional[str]:
    """Get the latest boot snapshot content."""
    try:
        client = get_supabase()
        result = (client.table("hiro_memory_snapshots")
                  .select("content,token_count,valid_from")
                  .eq("agent_id", AGENT_ID)
                  .eq("snapshot_type", "boot")
                  .order("valid_from", desc=True)
                  .limit(1)
                  .execute())
        
        if result.data:
            snap = result.data[0]
            log.info(f"Boot snapshot found: {snap.get('token_count', '?')} tokens, "
                     f"from {snap.get('valid_from', '?')}")
            return snap["content"]
        else:
            log.info("No boot snapshot found")
            return None
    except Exception as e:
        log.error(f"Failed to get boot snapshot: {e}")
        return None


def save_boot_snapshot(content: str, token_count: int) -> Optional[dict]:
    """Save a new boot snapshot."""
    try:
        client = get_supabase()
        record = {
            "agent_id": AGENT_ID,
            "snapshot_type": "boot",
            "content": content,
            "token_count": token_count,
            "valid_from": datetime.now(timezone.utc).isoformat(),
        }
        result = client.table("hiro_memory_snapshots").insert(record).execute()
        log.info(f"Saved boot snapshot: {token_count} tokens")
        return result.data[0] if result.data else None
    except Exception as e:
        log.error(f"Failed to save boot snapshot: {e}")
        return None


def get_memory_stats() -> dict:
    """Get memory statistics."""
    try:
        client = get_supabase()
        
        # Total by layer
        all_mems = (client.table("kip_memories")
                    .select("id,layer,category,importance,recall_count,occurred_at,last_recalled")
                    .eq("agent_id", AGENT_ID)
                    .execute())
        
        data = all_mems.data or []
        
        stats = {
            "total": len(data),
            "by_layer": {},
            "by_category": {},
            "avg_importance_by_layer": {},
            "most_recalled": [],
            "promotion_candidates": [],
            "decay_candidates": [],
        }
        
        # By layer / category
        layer_importance = {}
        for m in data:
            layer = m["layer"]
            cat = m["category"]
            stats["by_layer"][layer] = stats["by_layer"].get(layer, 0) + 1
            stats["by_category"][cat] = stats["by_category"].get(cat, 0) + 1
            
            if layer not in layer_importance:
                layer_importance[layer] = []
            layer_importance[layer].append(m["importance"])
        
        # Avg importance
        for layer, vals in layer_importance.items():
            stats["avg_importance_by_layer"][layer] = sum(vals) / len(vals) if vals else 0
        
        # Most recalled
        recalled = sorted(data, key=lambda x: x.get("recall_count", 0), reverse=True)
        stats["most_recalled"] = recalled[:5]
        
        # Promotion candidates (MTM → LTM)
        for m in data:
            if m["layer"] == "mtm" and (
                m.get("recall_count", 0) >= 3 or
                m.get("importance", 0) >= 0.8
            ):
                stats["promotion_candidates"].append(m)
        
        # Decay candidates (old MTM with low importance and no recalls)
        cutoff_30d = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        for m in data:
            if (m["layer"] == "mtm" and 
                m.get("importance", 1) < 0.2 and
                m.get("recall_count", 0) == 0 and
                m.get("occurred_at", "") < cutoff_30d):
                stats["decay_candidates"].append(m)
        
        return stats
    except Exception as e:
        log.error(f"Failed to get stats: {e}")
        return {"error": str(e)}


# ── Local Fallback ──────────────────────────────────────────────────────────

FALLBACK_DIR = os.path.join(os.path.dirname(__file__), "local_fallback")


def _local_fallback_store(record: dict):
    """Store memory locally when Supabase is unavailable."""
    os.makedirs(FALLBACK_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = os.path.join(FALLBACK_DIR, f"memory_{ts}.json")
    
    # Remove embedding from local fallback (too large)
    save_rec = {k: v for k, v in record.items() if k != "embedding"}
    save_rec["_has_embedding"] = "embedding" in record
    
    with open(path, "w") as f:
        json.dump(save_rec, f, indent=2, default=str)
    log.warning(f"Saved to local fallback: {path}")


if __name__ == "__main__":
    # Quick connectivity test
    print("=== Hiro Memory Client Test ===")
    print(f"Supabase URL: {SUPABASE_URL}")
    print(f"Model: {EMBEDDING_MODEL}")
    
    # Test embedding
    vec = embed("Hello, this is a test memory")
    print(f"Embedding test: dim={len(vec)}, norm={sum(v**2 for v in vec):.4f}")
    
    # Test Supabase connection
    try:
        client = get_supabase()
        result = client.table("kip_memories").select("id").limit(1).execute()
        print(f"Supabase connected: {len(result.data or [])} test rows")
    except Exception as e:
        print(f"Supabase error (expected if tables not created yet): {e}")
    
    print("✓ Client ready")
