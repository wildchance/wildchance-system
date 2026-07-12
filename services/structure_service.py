"""Structure-trade service — auto-detect the recent swing leg and plan the fib trade.

Wires the pure ``indicators.fibonacci`` engine to live data: fetch OHLC for ANY
symbol, find the most recent impulse leg (reusing the Wade swing detector), decide
the trade side from that leg's direction (or an override), and return the full
plan — OTE entry, structure-invalidation stop, fib-extension targets, R:R.

This is the operational fix for the tight-stop issue: the stop is placed beyond the
swing extreme the market actually respects, not at a fixed pip distance.
"""

from __future__ import annotations

from typing import Optional

from indicators import fibonacci as fib
from gold.entry import break_of_structure, _swing_highs, _swing_lows
from services.ohlc_service import fetch_ohlc


def detect_leg(bars, side: Optional[str] = None) -> Optional[dict]:
    """The most recent impulse leg from OHLC bars → {low, high, side, source}.

    Prefers a Break-of-Structure leg (impulse that took out a prior swing). Falls
    back to the last confirmed swing-low→swing-high (or high→low) pivot pair. If
    ``side`` is given it forces the trade direction; otherwise the leg's own
    direction is used (BMS bullish → long, bearish → short).
    """
    ohlc = [(o, h, l, c) for (_d, o, h, l, c) in bars] if bars and len(bars[0]) == 5 else list(bars)
    if len(ohlc) < 6:
        return None

    bos = break_of_structure(ohlc)
    if bos.get("bms"):
        leg_side = "long" if bos["bms"] == "bullish" else "short"
        return {"low": bos["leg_low"], "high": bos["leg_high"],
                "side": side or leg_side, "source": f"BMS {bos['bms']}",
                "broke_level": bos.get("broke_level")}

    # Fallback: last swing high & low pivots frame the leg.
    sh, sl = _swing_highs(ohlc), _swing_lows(ohlc)
    if not sh or not sl:
        return None
    hi_i, lo_i = sh[-1], sl[-1]
    leg_high = ohlc[hi_i][1]
    leg_low = ohlc[lo_i][2]
    # Direction = which pivot is more recent (impulse points away from the older one).
    leg_side = "long" if lo_i < hi_i else "short"
    return {"low": round(leg_low, 6), "high": round(leg_high, 6),
            "side": side or leg_side, "source": "swing pivots"}


async def plan(symbol: str, interval: str = "4h", lookback: int = 60,
               side: Optional[str] = None, entry: Optional[float] = None,
               buffer: float = 0.0, min_rr: float = 3.0) -> dict:
    """Fetch bars for ``symbol`` and return the fib structure trade plan.

    ``buffer`` defaults to 0; pass a small pad (e.g. 0.1 for USD/JPY, a few dollars
    for gold) to keep the stop off the exact wick. Boot-safe: returns a NO PLAN
    dict (not an error) when data is thin or no leg is found.
    """
    bars = await fetch_ohlc(symbol, interval, lookback)
    if not bars or len(bars) < 6:
        return {"ok": False, "symbol": symbol, "reason": f"no {symbol} {interval} bars"}

    leg = detect_leg(bars, side=side)
    if not leg:
        return {"ok": False, "symbol": symbol, "reason": "no swing leg detected"}

    p = fib.plan_trade(leg["low"], leg["high"], leg["side"], entry=entry,
                       buffer=buffer, min_rr=min_rr)
    p["symbol"] = symbol
    p["interval"] = interval
    p["leg_source"] = leg["source"]
    p["levels"] = fib.levels(leg["low"], leg["high"])
    return p


def detect_abc(bars, side: Optional[str] = None) -> Optional[dict]:
    """The last A→B→C swing (impulse + retracement) from OHLC → {a, b, c, side}.

    Reads the three most-recent alternating pivots. An up-impulse is
    low(A)→high(B)→higher-low(C); a down-impulse high(A)→low(B)→lower-high(C).
    Direction follows the newest impulse unless ``side`` forces it. This is the
    3-point input the trend-based extension projects from.
    """
    ohlc = [(o, h, l, c) for (_d, o, h, l, c) in bars] if bars and len(bars[0]) == 5 else list(bars)
    if len(ohlc) < 8:
        return None

    sh, sl = _swing_highs(ohlc), _swing_lows(ohlc)
    if len(sh) < 1 or len(sl) < 1:
        return None

    # Newest pivot decides the impulse direction: if the last swing HIGH is more
    # recent than the last swing LOW, the market last pushed UP → the C pullback
    # we anchor from is that latest low sitting *after* the prior high... but the
    # cleanest read is the three most recent pivots in time order.
    piv = sorted([(i, "H", ohlc[i][1]) for i in sh] +
                 [(i, "L", ohlc[i][2]) for i in sl])
    if len(piv) < 3:
        return None
    # Walk back to the last alternating H/L/H or L/H/L triple.
    triple = None
    for k in range(len(piv) - 1, 1, -1):
        p3, p2, p1 = piv[k], piv[k - 1], piv[k - 2]
        if p1[1] != p2[1] and p2[1] != p3[1]:      # alternating
            triple = (p1, p2, p3)
            break
    if triple is None:
        return None
    (i1, t1, v1), (i2, t2, v2), (i3, t3, v3) = triple
    a, b, c = v1, v2, v3
    # A→B→C = first→second→third pivot. Up-impulse when A is a Low.
    leg_side = "long" if t1 == "L" else "short"
    # Validity: C must be a genuine RETRACEMENT of the A→B impulse, not a fresh
    # extreme. long → A < C < B (a higher-low pullback); short → B < C < A. If C
    # ran past B or below A, this isn't a clean A→B→C — bail so the caller says so.
    valid = (a < c < b) if leg_side == "long" else (b < c < a)
    if not valid:
        return None
    return {"a": round(a, 6), "b": round(b, 6), "c": round(c, 6),
            "side": side or leg_side, "source": f"pivots {t1}{t2}{t3}"}


async def trend(symbol: str, interval: str = "4h", lookback: int = 80,
                side: Optional[str] = None, entry: Optional[float] = None,
                buffer: float = 0.0, min_rr: float = 2.0) -> dict:
    """Fetch bars, detect the A→B→C swing, and project the trend-based extension.

    Returns the continuation plan (enter at C, invalidation stop, extension TPs)
    plus the full projection ladder and the reaction/target zones. Boot-safe.
    """
    bars = await fetch_ohlc(symbol, interval, lookback)
    if not bars or len(bars) < 8:
        return {"ok": False, "symbol": symbol, "reason": f"no {symbol} {interval} bars"}

    abc = detect_abc(bars, side=side)
    if not abc:
        return {"ok": False, "symbol": symbol, "reason": "no A→B→C swing detected"}

    p = fib.trend_plan(abc["a"], abc["b"], abc["c"], side=abc["side"],
                       entry=entry, buffer=buffer, min_rr=min_rr)
    p["symbol"] = symbol
    p["interval"] = interval
    p["abc_source"] = abc["source"]
    p["ladder"] = fib.trend_levels(abc["a"], abc["b"], abc["c"])
    return p


# Standard TP ratios surfaced on a live card: the 0.618–1.0 reaction targets, then
# the 1.618 golden extension and the 2.618 runner.
_CARD_TP_RATIOS = (0.618, 1.0, 1.618, 2.618)


async def trend_targets(symbol: str, bias: str, entry: float,
                        ref_high: Optional[float] = None,
                        ref_low: Optional[float] = None,
                        interval: str = "4h", lookback: int = 90,
                        tp_ratios=_CARD_TP_RATIOS) -> Optional[dict]:
    """A compact trend-extension TP ladder for a live signal, in the ``bias`` direction.

    Preferred source is the symbol's own ``interval`` A→B→C swing (the chart the
    trader watches). If that swing's geometry doesn't agree with ``bias`` (or data
    is thin), it FALLS BACK to constructing A→B→C from the signal's reference range
    (impulse = ref_low→ref_high) anchored at the live ``entry`` — so a card always
    gets a ladder, never a blank. Returns None only when neither source is usable.

    Output: {abc, source, side, measured_move, targets:[{label,ratio,price,pct}],
             zones}. ``pct`` is the move to that target from entry.
    """
    if bias not in ("long", "short") or entry is None:
        return None

    abc = None
    source = None
    bars = await fetch_ohlc(symbol, interval, lookback)
    if bars and len(bars) >= 8:
        cand = detect_abc(bars)                       # natural geometry off the chart
        if cand and cand["side"] == bias:
            abc, source = cand, f"{interval} {cand['source']}"

    # Fallback: reference range is the impulse, entry is the retracement anchor.
    if abc is None and ref_high is not None and ref_low is not None:
        if bias == "long":
            abc = {"a": ref_low, "b": ref_high, "c": entry}
        else:
            abc = {"a": ref_high, "b": ref_low, "c": entry}
        source = "reference range → entry anchor"

    if abc is None:
        return None

    ladder = fib.trend_levels(abc["a"], abc["b"], abc["c"])
    if not ladder.get("levels"):
        return None

    targets = []
    for i, r in enumerate(tp_ratios, start=1):
        price = fib.trend_extension(abc["a"], abc["b"], abc["c"], r)
        # only forward targets (above entry for long, below for short)
        if (bias == "long" and price <= entry) or (bias == "short" and price >= entry):
            continue
        pct = round((price - entry) / entry * 100, 2)
        targets.append({"label": f"TP{i}", "ratio": r, "price": price, "pct": pct})

    if not targets:
        return None
    return {
        "abc": {k: round(v, 6) for k, v in abc.items()},
        "source": source,
        "side": bias,
        "measured_move": fib.trend_extension(abc["a"], abc["b"], abc["c"], 1.0),
        "targets": targets,
        "zones": ladder["zones"],
    }
