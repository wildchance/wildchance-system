"""Auto level finder — detect OB / swing / target structure from live OHLC.

The fix for stale levels: instead of hand-feeding Optimus the reaction map, derive it
from the bars. Detects:

  • swing highs / lows (fractal pivots) → the sell-retest levels + demand floors
  • order blocks — the last up-close candle before a down-displacement (bearish OB /
    supply) and the last down-close before an up-displacement (bullish OB / demand) →
    the wick+body ZONES the reject-gate fires on
  • the daily/4H structure pivots (recent high, low, mean)

Pure + stdlib-only. `build_levels(bars, price)` returns a map ready for
optimus.set_live_zones / set_sell_path, so the alerter always sits on today's structure.
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from gold.risk_engine import GOLD_PIP


def _ohlc(bar):
    if isinstance(bar, dict):
        return (float(bar["open"]), float(bar["high"]), float(bar["low"]), float(bar["close"]))
    return (float(bar[1]), float(bar[2]), float(bar[3]), float(bar[4]))


def atr(bars: Sequence, period: int = 14) -> float:
    trs = []
    prev_c = None
    for bar in bars:
        o, h, l, c = _ohlc(bar)
        tr = (h - l) if prev_c is None else max(h - l, abs(h - prev_c), abs(l - prev_c))
        trs.append(tr)
        prev_c = c
    if not trs:
        return 1.0
    tail = trs[-period:] if len(trs) >= period else trs
    return sum(tail) / len(tail) or 1.0


def swing_points(bars: Sequence, k: int = 2) -> dict:
    """Fractal swing highs/lows: a pivot whose high (low) exceeds the k bars on each side."""
    o = [_ohlc(b) for b in bars]
    highs, lows = [], []
    for i in range(k, len(o) - k):
        h, l = o[i][1], o[i][2]
        if all(h >= o[j][1] for j in range(i - k, i)) and all(h > o[j][1] for j in range(i + 1, i + k + 1)):
            highs.append((i, round(h, 2)))
        if all(l <= o[j][2] for j in range(i - k, i)) and all(l < o[j][2] for j in range(i + 1, i + k + 1)):
            lows.append((i, round(l, 2)))
    return {"highs": highs, "lows": lows}


def order_blocks(bars: Sequence, disp_mult: float = 1.1, window: int = 6) -> List[dict]:
    """Order blocks off displacement: the last up-close candle before a strong down bar
    (bearish OB / supply) and the last down-close before a strong up bar (bullish OB)."""
    o = [_ohlc(b) for b in bars]
    a = atr(bars)
    obs: List[dict] = []
    for i in range(1, len(o)):
        oo, hh, ll, cc = o[i]
        # bearish displacement → find the last up-close candle before it (supply OB)
        if (oo - cc) > disp_mult * a:
            for j in range(i - 1, max(-1, i - window), -1):
                oj, hj, lj, cj = o[j]
                if cj > oj:
                    obs.append({"side": "sell", "lo": round(min(oj, lj), 2), "hi": round(hj, 2),
                                "index": j, "mid": round((oj + hj) / 2, 2)})
                    break
        # bullish displacement → last down-close candle before it (demand OB)
        if (cc - oo) > disp_mult * a:
            for j in range(i - 1, max(-1, i - window), -1):
                oj, hj, lj, cj = o[j]
                if cj < oj:
                    obs.append({"side": "buy", "lo": round(lj, 2), "hi": round(max(oj, hj), 2),
                                "index": j, "mid": round((lj + oj) / 2, 2)})
                    break
    return obs


def _dedupe(levels: List[float], tol: float) -> List[float]:
    out: List[float] = []
    for lv in levels:
        if all(abs(lv - x) > tol for x in out):
            out.append(lv)
    return out


def build_levels(bars: Sequence, price: Optional[float] = None,
                 max_each: int = 5) -> dict:
    """Assemble the reaction map from the bars — sell zones/levels above price, demand
    floors/zones below — ready to feed optimus.set_live_zones / set_sell_path."""
    if not bars or len(bars) < 10:
        return {"ok": False, "reason": "need >=10 bars"}
    o = [_ohlc(b) for b in bars]
    price = float(price) if price is not None else o[-1][3]
    a = atr(bars)
    tol = max(2.0, 0.4 * a)

    sw = swing_points(bars)
    obs = order_blocks(bars)
    sell_obs = [z for z in obs if z["side"] == "sell" and z["hi"] > price]
    buy_obs = [z for z in obs if z["side"] == "buy" and z["lo"] < price]

    # sell-retest levels: swing highs + bearish-OB mids ABOVE price (nearest first)
    sell_raw = sorted({h for _, h in sw["highs"] if h > price}
                      | {z["mid"] for z in sell_obs}, )
    sell_levels = _dedupe([round(x, 2) for x in sell_raw], tol)[:max_each]

    # demand floors: swing lows + bullish-OB mids BELOW price (nearest first, desc)
    floor_raw = sorted(({l for _, l in sw["lows"] if l < price}
                        | {z["mid"] for z in buy_obs}), reverse=True)
    floors = _dedupe([round(x, 2) for x in floor_raw], tol)[:max_each]

    # zones (bands) for the reject-gate — dedupe by mid
    def _zones(z_obs, side):
        seen, out = [], []
        for z in sorted(z_obs, key=lambda z: abs(z["mid"] - price)):
            if any(abs(z["mid"] - s) <= tol for s in seen):
                continue
            seen.append(z["mid"])
            out.append({"name": f"{side}_ob_{int(round(z['mid']))}",
                        "lo": z["lo"], "hi": z["hi"],
                        "note": f"auto {side} OB @ {z['mid']:.0f}"})
        return out[:max_each]

    sell_zones = _zones(sell_obs, "sell")
    buy_zones = _zones(buy_obs, "buy")
    # if no OB zones detected near a swing level, synthesise a thin band around it
    if not sell_zones and sell_levels:
        sell_zones = [{"name": f"sell_{int(l)}", "lo": round(l - tol, 2), "hi": round(l, 2),
                       "note": f"auto swing-high {l:.0f}"} for l in sell_levels[:max_each]]
    if not buy_zones and floors:
        buy_zones = [{"name": f"buy_{int(l)}", "lo": round(l, 2), "hi": round(l + tol, 2),
                      "note": f"auto swing-low {l:.0f}"} for l in floors[:max_each]]

    recent = o[-40:] if len(o) >= 40 else o
    hi = max(r[1] for r in recent)
    lo = min(r[2] for r in recent)
    return {
        "ok": True, "price": round(price, 2), "atr": round(a, 2),
        "sell_retest_levels": sell_levels, "floors": floors,
        "sell_zones": sell_zones, "buy_zones": buy_zones,
        "pivots": {"recent_high": round(hi, 2), "recent_low": round(lo, 2),
                   "mean": round((hi + lo) / 2, 2)},
        "counts": {"swing_highs": len(sw["highs"]), "swing_lows": len(sw["lows"]),
                   "order_blocks": len(obs)},
    }
