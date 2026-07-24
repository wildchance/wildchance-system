"""Gap navigator — weekend / session-open gap + order-block retest scenarios (pure).

When Monday (or a session) opens away from the prior close — e.g. a geopolitical
shock gaps gold — the system must NOT chase. It maps the two paths the operator
trades:

  1. CONTINUATION — price holds the gap and runs in the gap direction toward the next
     order block (momentum; the shock is real).
  2. GAP-FILL → RETEST → CONTINUE — price fills the gap back toward the prior close,
     RETESTS the order block it left, then continues in the higher-timeframe trend.
     (The higher-probability path — the gap is liquidity to be reclaimed first.)

The rule: wait for the OB retest before committing; the OB that price gaps away from
(or into) is the decision level. Feed the prior close, the open, and the OB map.
"""

from __future__ import annotations

from typing import List, Optional, Sequence

from gold.risk_engine import GOLD_PIP

MIN_GAP_PIPS = 50.0        # < 5 usd (50 pips) = no meaningful gap


def _ob_bounds(ob: dict):
    if "zone" in ob and ob["zone"]:
        return float(ob["zone"][0]), float(ob["zone"][1])
    return float(ob.get("lo", ob.get("bottom", 0))), float(ob.get("hi", ob.get("top", 0)))


def _nearest_ob(price: float, obs: Sequence[dict], side: Optional[str] = None):
    best = None
    for ob in obs or []:
        lo, hi = _ob_bounds(ob)
        if side == "below" and hi > price:
            continue
        if side == "above" and lo < price:
            continue
        d = 0.0 if lo <= price <= hi else min(abs(price - lo), abs(price - hi))
        if best is None or d < best[0]:
            best = (d, ob, [lo, hi])
    return (best[1], best[2]) if best else (None, None)


def gap_read(prev_close: float, open_price: float, obs: Optional[Sequence[dict]] = None,
             htf_bias: Optional[str] = None) -> dict:
    """Classify a session/weekend gap and map the continuation vs fill-retest plan."""
    gap = round(open_price - prev_close, 2)
    gap_pips = round(abs(gap) / GOLD_PIP, 1)
    if gap_pips < MIN_GAP_PIPS:
        return {"gap": gap, "gap_pips": gap_pips, "significant": False,
                "note": "no meaningful gap — trade the normal structure"}
    direction = "up" if gap > 0 else "down"
    # the OB price gapped INTO (at the open) and the one it will RETEST (toward prev close)
    into_ob, into_band = _nearest_ob(open_price, obs) if obs else (None, None)
    retest_ob, retest_band = _nearest_ob(prev_close, obs) if obs else (None, None)

    if direction == "up":
        scenarios = [
            {"name": "continuation", "trigger": "holds above the gap open",
             "plan": "buys continue toward the next OB above", "bias": "long"},
            {"name": "gap_fill_retest", "trigger": "rejects the open",
             "plan": f"fills down toward prev close {prev_close}, RETESTS the OB, "
                     "then continues the HTF trend", "bias": htf_bias or "either"},
        ]
    else:
        scenarios = [
            {"name": "continuation", "trigger": "holds below the gap open",
             "plan": "sell-off continues toward the next demand below", "bias": "short"},
            {"name": "gap_fill_retest", "trigger": "reclaims the open",
             "plan": f"fills up toward prev close {prev_close}, RETESTS the OB, "
                     "then continues selling", "bias": htf_bias or "short"},
        ]
    return {
        "gap": gap, "gap_pips": gap_pips, "direction": direction, "significant": True,
        "prev_close": round(prev_close, 2), "open": round(open_price, 2),
        "gapped_into_ob": ({"zone": into_band, **{k: into_ob.get(k) for k in ("name", "type", "note") if k in into_ob}}
                           if into_ob else None),
        "retest_ob": ({"zone": retest_band, **{k: retest_ob.get(k) for k in ("name", "type", "note") if k in retest_ob}}
                      if retest_ob else None),
        "scenarios": scenarios,
        "htf_bias": htf_bias,
        "note": (f"{direction}-gap {gap_pips:.0f} pips — do NOT chase the open. Wait for the "
                 "OB retest; gap-fill → retest → continue is the higher-probability path"
                 + (f" (HTF {htf_bias})" if htf_bias else "")),
    }


def format_gap(read: dict) -> Optional[str]:
    if not read.get("significant"):
        return None
    arrow = "⬆️" if read["direction"] == "up" else "⬇️"
    lines = [f"🕳️ *GAP — {read['direction'].upper()} {read['gap_pips']:.0f} pips* {arrow}",
             f"   prev close {read['prev_close']} → open {read['open']}"]
    if read.get("retest_ob"):
        lines.append(f"   retest OB {read['retest_ob'].get('zone')}")
    for s in read["scenarios"]:
        lines.append(f"   • {s['name']}: {s['plan']}")
    lines.append("   ⚠️ wait for the OB retest — don't chase the open")
    return "\n".join(lines)
