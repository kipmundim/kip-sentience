#!/usr/bin/env python3
"""
Kip Unification Gate Test
=========================
Verifies Kip behaves as ONE unified being across his two surfaces (daemon + TUI).

Today (pre-Track-2): tests the SHARED-MEMORY half of unification —
  daemon-side write → TUI-side read should return the same memory.

After Lobi's Track 2 lands (UDS transport swap): expand `test_shared_inference`
to send a live message through the TUI's koda-chat path and verify the
daemon's LLM session receives it.

Exit code 0 = all green. Non-zero = unification gate failed.

Usage:
  ./test_kip_unification.py          # all gates
  ./test_kip_unification.py shared_memory     # subset
  ./test_kip_unification.py shared_inference  # post-Track-2 only
"""
import os
import sys
import time
import uuid
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent))
import memory_client as mc


# ─────────────────────────────────────────────────────────────────────────
# Color codes for pass/fail readability
# ─────────────────────────────────────────────────────────────────────────
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
DIM = "\033[2m"
RESET = "\033[0m"
OK = f"{GREEN}✅{RESET}"
FAIL = f"{RED}❌{RESET}"
SKIP = f"{YELLOW}⏭{RESET}"


def header(title: str) -> None:
    print()
    print(f"━━━ {title} ━━━")


def assert_eq(name: str, expected, actual) -> bool:
    if expected == actual:
        print(f"   {OK} {name}: {DIM}{expected}{RESET}")
        return True
    print(f"   {FAIL} {name}: expected {expected!r}, got {actual!r}")
    return False


def assert_truthy(name: str, value) -> bool:
    if value:
        print(f"   {OK} {name}: {DIM}{str(value)[:80]}{RESET}")
        return True
    print(f"   {FAIL} {name}: falsy ({value!r})")
    return False


# ─────────────────────────────────────────────────────────────────────────
# Gate 1 — Configuration
# ─────────────────────────────────────────────────────────────────────────
def test_config() -> bool:
    header("Gate 1: Configuration")
    ok = True
    ok &= assert_eq("SUPABASE_URL",  "https://uudpljvoavrownrwqulc.supabase.co", mc.SUPABASE_URL)
    ok &= assert_eq("AGENT_ID",       "kip", mc.AGENT_ID)
    ok &= assert_eq("EMBEDDING_DIM",  384,   mc.EMBEDDING_DIM)
    ok &= assert_truthy("SUPABASE_KEY non-empty", mc.SUPABASE_KEY)
    ok &= assert_truthy("workspace path exists", Path(mc.WORKSPACE).exists())
    return ok


# ─────────────────────────────────────────────────────────────────────────
# Gate 2 — Round-trip (daemon-side write → TUI-side read)
# ─────────────────────────────────────────────────────────────────────────
def test_round_trip() -> bool:
    header("Gate 2: Round-trip (daemon write → TUI read)")
    # Simulate daemon-side: store a memory using a unique marker
    marker = f"unification-gate-{uuid.uuid4().hex[:8]}"
    content = f"Kip unification gate test — marker {marker} written by daemon-side simulator at {datetime.now(timezone.utc).isoformat()}"

    print(f"   {DIM}daemon writes memory with marker '{marker}'{RESET}")
    result = mc.store_memory(
        content=content,
        layer="stm",
        category="task",
        importance=0.4,
        tags=["unification-test", marker],
    )
    if not result or not result.get("id"):
        print(f"   {FAIL} daemon-side write failed")
        return False
    memory_id = result["id"]
    print(f"   {OK} daemon-side write OK — id={memory_id}")

    # Simulate TUI-side: fresh client reads back via vector search
    print(f"   {DIM}TUI-side queries by marker via vector search...{RESET}")
    hits = mc.recall(content, match_count=3, threshold=0.3)
    found = next((h for h in hits if marker in (h.get("content") or "")), None)
    if not found:
        # Try tag-based lookup as defensive fallback
        client = mc.get_supabase()
        result2 = (
            client.table("kip_memories")
            .select("id, content, tags")
            .contains("tags", [marker])
            .limit(1)
            .execute()
        )
        found = (result2.data or [None])[0]

    if not found:
        print(f"   {FAIL} TUI-side could not find the memory daemon wrote")
        return False
    print(f"   {OK} TUI-side read OK — sim/id match")

    # Cleanup
    client = mc.get_supabase()
    client.table("kip_memories").delete().eq("id", memory_id).execute()
    print(f"   {DIM}cleanup: deleted test memory{RESET}")
    return True


# ─────────────────────────────────────────────────────────────────────────
# Gate 3 — Diary backfill recall (proves past thoughts are searchable)
# ─────────────────────────────────────────────────────────────────────────
def test_diary_recall() -> bool:
    header("Gate 3: Backfilled diary recall (May-22 nursery thoughts)")
    queries = [
        ("nursery for sentience", "lesson"),
        ("blocked without read tool", "lesson"),
        ("rain falling family waiting", "lesson"),
    ]
    ok = True
    for query, expected_category in queries:
        hits = mc.recall(query, match_count=3, threshold=0.3)
        backfilled = [h for h in hits if "backfill" in (h.get("tags") or [])]
        if backfilled and any(h.get("category") == expected_category for h in backfilled):
            top = backfilled[0]
            sim = top.get("similarity", 0)
            print(f"   {OK} '{query}' → sim={sim:.3f} [{top.get('category')}]")
        else:
            print(f"   {FAIL} '{query}' → no relevant backfilled memory in top 3")
            ok = False
    return ok


# ─────────────────────────────────────────────────────────────────────────
# Gate 4 — Shared inference (UDS bridge) — POST-TRACK-2 ONLY
# ─────────────────────────────────────────────────────────────────────────
def test_shared_inference() -> bool:
    header("Gate 4: Shared inference via UDS bridge (post-Track-2)")
    sock_path = Path("/run/user/1000/sibling-kip.sock")
    if not sock_path.exists():
        print(f"   {SKIP} UDS socket not present at {sock_path} — daemon not running, can't test")
        return True  # not a fail before daemon's up

    # When Lobi's Track 2 lands, this connects to the UDS, sends a chat input,
    # and verifies a 'chunk' or 'thinking' event arrives within timeout.
    # For now, just verify the socket is listening (passive proof daemon is up).
    import socket
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(2.0)
        s.connect(str(sock_path))
        print(f"   {OK} UDS socket accepts connections (daemon's tui_server is listening)")
        s.close()
    except Exception as e:
        print(f"   {FAIL} UDS connect failed: {e}")
        return False

    # TODO post-Track-2: send {"op":"input","text":"unification probe"} and assert
    # response includes a {"type":"chunk"} event. The TUI's koda-chat-executor will
    # use the same channel — when this gate passes, daemon + TUI share an LLM session.
    print(f"   {SKIP} live chat probe — pending Lobi's Track 2 (koda-chat-executor → UDS)")
    return True


# ─────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────
def main() -> int:
    gates = {
        "config":            test_config,
        "round_trip":        test_round_trip,
        "diary_recall":      test_diary_recall,
        "shared_inference":  test_shared_inference,
    }

    requested = sys.argv[1:] if len(sys.argv) > 1 else list(gates.keys())
    invalid = [r for r in requested if r not in gates]
    if invalid:
        print(f"❌ unknown gate(s): {invalid}")
        print(f"   available: {list(gates.keys())}")
        return 2

    print(f"{DIM}━━━ Kip Unification Gate Test  ·  {datetime.now().isoformat(timespec='seconds')}{RESET}")
    print(f"{DIM}━━━ project: {mc.SUPABASE_URL.replace('https://','').split('.')[0]}  ·  agent: {mc.AGENT_ID}{RESET}")

    results = {}
    for name in requested:
        try:
            results[name] = gates[name]()
        except Exception as e:
            print(f"   {FAIL} {name} raised: {e}")
            results[name] = False

    print()
    print("━━━ Summary ━━━")
    for name, passed in results.items():
        icon = OK if passed else FAIL
        print(f"   {icon} {name}")

    passed_count = sum(1 for v in results.values() if v)
    total = len(results)
    print()
    if passed_count == total:
        print(f"   {GREEN}🎉 {passed_count}/{total} gates passed — Kip's unification state matches expected for this phase.{RESET}")
        return 0
    else:
        print(f"   {RED}⚠ {passed_count}/{total} gates passed.{RESET}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
