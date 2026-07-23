"""Event horizon (B14) — impact propagation, decay, and event stacking (pure).

Turns a calendar of scheduled events into a temporal risk model:

  • classify — each event gets a base impact weight (FOMC/CPI/NFP heaviest).
  • propagate — impact decays over the horizon: an exponential decay across the
    short (0-48h) / intermediate (1-4wk) / long (1-6mo) windows.
  • stack — overlapping high-impact events compound the near-term volatility (a
    CPI the day before FOMC is more than the sum of its parts).
  • calendar-aware sizing — a position-size modifier that shrinks into a heavy
    event cluster and normalises as it decays.

Pure: feed a list of events [{name, hours_until}] and it returns the model. The
service layer maps the live calendar onto it.
"""

from __future__ import annotations

import math
from typing import List, Optional, Sequence

# Base impact weights by event class (0-1). Matched case-insensitively by keyword.
EVENT_WEIGHTS = {
    "fomc": 1.0, "rate decision": 1.0, "interest rate": 0.95,
    "cpi": 0.92, "inflation": 0.9, "pce": 0.85, "nfp": 0.88, "non-farm": 0.88,
    "payroll": 0.85, "unemployment": 0.75, "jobs": 0.7,
    "gdp": 0.65, "ppi": 0.55, "retail sales": 0.55, "powell": 0.85,
    "fed": 0.7, "jackson hole": 0.9, "ecb": 0.6, "boe": 0.5,
}
DEFAULT_WEIGHT = 0.3

# Horizon boundaries in hours.
SHORT_H = 48                 # 0-48h
INTER_H = 24 * 28            # 1-4 weeks
LONG_H = 24 * 30 * 6         # 1-6 months
# Decay constant (hours) — impact halves roughly every ~36h in the near field.
DECAY_TAU = 52.0


def classify_event(name: str) -> float:
    n = (name or "").lower()
    for kw, w in EVENT_WEIGHTS.items():
        if kw in n:
            return w
    return DEFAULT_WEIGHT


def horizon_of(hours_until: float) -> str:
    h = abs(hours_until)
    if h <= SHORT_H:
        return "short"        # 0-48h
    if h <= INTER_H:
        return "intermediate"  # 1-4 weeks
    if h <= LONG_H:
        return "long"          # 1-6 months
    return "beyond"


def propagate(weight: float, hours_until: float) -> float:
    """Decayed live impact of an event ``hours_until`` away (pre-event anticipation
    builds as it nears; post-event, |hours| decays it)."""
    return round(weight * math.exp(-abs(hours_until) / (DECAY_TAU * 4)), 4)


def stack(events: Sequence[dict]) -> dict:
    """Compound near-term (short-horizon) impact — clustered heavy events amplify.

    ``events`` = [{name, hours_until}]. Returns the near-term stacked impact and a
    volatility multiplier (>1 = compression of risk into a cluster)."""
    near = [e for e in events if horizon_of(e.get("hours_until", 9e9)) == "short"]
    live = [propagate(classify_event(e.get("name", "")), e.get("hours_until", 0))
            for e in near]
    if not live:
        return {"near_events": 0, "stacked_impact": 0.0, "vol_multiplier": 1.0}
    base = max(live)
    # compounding: each additional near event adds a fraction of its impact
    extra = sum(sorted(live, reverse=True)[1:])
    stacked = round(min(1.0, base + 0.5 * extra), 3)
    vol_mult = round(1.0 + stacked, 3)          # 1.0 (calm) → 2.0 (heavy cluster)
    return {"near_events": len(near), "stacked_impact": stacked,
            "vol_multiplier": vol_mult}


def size_modifier(events: Sequence[dict]) -> float:
    """Position-size modifier for the event field — shrink into a heavy near cluster.
    ~[0.5, 1.0]: 1.0 when calm, 0.5 into a stacked FOMC/CPI window."""
    s = stack(events)
    return round(max(0.5, 1.0 - 0.5 * s["stacked_impact"]), 3)


def event_horizon(events: Sequence[dict], top_n: int = 8) -> dict:
    """The full B14 read — classified events by horizon, live decayed impact, the
    near-term stack, and the calendar-aware sizing modifier."""
    rows = []
    for e in events or []:
        name = e.get("name", "")
        hu = e.get("hours_until", 0.0)
        w = classify_event(name)
        rows.append({"name": name, "hours_until": round(hu, 1),
                     "horizon": horizon_of(hu), "base_weight": w,
                     "live_impact": propagate(w, hu)})
    rows.sort(key=lambda r: -r["live_impact"])
    st = stack(events or [])
    by_h = {h: [r for r in rows if r["horizon"] == h]
            for h in ("short", "intermediate", "long")}
    return {
        "events": rows[:top_n],
        "by_horizon": {h: len(v) for h, v in by_h.items()},
        "stack": st,
        "size_modifier": size_modifier(events or []),
        "note": (f"{st['near_events']} near event(s), stacked impact "
                 f"{st['stacked_impact']} → vol ×{st['vol_multiplier']}, "
                 f"size ×{size_modifier(events or [])}"),
    }
