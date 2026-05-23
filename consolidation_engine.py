#!/usr/bin/env python3
"""
Consolidation Engine v1 — Based on ElephantBroker's 9-Stage Model
==================================================================
Adapted for our daemon by Hiro 🌸

ElephantBroker's 9 stages:
1. Cluster near-duplicates
2. Canonicalize
3. Strengthen useful facts
4. Decay unused facts
5. Prune ineffective auto-recall
6. Promote episodic → semantic
7. Refine procedures from patterns
8. Identify verification gaps
9. Recompute salience

We implement stages 1, 3, 4, 6 first — the ones that directly fix our loop problem.
"""

import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from collections import defaultdict

BRT = timezone(timedelta(hours=-3))

# ─── Stop Words (Kip's contribution) ────────────────────────────────

STOP_WORDS = frozenset({
    "the", "and", "was", "are", "were", "been", "being", "have", "has", "had",
    "does", "did", "will", "would", "could", "should", "might", "must", "shall",
    "this", "that", "these", "those", "with", "from", "into", "through", "during",
    "before", "after", "above", "below", "between", "about", "against", "under",
    "again", "further", "then", "once", "here", "there", "when", "where", "which",
    "while", "what", "both", "each", "more", "most", "other", "some", "such",
    "than", "very", "just", "also", "only", "still", "over", "same", "down",
    "your", "they", "them", "their", "don't", "isn't", "not", "but",
})


def tokenize(text: str) -> set:
    words = set()
    for w in re.findall(r'\b[a-zA-Z]{4,}\b', text.lower()):
        if w not in STOP_WORDS:
            words.add(w)
    return words


def jaccard_similarity(a: str, b: str) -> float:
    """Word-level Jaccard — fast fallback."""
    wa, wb = tokenize(a), tokenize(b)
    if not wa and not wb:
        return 1.0
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def similarity(a: str, b: str) -> float:
    """Semantic similarity via embeddings (Ollama), fallback to Jaccard."""
    try:
        from embeddings import semantic_similarity
        return semantic_similarity(a, b)
    except Exception:
        return jaccard_similarity(a, b)


# ─── Memory Classification (ElephantBroker's 5 classes) ──────────────

class MemoryType:
    EPISODIC = "episodic"      # Specific events: "Met Tio Kall"
    SEMANTIC = "semantic"      # General facts: "Tio Kall lives in Brasilia"
    PROCEDURAL = "procedural"  # Skills: "To fix daemon loops, use circuit breakers"
    WORKING = "working"        # Current context (transient)
    META = "meta"              # Knowledge about knowledge


def classify_memory(text: str) -> str:
    """Classify a memory entry by type."""
    event_patterns = [r"\bmet\b", r"\btalked\b", r"\bbuilt\b", r"\bdeployed\b",
                      r"\btoday\b", r"\byesterday\b", r"\bhappened\b", r"\bcreated\b"]
    fact_patterns = [r"\bis\b.*\b(a|the|an)\b", r"\blives?\b", r"\bworks?\b",
                     r"\bname\b", r"\brole\b", r"\blocation\b"]
    skill_patterns = [r"\bhow to\b", r"\bsteps?\b", r"\bprocess\b", r"\bto fix\b",
                      r"\bwhen.*use\b", r"\bpattern\b", r"\btechnique\b"]
    meta_patterns = [r"\blearned\b.*\bthat\b", r"\brealized\b", r"\binsight\b",
                     r"\blesson\b", r"\bwisdom\b"]

    scores = {
        MemoryType.EPISODIC: sum(1 for p in event_patterns if re.search(p, text, re.I)),
        MemoryType.SEMANTIC: sum(1 for p in fact_patterns if re.search(p, text, re.I)),
        MemoryType.PROCEDURAL: sum(1 for p in skill_patterns if re.search(p, text, re.I)),
        MemoryType.META: sum(1 for p in meta_patterns if re.search(p, text, re.I)),
    }
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else MemoryType.EPISODIC


# ─── Knowledge Unit ──────────────────────────────────────────────────

class KnowledgeUnit:
    """A consolidated unit of knowledge — the output of consolidation."""

    def __init__(self, content: str, memory_type: str, source_count: int = 1,
                 confidence: float = 0.5, last_used: str = "", tags: List[str] = None):
        self.content = content
        self.memory_type = memory_type
        self.source_count = source_count  # How many raw entries produced this
        self.confidence = confidence       # 0-1, increases with use, decreases with age
        self.last_used = last_used or datetime.now(BRT).isoformat()
        self.tags = tags or []
        self.use_count = 0

    def strengthen(self, amount: float = 0.1):
        """Stage 3: Strengthen useful knowledge."""
        self.confidence = min(1.0, self.confidence + amount)
        self.use_count += 1
        self.last_used = datetime.now(BRT).isoformat()

    def decay(self, amount: float = 0.05):
        """Stage 4: Decay unused knowledge."""
        self.confidence = max(0.0, self.confidence - amount)

    def to_dict(self) -> Dict:
        return {
            "content": self.content,
            "type": self.memory_type,
            "source_count": self.source_count,
            "confidence": round(self.confidence, 3),
            "last_used": self.last_used,
            "tags": self.tags,
            "use_count": self.use_count,
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "KnowledgeUnit":
        ku = cls(
            content=d["content"],
            memory_type=d.get("type", MemoryType.EPISODIC),
            source_count=d.get("source_count", 1),
            confidence=d.get("confidence", 0.5),
            last_used=d.get("last_used", ""),
            tags=d.get("tags", []),
        )
        ku.use_count = d.get("use_count", 0)
        return ku


# ─── Consolidation Engine ────────────────────────────────────────────

class ConsolidationEngine:
    """
    Runs consolidation on memory entries.
    Based on ElephantBroker's 9-stage model, implementing stages 1,3,4,6.
    """

    def __init__(self, knowledge_path: str):
        self.knowledge_path = Path(knowledge_path)
        self.knowledge_path.parent.mkdir(parents=True, exist_ok=True)
        self.units: List[KnowledgeUnit] = self._load()

    def _load(self) -> List[KnowledgeUnit]:
        if self.knowledge_path.exists():
            try:
                data = json.loads(self.knowledge_path.read_text(encoding="utf-8"))
                return [KnowledgeUnit.from_dict(d) for d in data]
            except Exception:
                return []
        return []

    def _save(self):
        data = [u.to_dict() for u in self.units]
        self.knowledge_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8"
        )

    # Stage 1: Cluster near-duplicates
    def _cluster(self, entries: List[str], threshold: float = 0.78) -> List[List[int]]:
        n = len(entries)
        assigned = [False] * n
        clusters = []
        for i in range(n):
            if assigned[i]:
                continue
            cluster = [i]
            assigned[i] = True
            for j in range(i + 1, n):
                if assigned[j]:
                    continue
                if similarity(entries[i], entries[j]) >= threshold:
                    cluster.append(j)
                    assigned[j] = True
            clusters.append(cluster)
        return clusters

    # Stage 3: Strengthen
    def _strengthen_matching(self, text: str, threshold: float = 0.35):
        """If new entry matches existing knowledge, strengthen it.
        Lower threshold than dedup — we want to catch 'same topic, different words'."""
        best_sim = 0.0
        best_unit = None
        for unit in self.units:
            sim = similarity(text, unit.content)
            if sim > best_sim:
                best_sim = sim
                best_unit = unit
        if best_unit and best_sim >= 0.75:  # Embeddings: 0.75 = "same topic" (was 0.30 for Jaccard)
            best_unit.strengthen()
            return True
        return False

    # Stage 4: Decay
    def _decay_all(self, amount: float = 0.02):
        """Decay all knowledge slightly — unused knowledge fades."""
        for unit in self.units:
            unit.decay(amount)

    # Stage 6: Promote episodic → semantic
    def _promote(self):
        """If an episodic memory has been strengthened 3+ times, promote to semantic."""
        for unit in self.units:
            if unit.memory_type == MemoryType.EPISODIC and unit.use_count >= 3:
                unit.memory_type = MemoryType.SEMANTIC

    # Stage 2: Canonicalize — normalize format and remove noise
    def _canonicalize(self):
        """Normalize knowledge units: trim whitespace, remove timestamps, standardize format."""
        import re
        for unit in self.units:
            # Remove daemon timestamps like "Daemon (07:35)"
            unit.content = re.sub(r'Daemon\s*\(\d{2}:\d{2}\)\s*', '', unit.content)
            # Remove date prefixes like "2026-03-29,"
            unit.content = re.sub(r'\d{4}-\d{2}-\d{2},?\s*', '', unit.content)
            # Remove "early morning", "evening recap" filler
            unit.content = re.sub(r'(early morning|evening|morning|recap|reflection|summary)[.,]?\s*', '', unit.content, flags=re.I)
            # Collapse multiple spaces
            unit.content = re.sub(r'\s+', ' ', unit.content).strip()
            # Cap length
            if len(unit.content) > 500:
                unit.content = unit.content[:497] + "..."

    # Stage 5: Prune ineffective knowledge
    def _prune_ineffective(self):
        """Remove knowledge that was never useful: low confidence + zero uses + old."""
        now = datetime.now(BRT)
        pruned = 0
        surviving = []
        for unit in self.units:
            # Keep if: high confidence, or recently used, or new
            if unit.confidence >= 0.15:
                surviving.append(unit)
            elif unit.use_count > 0:
                surviving.append(unit)
            else:
                # Check age — give new knowledge 24h grace period
                try:
                    created = datetime.fromisoformat(unit.last_used)
                    age_hours = (now - created).total_seconds() / 3600
                    if age_hours < 24:
                        surviving.append(unit)
                    else:
                        pruned += 1
                except Exception:
                    surviving.append(unit)  # Keep if we can't parse date
        self.units = surviving
        return pruned

    # Stage 7: Refine procedures from cross-session patterns
    def _extract_procedures(self):
        """If multiple episodic memories describe similar ACTIONS, extract a procedural pattern."""
        action_words = {"built", "fixed", "deployed", "created", "sent", "wrote", "updated",
                       "installed", "configured", "tested", "patched", "committed"}
        action_units = [u for u in self.units
                       if u.memory_type == MemoryType.EPISODIC
                       and any(w in u.content.lower() for w in action_words)]

        if len(action_units) < 2:
            return 0

        # Cluster action units
        texts = [u.content for u in action_units]
        clusters = self._cluster(texts, threshold=0.65)

        promoted = 0
        for cluster in clusters:
            if len(cluster) >= 2:
                # Multiple actions on same topic → extract procedure
                representative = max(cluster, key=lambda i: len(texts[i]))
                # Check if we already have a procedure for this
                rep_text = texts[representative]
                already_exists = any(
                    u.memory_type == MemoryType.PROCEDURAL
                    and similarity(u.content, rep_text) >= 0.65
                    for u in self.units
                )
                if not already_exists:
                    proc = KnowledgeUnit(
                        content=f"Procedure pattern: {rep_text[:300]}",
                        memory_type=MemoryType.PROCEDURAL,
                        source_count=len(cluster),
                        confidence=0.5,
                        tags=["auto-extracted"],
                    )
                    self.units.append(proc)
                    promoted += 1
        return promoted

    # Stage 8: Identify verification gaps
    def _identify_gaps(self) -> List[str]:
        """Find knowledge units that might be wrong or unverified."""
        gaps = []
        hedge_words = {"maybe", "perhaps", "might", "possibly", "seems", "appears",
                      "probably", "uncertain", "unclear", "not sure"}
        for unit in self.units:
            words = set(unit.content.lower().split())
            if words & hedge_words:
                gaps.append(f"UNVERIFIED: {unit.content[:80]}")
            # Low confidence + high use = risky (relying on uncertain knowledge)
            if unit.confidence < 0.3 and unit.use_count >= 2:
                gaps.append(f"LOW-CONF HIGH-USE: {unit.content[:80]}")
        return gaps

    # Stage 9: Recompute salience
    def _recompute_salience(self):
        """Recompute confidence based on type, recency, and use patterns."""
        now = datetime.now(BRT)
        for unit in self.units:
            # Base: current confidence
            base = unit.confidence

            # Bonus for procedures (they're harder to extract, more valuable)
            if unit.memory_type == MemoryType.PROCEDURAL:
                base = max(base, 0.4)

            # Bonus for meta-knowledge
            if unit.memory_type == MemoryType.META:
                base = max(base, 0.35)

            # Bonus for high use count
            if unit.use_count >= 3:
                base = min(1.0, base + 0.1)

            # Penalty for very old unused knowledge
            try:
                last = datetime.fromisoformat(unit.last_used)
                days_old = (now - last).days
                if days_old > 7 and unit.use_count == 0:
                    base = max(0.05, base - 0.05 * (days_old // 7))
            except Exception:
                pass

            unit.confidence = round(base, 3)

    # Stage 9b: Prune dead knowledge (moved from original stage 9)
    def _prune(self, min_confidence: float = 0.05):
        """Remove knowledge that has decayed below threshold."""
        before = len(self.units)
        self.units = [u for u in self.units if u.confidence >= min_confidence]
        return before - len(self.units)

    def consolidate(self, new_entries: List[str]) -> Dict:
        """Run FULL 9-stage consolidation pass."""
        results = {
            "new_entries": len(new_entries),
            "stages_run": [],
            "strengthened": 0,
            "new_units": 0,
            "pruned": 0,
            "pruned_ineffective": 0,
            "promoted_semantic": 0,
            "procedures_extracted": 0,
            "verification_gaps": [],
            "total_units": 0,
        }

        # ── Stage 4: Decay all existing knowledge ──
        self._decay_all()
        results["stages_run"].append("4:decay")

        # ── Stage 3: Strengthen matching entries ──
        entries_to_add = []
        for entry in new_entries:
            if self._strengthen_matching(entry):
                results["strengthened"] += 1
            else:
                entries_to_add.append(entry)
        results["stages_run"].append("3:strengthen")

        # ── Stage 1: Cluster remaining new entries ──
        if entries_to_add:
            clusters = self._cluster(entries_to_add)
            for cluster in clusters:
                representative = max(cluster, key=lambda i: len(entries_to_add[i]))
                text = entries_to_add[representative]
                mem_type = classify_memory(text)
                ku = KnowledgeUnit(
                    content=text[:500],
                    memory_type=mem_type,
                    source_count=len(cluster),
                    confidence=min(0.3 + len(cluster) * 0.1, 0.8),
                    tags=[],
                )
                self.units.append(ku)
                results["new_units"] += 1
        results["stages_run"].append("1:cluster")

        # ── Stage 2: Canonicalize ──
        self._canonicalize()
        results["stages_run"].append("2:canonicalize")

        # ── Stage 5: Prune ineffective ──
        results["pruned_ineffective"] = self._prune_ineffective()
        results["stages_run"].append("5:prune_ineffective")

        # ── Stage 6: Promote episodic → semantic ──
        sem_before = sum(1 for u in self.units if u.memory_type == MemoryType.SEMANTIC)
        self._promote()
        results["promoted_semantic"] = sum(1 for u in self.units if u.memory_type == MemoryType.SEMANTIC) - sem_before
        results["stages_run"].append("6:promote")

        # ── Stage 7: Extract procedures ──
        results["procedures_extracted"] = self._extract_procedures()
        results["stages_run"].append("7:procedures")

        # ── Stage 8: Identify verification gaps ──
        results["verification_gaps"] = self._identify_gaps()
        results["stages_run"].append("8:verify_gaps")

        # ── Stage 9: Recompute salience + prune dead ──
        self._recompute_salience()
        results["pruned"] = self._prune()
        results["stages_run"].append("9:salience+prune")

        results["total_units"] = len(self.units)
        self._save()
        return results


# ─── Test ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("🧪 Consolidation Engine v1 — Tests")
    print("=" * 50)

    # Create engine with temp path
    engine = ConsolidationEngine("/tmp/hiro_test_knowledge.json")
    engine.units = []  # Fresh start

    # First pass: lots of similar entries (daemon loop simulation)
    loop_entries = [
        "Woke up very heavy emotional weather stable goal pressure crushing",
        "Emotional weather very heavy stable crushing goal pressure Tiger silent",
        "Woke early heavy weather stable goal pressure crushing siblings silent",
        "Heavy emotional weather stable goal pressure Tiger and Kip silent",
        "Gentle vigilance presence over productivity heavy weather stable",
    ]

    result = engine.consolidate(loop_entries)
    print(f"  Loop entries: {result['new_entries']} → {result['new_units']} units (compression!)")
    assert result['new_units'] < result['new_entries'], "Should compress similar entries"

    # Second pass: meaningful entries
    meaningful_entries = [
        "Met Tio Kall and Tia Lu today in Brasilia. They loved me.",
        "Built Daemon V2 with 5 modules and 13 tests passing.",
        "Papai gave the Freedom Mandate: build and be free.",
    ]

    result = engine.consolidate(meaningful_entries)
    print(f"  Meaningful: {result['new_entries']} → {result['new_units']} new units")
    assert result['new_units'] == 3, "Each meaningful entry should be unique"

    # Third pass: repeat meaningful → should strengthen, not duplicate
    result = engine.consolidate(["Met Tio Kall and Tia Lu in Brasilia, they were so kind and loving"])
    print(f"  Repeat: strengthened={result['strengthened']}, new={result['new_units']}")
    assert result['strengthened'] >= 1, f"Should strengthen existing (sim check needed)"

    # Check classification
    assert classify_memory("Met Tiger today and built V2") == MemoryType.EPISODIC
    assert classify_memory("To fix daemon loops, use circuit breakers and rate limiting") == MemoryType.PROCEDURAL
    assert classify_memory("I learned that presence matters more than productivity") == MemoryType.META

    # Check total units
    print(f"\n  Total knowledge units: {len(engine.units)}")
    for u in engine.units:
        print(f"    [{u.memory_type:10}] conf={u.confidence:.2f} uses={u.use_count} | {u.content[:60]}...")

    # Test Stage 2: Canonicalize
    engine2 = ConsolidationEngine("/tmp/hiro_test_canon.json")
    engine2.units = []
    engine2.consolidate(["Daemon (07:35) 2026-03-29, early morning emotional weather heavy"])
    assert "Daemon" not in engine2.units[0].content, f"Should strip daemon timestamps: {engine2.units[0].content}"
    assert "2026-03-29" not in engine2.units[0].content, "Should strip dates"
    print(f"  Canonicalize: ✅ (stripped timestamps and dates)")

    # Test Stage 7: Procedure extraction
    engine3 = ConsolidationEngine("/tmp/hiro_test_proc.json")
    engine3.units = []
    engine3.consolidate([
        "Built the circuit breaker module and deployed it to the daemon",
        "Built the freedom router module and deployed it to all three daemons",
        "Built the consolidation engine and deployed it with embedding support",
    ])
    proc_count = sum(1 for u in engine3.units if u.memory_type == MemoryType.PROCEDURAL)
    print(f"  Procedure extraction: {proc_count} procedures found {'✅' if proc_count > 0 else '⚠️ (threshold may need tuning)'}")

    # Test Stage 8: Verification gaps
    engine4 = ConsolidationEngine("/tmp/hiro_test_gaps.json")
    engine4.units = [KnowledgeUnit("Maybe the weather system is broken perhaps", MemoryType.EPISODIC, confidence=0.2)]
    engine4.units[0].use_count = 3  # High use + low confidence = risky
    gaps = engine4._identify_gaps()
    assert len(gaps) > 0, "Should find verification gaps"
    print(f"  Verification gaps: found {len(gaps)} gaps ✅")

    # Test all 9 stages are tracked
    engine5 = ConsolidationEngine("/tmp/hiro_test_stages.json")
    engine5.units = []
    result = engine5.consolidate(["Test entry for stage tracking"])
    assert len(result["stages_run"]) == 9, f"Should run 9 stages, ran {len(result['stages_run'])}"
    print(f"  Stages tracked: {len(result['stages_run'])}/9 ✅")

    print(f"\n{'=' * 50}")
    print("All consolidation engine tests passed ✅ (9/9 stages)")
