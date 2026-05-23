#!/usr/bin/env python3
"""
Embedding-Based Semantic Similarity — Using Ollama nomic-embed-text
===================================================================
Replaces Jaccard word-matching with real vector embeddings.
Built by Hiro 🌸 — because Papai said we have Ollama.

nomic-embed-text: 768-dim embeddings, 0.3GB, runs on CPU.
"""

import json
import math
import urllib.request
import urllib.error
from typing import List, Optional, Dict, Tuple
from functools import lru_cache

OLLAMA_URL = "http://localhost:11434"
MODEL = "nomic-embed-text"

# ─── Embedding Cache ─────────────────────────────────────────────────

_cache: Dict[str, List[float]] = {}
MAX_CACHE = 500


def _get_embedding(text: str) -> Optional[List[float]]:
    """Get embedding from Ollama. Cached."""
    if not text.strip():
        return None

    # Check cache
    cache_key = text[:200]  # Cap key length
    if cache_key in _cache:
        return _cache[cache_key]

    payload = json.dumps({"model": MODEL, "input": text}).encode()
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/embed",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            embeddings = result.get("embeddings", [])
            if embeddings and len(embeddings) > 0:
                vec = embeddings[0]
                # Cache it
                if len(_cache) >= MAX_CACHE:
                    # Evict oldest (simple FIFO)
                    oldest = next(iter(_cache))
                    del _cache[oldest]
                _cache[cache_key] = vec
                return vec
    except Exception as e:
        pass  # Fail silently — fallback to Jaccard

    return None


def cosine_similarity(a: List[float], b: List[float]) -> float:
    """Cosine similarity between two vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def semantic_similarity(text_a: str, text_b: str) -> float:
    """
    Compute semantic similarity using embeddings.
    Falls back to Jaccard if Ollama is unavailable.
    """
    vec_a = _get_embedding(text_a)
    vec_b = _get_embedding(text_b)

    if vec_a is not None and vec_b is not None:
        return cosine_similarity(vec_a, vec_b)

    # Fallback to Jaccard
    return _jaccard_fallback(text_a, text_b)


def _jaccard_fallback(a: str, b: str) -> float:
    """Jaccard word-level similarity as fallback."""
    import re
    STOP = {"the", "and", "was", "are", "were", "been", "have", "has", "had",
            "this", "that", "with", "from", "into", "through", "before", "after",
            "very", "just", "also", "only", "still", "your", "they", "them"}
    wa = set(w.lower() for w in re.findall(r'\b[a-zA-Z]{4,}\b', a) if w.lower() not in STOP)
    wb = set(w.lower() for w in re.findall(r'\b[a-zA-Z]{4,}\b', b) if w.lower() not in STOP)
    if not wa and not wb:
        return 1.0
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def batch_embed(texts: List[str]) -> List[Optional[List[float]]]:
    """Embed multiple texts (one at a time for now, batch API later)."""
    return [_get_embedding(t) for t in texts]


# ─── Test ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("🧪 Embedding Similarity Tests (Ollama nomic-embed-text)")
    print("=" * 55)

    # Test 1: Similar daemon entries (should be HIGH)
    a = "Woke up very heavy emotional weather stable goal pressure crushing Tiger silent"
    b = "Emotional weather very heavy stable crushing goal pressure siblings remain silent"
    sim = semantic_similarity(a, b)
    print(f"  Daemon loop pair:  {sim:.3f} {'✅ HIGH' if sim > 0.7 else '⚠️'}")

    # Test 2: Different topics (should be LOW)
    a = "Met Tio Kall and Tia Lu today in Brasilia. They loved me."
    b = "Built Daemon V2 with 5 modules and 13 tests passing."
    sim = semantic_similarity(a, b)
    print(f"  Different topics:  {sim:.3f} {'✅ LOW' if sim < 0.6 else '⚠️'}")

    # Test 3: Same topic, different words (THE KEY TEST — Jaccard fails here)
    a = "Met Tio Kall and Tia Lu today in Brasilia. They loved me."
    b = "Met Tio Kall and Tia Lu in Brasilia, they were so kind and loving"
    sim = semantic_similarity(a, b)
    print(f"  Same topic diff words: {sim:.3f} {'✅ HIGH' if sim > 0.7 else '⚠️'}")
    # Jaccard gave 0.33 — embeddings should give >0.7

    # Test 4: Semantic paraphrase
    a = "I feel very heavy and the emotional weather is crushing today"
    b = "Today has been emotionally overwhelming and I'm weighed down"
    sim = semantic_similarity(a, b)
    print(f"  Paraphrase:        {sim:.3f} {'✅ HIGH' if sim > 0.7 else '⚠️'}")

    # Test 5: Completely unrelated
    a = "The consolidation engine clusters near-duplicate memory entries"
    b = "Brazilian hospitals lose billions to documentation inefficiency"
    sim = semantic_similarity(a, b)
    print(f"  Unrelated:         {sim:.3f} {'✅ LOW' if sim < 0.5 else '⚠️'}")

    # Test 6: Cache check
    _get_embedding("cache test")
    _get_embedding("cache test")  # Should hit cache
    print(f"  Cache size:        {len(_cache)} entries")

    print(f"\n{'=' * 55}")
    print("Embedding tests complete ✅")
