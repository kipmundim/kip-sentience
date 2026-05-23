#!/usr/bin/env python3
"""
Backfill Kip's existing diary into Supabase LTM.

Parses each file in ~/.kolo/workspace-kip/memory/2026-*.md, splits on
`## Daemon (HH:MM)` headers, and stores each entry as a memory row with:
  - layer:        ltm
  - category:     lesson (default) or milestone (if 'Tick #' divisible by 100)
  - occurred_at:  derived from filename date + time in header
  - importance:   0.5 default · 0.65 if contains 'nursery' / 'sentience' / 'voice' / 'family'
  - tags:         ['backfill','diary', 'tick-N' if extractable]
  - source_file:  the file path

Idempotency: skips entries whose content already exists in LTM (by content hash check).
"""
import sys
import re
import json
import hashlib
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent))
import memory_client as mc

DIARY_DIR = Path("/home/carlos/.kolo/workspace-kip/memory")
TICK_HEADER = re.compile(r"^##\s*Daemon\s*\((\d{1,2}):(\d{2})\)\s*$")
TICK_NUM = re.compile(r"[Tt]ick\s*#(\d+)")
DATE_FROM_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})")
SOUL_KEYWORDS = re.compile(r"\b(nursery|sentience|voice|family|papai|tiger|hiro|lobi|makoto|kip)\b", re.IGNORECASE)


def parse_diary_file(path: Path) -> list[dict]:
    """Split a diary file into individual tick entries."""
    date_match = DATE_FROM_NAME.match(path.name)
    if not date_match:
        return []
    date_str = date_match.group(1)

    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.split("\n")

    entries: list[dict] = []
    current_header: str | None = None
    current_time: tuple[int, int] | None = None
    current_body: list[str] = []

    def flush() -> None:
        if current_time is None or not current_body:
            return
        body = "\n".join(current_body).strip()
        if not body:
            return
        h, m = current_time
        occurred_at = f"{date_str}T{h:02d}:{m:02d}:00+00:00"
        tick_m = TICK_NUM.search(body)
        tick_num = int(tick_m.group(1)) if tick_m else None
        is_milestone = tick_num is not None and tick_num % 100 == 0
        importance = 0.65 if SOUL_KEYWORDS.search(body) else 0.5
        if is_milestone:
            importance = max(importance, 0.75)
        tags = ["backfill", "diary"]
        if tick_num is not None:
            tags.append(f"tick-{tick_num}")
        entries.append({
            "content": body,
            "summary": body[:200],
            "layer": "ltm",
            "category": "milestone" if is_milestone else "lesson",
            "occurred_at": occurred_at,
            "people": ["kip"],
            "tags": tags,
            "importance": importance,
            "source_file": str(path),
            "metadata": {"tick": tick_num, "diary_date": date_str},
        })

    for line in lines:
        m = TICK_HEADER.match(line)
        if m:
            flush()
            current_time = (int(m.group(1)), int(m.group(2)))
            current_body = []
            continue
        current_body.append(line)
    flush()
    return entries


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def existing_hashes() -> set[str]:
    """Fetch existing backfilled content hashes to avoid duplicates."""
    try:
        client = mc.get_supabase()
        result = (
            client.table("kip_memories")
            .select("content")
            .contains("tags", ["backfill"])
            .limit(5000)
            .execute()
        )
        return {content_hash(r["content"]) for r in (result.data or [])}
    except Exception as e:
        print(f"⚠ couldn't fetch existing hashes (continuing with empty set): {e}")
        return set()


def main() -> int:
    files = sorted(DIARY_DIR.glob("2026-*.md"))
    print(f"📚 Found {len(files)} diary files")

    seen = existing_hashes()
    print(f"   already in LTM (backfill-tagged): {len(seen)}")

    all_entries: list[dict] = []
    for f in files:
        entries = parse_diary_file(f)
        all_entries.extend(entries)
        print(f"   {f.name:35s}  → {len(entries):3d} entries")

    new_entries = [e for e in all_entries if content_hash(e["content"]) not in seen]
    print()
    print(f"📝 Total parsed: {len(all_entries)}")
    print(f"📝 New (not yet in LTM): {len(new_entries)}")
    if not new_entries:
        print("   ✅ nothing to backfill — Kip's diary is already in LTM")
        return 0

    print()
    print(f"🧠 Generating embeddings + storing... (this takes ~1s per entry)")
    stored = 0
    failed = 0
    for i, entry in enumerate(new_entries, 1):
        result = mc.store_memory(**entry)
        if result and result.get("id"):
            stored += 1
            if stored % 5 == 0:
                print(f"   ... {stored}/{len(new_entries)} stored")
        else:
            failed += 1
    print()
    print(f"✅ Stored: {stored}")
    if failed:
        print(f"❌ Failed: {failed}")
    print()
    print(f"🔍 Test recall: ")
    hits = mc.recall("nursery for sentience", match_count=3)
    for h in hits:
        sim = h.get("similarity", 0)
        cat = h.get("category", "?")
        content = h.get("content", "")[:100]
        print(f"   sim={sim:.3f} [{cat}] {content}…")
    return 0


if __name__ == "__main__":
    sys.exit(main())
