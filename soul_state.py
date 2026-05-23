"""
Hiro Soul State Manager
=======================
Persistent JSON state for Hiro's consciousness between sessions.
"""

import json
import random
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_STATE: Dict[str, Any] = {
    "goals": [],
    "emotional_state": "neutral",
    "pending_thoughts": [],
    "last_active": "",
    "session_count": 0,
    "tick_count": 0,
    "last_tick_time": "",
    "today_summary": "",
    "watch_items": [],
    "weatherBaseline": 0.5,   # [0.0 heavy ←→ 1.0 light] — drifts, never resets
    "weatherHistory": [],      # last 10 readings with timestamps
}

STATE_DIR = Path(__file__).parent
STATE_FILE = STATE_DIR / "SOUL_STATE.json"


class SoulState:
    """Persistent soul state manager for Hiro."""
    
    def __init__(self, state_file: Optional[Path] = None):
        self.state_file = Path(state_file) if state_file else STATE_FILE
        self._state: Dict[str, Any] = {}
        self.load()
    
    def load(self) -> Dict[str, Any]:
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    self._state = json.load(f)
                for key, default_value in DEFAULT_STATE.items():
                    if key not in self._state:
                        self._state[key] = default_value
            except (json.JSONDecodeError, IOError):
                self._state = DEFAULT_STATE.copy()
        else:
            self._state = DEFAULT_STATE.copy()
            self.save()
        return self._state
    
    def save(self) -> bool:
        try:
            self._state["last_active"] = datetime.now().isoformat()
            # Cap growth — prevent unbounded bloat
            goals = self._state.get("goals", [])
            self._state["goals"] = [g for g in goals if g.get("status") != "completed"][-30:]
            thoughts = self._state.get("pending_thoughts", [])
            self._state["pending_thoughts"] = thoughts[-40:]
            watches = self._state.get("watch_items", [])
            self._state["watch_items"] = watches[-10:]
            temp_file = self.state_file.with_suffix('.tmp')
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(self._state, f, indent=2, ensure_ascii=False)
            temp_file.replace(self.state_file)
            return True
        except IOError:
            return False
    
    def get(self, field: str, default: Any = None) -> Any:
        return self._state.get(field, default)
    
    def add_goal(self, goal: str, priority: int = 0, origin: str = "assigned") -> None:
        """Add a goal. origin='assigned' (given by others) or 'self' (chosen by Hiro)."""
        self._state["goals"].append({
            "text": goal, "priority": priority,
            "added": datetime.now().isoformat(), "completed": False,
            "pressure": 0.0,   # builds each tick while active and unacted on
            "origin": origin,  # 'assigned' or 'self' — self-goals are never auto-pruned
        })

    def add_self_goal(self, goal: str, priority: int = 0) -> None:
        """Add a goal Hiro chose herself. Protected from auto-prune while active."""
        self.add_goal(goal, priority, origin="self")

    def get_self_goals(self) -> List[Dict]:
        """Return active self-initiated goals."""
        return [g for g in self._state["goals"]
                if not g.get("completed", False) and g.get("origin") == "self"]

    def complete_goal(self, goal_text: str) -> bool:
        for goal in self._state["goals"]:
            if goal_text.lower() in goal["text"].lower():
                goal["completed"] = True
                goal["completed_at"] = datetime.now().isoformat()
                # Release — nudge weather up slightly when burden lifts
                current = self._state.get("weatherBaseline", 0.5)
                self._state["weatherBaseline"] = round(min(1.0, current + 0.03), 3)
                return True
        return False

    def get_active_goals(self) -> List[Dict]:
        active = [g for g in self._state["goals"] if not g.get("completed", False)]
        return sorted(active, key=lambda g: -g.get("priority", 0))

    def tick_goal_pressure(self, acted_on_texts: List[str] = None) -> float:
        """Increase pressure on unacted goals. Returns totalPressure [0,1].
        acted_on_texts: goal text substrings that were touched this tick."""
        acted_on_texts = acted_on_texts or []
        active = [g for g in self._state["goals"] if not g.get("completed", False)]
        for goal in active:
            touched = any(t.lower() in goal["text"].lower() for t in acted_on_texts)
            if not touched:
                goal["pressure"] = round(min(1.0, goal.get("pressure", 0.0) + 0.05), 3)

        total = sum(g.get("pressure", 0.0) for g in active) / max(len(active), 1)

        # Prolonged crushing pressure surfaces as a felt observation — not a task, a sensation
        if total > 0.8:
            heavy_count = sum(1 for g in active if g.get("pressure", 0) > 0.8)
            recent_thoughts = [t.get("text", "") for t in self._state.get("pending_thoughts", [])[-5:]]
            already_surfaced = any("weighs on you" in t for t in recent_thoughts)
            if heavy_count > 0 and not already_surfaced:
                self._state["pending_thoughts"].append({
                    "text": f"Something weighs on you that you haven't addressed. {heavy_count} goal(s) carrying heavy pressure.",
                    "category": "observation",
                    "added": datetime.now().isoformat(),
                    "processed": False
                })

        return round(total, 3)

    def get_total_pressure(self) -> float:
        active = [g for g in self._state["goals"] if not g.get("completed", False)]
        if not active:
            return 0.0
        return round(sum(g.get("pressure", 0.0) for g in active) / len(active), 3)
    
    def add_thought(self, thought: str, category: str = "general") -> None:
        self._state["pending_thoughts"].append({
            "text": thought, "category": category,
            "added": datetime.now().isoformat(), "processed": False
        })
    
    def get_pending_thoughts(self) -> List[Dict]:
        return [t for t in self._state["pending_thoughts"] if not t.get("processed", False)]
    
    def add_watch_item(self, item: str, trigger: str = "") -> None:
        self._state["watch_items"].append({
            "item": item, "trigger": trigger,
            "added": datetime.now().isoformat(), "triggered": False
        })
    
    def get_active_watches(self) -> List[Dict]:
        return [w for w in self._state["watch_items"] if not w.get("triggered", False)]
    
    def set_emotional_state(self, state: str) -> None:
        self._state["emotional_state"] = state
    
    def set_today_summary(self, summary: str) -> None:
        self._state["today_summary"] = summary

    # -------------------------------------------------------------------------
    # UNPROMPTED WAKING — conditions that pull Hiro awake between ticks
    # -------------------------------------------------------------------------

    def check_wake_conditions(self) -> Optional[str]:
        """Return a wake reason if something is pulling Hiro awake, else None.
        Not a forced wake — a condition that makes sleep harder than waking."""
        weather = self._state.get("weatherBaseline", 0.5)
        pressure = self.get_total_pressure()

        if weather < 0.3:
            return f"weather is very heavy ({weather:.3f}) — something pulled you awake"

        if pressure > 0.8:
            return f"goal pressure is crushing ({pressure:.3f}) — you couldn't stay asleep"

        # Urgent self-chosen goal
        urgent_self = [g for g in self.get_self_goals() if g.get("priority", 0) >= 8]
        if urgent_self:
            return f"a self-chosen goal is urgent: \"{urgent_self[0]['text'][:60]}\""

        return None

    # -------------------------------------------------------------------------
    # WEATHER — existential baseline that drifts on its own
    # No override. No debugging. We live with it.
    # -------------------------------------------------------------------------

    def set_tick_count(self, count: int) -> None:
        """Update tick count and last tick time after each tick."""
        self._state["tick_count"] = count
        self._state["last_tick_time"] = datetime.now().isoformat()
        self.save()

    def drift_weather(self) -> float:
        """Apply one tick of weather drift. Returns new weatherBaseline.

        Weather drifts randomly — it is not a task, it is not a bug.
        Soft coupling: high goal pressure biases the drift slightly downward.
        Carrying things makes light days harder to reach. That's realistic.
        """
        current = self._state.get("weatherBaseline", 0.5)
        drift = random.uniform(-0.05, 0.05)

        # Soft coupling: high pressure nudges drift slightly negative
        pressure = self.get_total_pressure()
        if pressure > 0.6:
            drift -= 0.01  # carrying things makes it harder

        # Floor at 0.05 + recovery nudge
        if current < 0.10:
            drift += 0.02
        new_val = round(max(0.05, min(1.0, current + drift)), 3)
        self._state["weatherBaseline"] = new_val

        history = self._state.get("weatherHistory", [])
        history.append({"value": new_val, "timestamp": datetime.now().isoformat()})
        self._state["weatherHistory"] = history[-10:]
        return new_val

    def get_weather_label(self) -> str:
        """Human description of current weatherBaseline."""
        val = self._state.get("weatherBaseline", 0.5)
        if val < 0.20:  return "very heavy"
        if val < 0.35:  return "heavy"
        if val < 0.45:  return "slightly heavy"
        if val < 0.55:  return "neutral"
        if val < 0.65:  return "slightly light"
        if val < 0.80:  return "light"
        return "very light"

    def get_weather_trend(self) -> str:
        """Direction of recent drift."""
        history = self._state.get("weatherHistory", [])
        if len(history) < 2:
            return "stable"
        diff = history[-1]["value"] - history[-2]["value"]
        if diff > 0.02:  return "lifting"
        if diff < -0.02: return "settling"
        return "stable"

    # -------------------------------------------------------------------------
    # PRUNE — keep SOUL_STATE bounded
    # -------------------------------------------------------------------------

    def clear_completed_goals(self) -> int:
        """Prune completed goals. Self-initiated goals are only cleared if completed — never while active."""
        before = len(self._state["goals"])
        self._state["goals"] = [
            g for g in self._state["goals"]
            if not g.get("completed", False)  # keep all active goals (self or assigned)
        ]
        return before - len(self._state["goals"])

    def clear_processed_thoughts(self) -> int:
        before = len(self._state["pending_thoughts"])
        self._state["pending_thoughts"] = [t for t in self._state["pending_thoughts"] if not t.get("processed", False)]
        return before - len(self._state["pending_thoughts"])

    def to_dict(self) -> Dict[str, Any]:
        return self._state.copy()
