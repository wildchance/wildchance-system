"""Owner theses — Elliott-wave scenarios checked against live prices → the best current bias.

data/theses.json holds, per symbol, the owner's count and its two scenarios with four
levels that decide between them:

    floor        the low the bounce must hold (e.g. wave 5 of Z ≈ 3,487)
    pivot zone   pivot_low..pivot_high — where the bounce is expected to fail (sell OB)
    line         above it the bearish scenario is dead (channel top / plan invalidation)

Live daily bars since the thesis date decide which LEG we are in:

    close > line                      → Option 1 confirmed       long-term BUY
    close < floor before the pivot    → bounce failed early      SELL (deeper leg now)
    pivot reached, then close < floor → final leg underway       SELL
    price inside the pivot zone       → decision zone            SELL on rejection
    pivot reached, back below it      → rejected                 SELL
    otherwise                         → bounce leg               short-term BUY → pivot

The thesis leg is then combined with the system's live reads — EdgeFinder v2 score and
the Spotter (signal lab) tilt — into one bias with a conviction label.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from typing import Dict, List, Optional

from decouple import config

THESES_JSON = str(config("THESES_JSON", default="data/theses.json"))


def load(path: Optional[str] = None) -> Dict[str, Dict]:
    path = path or THESES_JSON
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def _side_word(v: int) -> str:
    return "BUY" if v > 0 else "SELL" if v < 0 else "NEUTRAL"


def leg(th: Dict, bars: List[Dict], price: float) -> Dict:
    """Which leg of the thesis are we in? ``bars``: daily bars since the thesis date, oldest first."""
    lv = th["levels"]
    floor, plo, phi, line = lv["floor"], lv["pivot_low"], lv["pivot_high"], lv["line"]
    closes = [b["close"] for b in bars] + [price]
    reached = any(b["high"] >= plo for b in bars) or price >= plo
    if any(c > line for c in closes):
        return {"leg": "opt1_confirmed", "short": 1, "medium": 1,
                "text": f"closed above {line:,.0f} — Option 1 confirmed: wave 5 up, look for longs"}
    broke = [i for i, c in enumerate(closes) if c < floor]
    if broke:
        if reached:
            return {"leg": "final_leg", "short": -1, "medium": -1,
                    "text": f"rejected at {plo:,.0f}-{phi:,.0f} and broke {floor:,.0f} — final leg down underway"}
        return {"leg": "early_break", "short": -1, "medium": -1,
                "text": f"closed below {floor:,.0f} before reaching {plo:,.0f} — bounce failed, deeper leg now"}
    if plo <= price <= phi or (phi < price <= line):
        return {"leg": "decision", "short": 0, "medium": -1,
                "text": f"in the decision zone {plo:,.0f}-{line:,.0f} — sell a rejection, flip only above {line:,.0f}"}
    if reached:
        return {"leg": "rejected", "short": -1, "medium": -1,
                "text": f"tagged {plo:,.0f}+ and fell back — Option 2 in play, sell rallies under {phi:,.0f}"}
    return {"leg": "bounce", "short": 1, "medium": -1,
            "text": f"bounce leg from {floor:,.0f} toward {plo:,.0f}-{phi:,.0f} — buy dips above {floor:,.0f}, "
                    f"then the sell zone"}


def combine(lg: Dict, edge: Optional[Dict], spot: Optional[Dict]) -> Dict:
    """Thesis short-term leg (weight 2) + EdgeFinder (|score| ≥ 3) + Spotter (|tilt| ≥ 0.34)."""
    parts = [("thesis", 2 * lg["short"])]
    if edge and edge.get("score") is not None and abs(int(edge["score"])) >= 3:
        parts.append(("edgefinder", 1 if int(edge["score"]) > 0 else -1))
    else:
        parts.append(("edgefinder", 0))
    if spot and spot.get("signals") and abs(spot.get("tilt", 0)) >= 0.34:
        parts.append(("spotter", 1 if spot["tilt"] > 0 else -1))
    else:
        parts.append(("spotter", 0))
    total = sum(v for _, v in parts)
    agree = sum(1 for _, v in parts if v and (v > 0) == (total > 0)) if total else 0
    against = sum(1 for _, v in parts if v and total and (v > 0) != (total > 0))
    conv = "strong" if abs(total) >= 3 and not against else "moderate" if abs(total) >= 2 else \
        "weak" if total else "mixed"
    return {"bias": _side_word(total), "score": total, "conviction": conv,
            "votes": dict(parts), "agree": agree, "against": against}


def event_note(th: Dict, today: dt.date) -> Optional[str]:
    ev = th.get("event") or {}
    try:
        d = dt.date.fromisoformat(ev["date"])
    except (KeyError, ValueError, TypeError):
        return None
    days = (d - today).days
    if 0 <= days <= 2:
        return f"⚠️ {ev.get('name', 'event')} in {days} day(s) — size down, expect a spike through levels"
    if days > 2:
        return f"📅 {ev.get('name', 'event')} on {d:%d %b} ({days} days)"
    return None


def evaluate(symbol: str, th: Dict, bars: List[Dict], price: float, edge: Optional[Dict] = None,
             spot: Optional[Dict] = None, today: Optional[dt.date] = None) -> Dict:
    today = today or dt.date.today()
    lg = leg(th, bars, price)
    best = combine(lg, edge, spot)
    scen = []
    for s in th.get("scenarios") or []:
        state = "live"
        if lg["leg"] == "opt1_confirmed":
            state = "confirmed" if s["side"] == "BUY" else "invalidated"
        elif lg["leg"] in ("final_leg", "early_break"):
            state = "confirmed" if s["side"] == "SELL" else "invalidated"
        nxt = next((t for t in s.get("targets") or [] if (t > price if s["side"] == "BUY" else t < price)), None)
        scen.append({**s, "state": state, "next_target": nxt})
    return {"symbol": symbol, "price": price, "title": th.get("title"), "count": th.get("count"),
            "since": th.get("since"), "leg": lg, "medium_bias": _side_word(lg["medium"]),
            "best": best, "scenarios": scen, "event": event_note(th, today)}


def text(r: Dict) -> str:
    b, lg = r["best"], r["leg"]
    lines = [f"🧭 {r['symbol']} thesis · {r['price']:,.2f}",
             r.get("title") or "",
             f"Leg: {lg['text']}",
             f"Best bias now: *{b['bias']}* ({b['conviction']}) · thesis {b['votes']['thesis']:+d} · "
             f"EdgeFinder {b['votes']['edgefinder']:+d} · Spotter {b['votes']['spotter']:+d}",
             f"Medium term: {r['medium_bias']}"]
    for s in r["scenarios"]:
        icon = {"confirmed": "✅", "invalidated": "❌"}.get(s["state"], "•")
        nt = f" · next {s['next_target']:,.0f}" if s.get("next_target") else ""
        lines.append(f"{icon} {s['name']} ({s['state']}{nt})")
    if r.get("event"):
        lines.append(r["event"])
    lines.append("Bias ≠ entry — trades still need a trigger + council GO.")
    return "\n".join(x for x in lines if x)


async def run(symbol: str) -> Dict:
    sym = symbol.upper().replace("/", "")
    th = load().get(sym)
    if not th:
        return {"ok": False, "reason": f"no thesis for {sym} (data/theses.json)", "have": sorted(load())}
    from services import signal_lab
    bars = await signal_lab._daily(sym, 120)
    if not bars:
        return {"ok": False, "reason": f"no live prices for {sym}"}
    since = th.get("since") or "1970-01-01"
    after = [b for b in bars if b["d"] > since]
    price = bars[-1]["close"]
    edge = spot = None
    try:
        from services import edge_v2
        edge = await edge_v2.score_for(sym)
    except Exception:
        pass
    try:
        spot = await signal_lab.spotter(sym)
    except Exception:
        pass
    return {"ok": True, **evaluate(sym, th, after, price, edge, spot)}
