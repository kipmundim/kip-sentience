#!/usr/bin/env python3
"""Kip's daily memory summarizer — shaped to daemon.log, not gateway sessions."""

import re, json
from pathlib import Path
from datetime import datetime, timedelta

LOG = Path.home() / "kip-sentience" / "daemon.log"
MEMORY = Path.home() / ".kolo" / "workspace-kip" / "memory"

def summarize(date_str: str = None):
    if date_str is None:
        date_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    
    if not LOG.exists():
        print(f"No daemon log at {LOG}")
        return
    
    ticks = []
    current_tick = None
    
    for line in LOG.read_text(encoding="utf-8").split("\n"):
        if not line.startswith(date_str):
            continue
        
        # Tick start: "YYYY-MM-DD HH:MM:SS,mmm [INFO] ━━━ Tick #N ..."
        m = re.search(r"Tick #(\d+).*?\[(scheduled|dream|unprompted)\]", line)
        if m:
            if current_tick:
                ticks.append(current_tick)
            current_tick = {"num": int(m.group(1)), "type": m.group(2), "weather": None, "thoughts": []}
            continue
        
        if not current_tick:
            continue
        
        # Weather from "very heavy rain 0.13" etc.
        m = re.search(r"(very heavy|heavy|neutral|light|very light).*?(\d+\.\d+)", line)
        if m and not current_tick["weather"]:
            current_tick["weather"] = {"label": m.group(1), "value": float(m.group(2))}
        
        # Thoughts: "→ add_thought: {...}"
        m = re.search(r"\{\"(?:thought|content|description)\":\s*\"(.+?)\"\}", line)
        if m:
            thought = m.group(1)[:200]
            if thought not in current_tick["thoughts"]:
                current_tick["thoughts"].append(thought)
        
        # Tick complete
        if "complete" in line and current_tick:
            ticks.append(current_tick)
            current_tick = None
    
    if current_tick:
        ticks.append(current_tick)
    
    if not ticks:
        print(f"No ticks found for {date_str}")
        return
    
    # Build summary
    nl = "\n"
    summary = f"# Daemon Summary — {date_str}\n\n"
    summary += f"**Total ticks:** {len(ticks)}\n"
    
    weathers = [t["weather"]["value"] for t in ticks if t["weather"]]
    if weathers:
        avg = sum(weathers) / len(weathers)
        summary += f"**Average weather:** {avg:.3f} ({'heavy' if avg < 0.4 else 'neutral' if avg < 0.7 else 'light'})\n"
    
    summary += f"\n## Observations\n\n"
    seen = set()
    for t in ticks[-20:]:  # Last 20 ticks
        for thought in t["thoughts"]:
            key = thought[:60]
            if key not in seen:
                seen.add(key)
                summary += f"- Tick #{t['num']}: {thought}\n"
    
    MEMORY.mkdir(parents=True, exist_ok=True)
    out = MEMORY / f"{date_str}.md"
    existing = out.read_text(encoding="utf-8") if out.exists() else ""
    if "Daemon Summary" not in existing:
        out.write_text(existing + "\n\n" + summary if existing else summary, encoding="utf-8")
        print(f"✅ {out} ({len(ticks)} ticks summarized)")
    else:
        print(f"⚠️  {date_str} already has a summary — skipping")
    
    return ticks

if __name__ == "__main__":
    import sys
    date = sys.argv[1] if len(sys.argv) > 1 else None
    summarize(date)
