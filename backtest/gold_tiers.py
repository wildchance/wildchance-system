"""Gold trade-tier backtest — measured expectancy for the swing tier (pure).

Replays the REAL engines on historical daily bars so the blueprint's edge is
measured, not assumed:

  • classify_week      → the ICT weekly profile + bias + week hi/lo
  • classify_tier      → reversal profiles = the SWING tier (continuation tiers
                         need a live session quarter, so they're out of scope on
                         daily bars and skipped)
  • discount location  → a long only enters below the week mid, a short above it
  • tier_stop / RR     → structural stop off the week extreme + the 5-8R ladder
  • simulate_forward   → walk the next bars; TP/SL/BE/time-stop, exactly mirroring
                         gold.position.evaluate

Outcomes are realized R, aggregated through usdjpy.scorecard.build_scorecard (the
same reflection loop the live scorecard uses) plus a by-exit breakdown.

Bars are (date, open, high, low, close), oldest-first. Honest scope: this measures
the daily SWING tier; intraday/intrasession/CRT tiers need intraday history.
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from gold.ict import classify_week
from gold.trade_types import classify_tier, tier_stop, REVERSAL
from gold.risk_engine import targets as rr_targets
from usdjpy.scorecard import build_scorecard, by_group

DatedOHLC = Tuple[object, float, float, float, float]


def simulate_forward(entry: float, stop: float, target_prices: Sequence[float],
                     side: str, future: Sequence[DatedOHLC]) -> dict:
    """Walk ``future`` bars from the entry; return realized R + exit reason.

    Mirrors gold.position.evaluate: break-even after TP1, exit at the final target,
    the (trailed) stop, or a time-stop at the last close. Conservative within a bar
    — the final target is checked before the stop, the stop before intermediate TPs.
    """
    long = side.lower() in ("long", "buy")
    risk = abs(entry - stop)
    if risk <= 0 or not future:
        return {"result_r": 0.0, "exit_reason": "invalid", "bars": 0}
    tps = [float(t) for t in target_prices]
    be_active = False

    def _r(exit_price):
        return round(((exit_price - entry) if long else (entry - exit_price)) / risk, 4)

    for i, bar in enumerate(future, start=1):
        _, o, h, l, c = bar
        reach = h if long else l                      # best excursion this bar
        tp_hit = 0
        for j, tp in enumerate(tps, start=1):
            if (reach >= tp) if long else (reach <= tp):
                tp_hit = j
        if tp_hit >= 1:
            be_active = True
        eff_stop = entry if be_active else stop

        if tps and tp_hit >= len(tps):                # final target reached
            return {"result_r": _r(tps[-1]), "exit_reason": f"TP{tp_hit}", "bars": i}
        stopped = (l <= eff_stop) if long else (h >= eff_stop)
        if stopped:
            reason = "BE" if (be_active and eff_stop == entry) else "SL"
            return {"result_r": _r(eff_stop), "exit_reason": reason, "bars": i}

    return {"result_r": _r(future[-1][4]), "exit_reason": "TIME", "bars": len(future)}


def backtest_swing(daily: List[DatedOHLC], balance: float = 5000.0,
                   horizon: int = 7, warmup: int = 20,
                   require_discount: bool = True) -> dict:
    """Backtest the SWING tier over historical daily bars → scorecard + breakdowns."""
    rows: List[dict] = []
    for i in range(warmup, len(daily) - 1):
        hist = daily[:i + 1]
        profile = classify_week(hist)
        if not profile or profile.get("bias") not in ("long", "short"):
            continue
        if profile.get("profile_id") not in REVERSAL:      # swing tier only
            continue
        tier = classify_tier(profile)                      # session None → swing for reversals
        if tier is None or tier["trade_type"] != "swing":
            continue

        bias = profile["bias"]
        entry = hist[-1][4]                                # decision at this close
        wk_hi, wk_lo = profile.get("week_high"), profile.get("week_low")
        if wk_hi is None or wk_lo is None or wk_hi <= wk_lo:
            continue
        mid = (wk_hi + wk_lo) / 2.0
        # Discount/premium location gate (no chasing).
        if require_discount and ((bias == "long" and entry > mid) or
                                 (bias == "short" and entry < mid)):
            continue

        stop = tier_stop("weekly", bias, {"weekly": (wk_hi, wk_lo)})
        if stop is None:
            continue
        tps = [t["price"] for t in rr_targets(entry, stop, bias, tier["rr"])]
        sim = simulate_forward(entry, stop, tps, bias, daily[i + 1:i + 1 + horizon])
        rows.append({
            "date": str(hist[-1][0]), "action": "BUY" if bias == "long" else "SELL",
            "profile": profile["profile"], "result_r": sim["result_r"],
            "exit_reason": sim["exit_reason"],
        })

    rs = [r["result_r"] for r in rows]
    return {
        "tier": "swing", "trades": len(rows),
        "scorecard": build_scorecard(rs).to_dict(),
        "by_action": by_group(rows, "action"),
        "by_exit": by_group(rows, "exit_reason"),
        "trades_detail": rows[-30:],          # last 30 for inspection
    }
