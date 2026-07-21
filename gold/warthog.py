"""Warthog — HTF liquidity-sweep + OTE catapult engine (pure, 1H and above).

The "warthog" hunts swept liquidity on the higher timeframe and turns it into a
structure entry: a swing high/low is SWEPT (stops grabbed), price retraces >60%
into the OTE fib band, then the trend continues — the sweep is the catapult that
loads the next leg. It answers, for a given HTF gold series:

  • did we just sweep a HTF high (buy-side) or low (sell-side)?
  • is there a BMS trend to continue?
  • the OTE (0.62-0.79) retracement entry of the impulse leg, with the stop beyond
    the swept extreme / previous high-low (within R:R), and the next liquidity pool
    as the target (e.g. sweep 3941 → 3886).

Built on gold.entry (BMS / OTE / OB / FVG). Feed it H1/H4 (o,h,l,c) bars oldest-
first — the warthog frames the trend so the lower-timeframe scans hunt WITH it.
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from gold.entry import (_swing_highs, _swing_lows, break_of_structure,
                        ote_zone, order_block, OHLC)

_OTE_MIN_RETRACE = 0.60          # the ">60% retrace" catapult threshold
_STOP_BUFFER = 1.5               # stop beyond the swept extreme (gold price)


def _impulse_leg(bars: Sequence[OHLC]) -> Tuple[Optional[str], float, float]:
    """The dominant recent impulse leg + its direction, robust to a live pullback.

    Uses the highest high / lowest low and their ORDER: if the low prints AFTER the
    high the impulse is DOWN (bearish); if the high prints after the low it is UP.
    Returns (trend, leg_low, leg_high)."""
    hh_idx = max(range(len(bars)), key=lambda i: bars[i][1])
    ll_idx = min(range(len(bars)), key=lambda i: bars[i][2])
    leg_high, leg_low = bars[hh_idx][1], bars[ll_idx][2]
    if ll_idx > hh_idx:
        return "bearish", leg_low, leg_high
    if hh_idx > ll_idx:
        return "bullish", leg_low, leg_high
    return None, leg_low, leg_high


def detect_sweep(bars: Sequence[OHLC], left: int = 2, right: int = 2) -> Optional[dict]:
    """The most recent HTF liquidity sweep: a prior swing high whose level a later
    bar traded through, or a prior swing low taken out. Returns the swept level +
    whether price CLOSED back inside (reclaim = reversal) or beyond (continuation)."""
    sh, sl = _swing_highs(bars, left, right), _swing_lows(bars, left, right)
    best = None
    for i in sh:
        lvl = bars[i][1]
        for j in range(i + 1, len(bars)):
            if bars[j][1] > lvl:
                reclaim = bars[-1][3] < lvl        # closed back below the swept high
                cand = {"type": "high", "level": round(lvl, 4), "swing_idx": i,
                        "swept_idx": j, "reclaim": reclaim}
                if best is None or j >= best["swept_idx"]:
                    best = cand
                break
    for i in sl:
        lvl = bars[i][2]
        for j in range(i + 1, len(bars)):
            if bars[j][2] < lvl:
                reclaim = bars[-1][3] > lvl        # closed back above the swept low
                cand = {"type": "low", "level": round(lvl, 4), "swing_idx": i,
                        "swept_idx": j, "reclaim": reclaim}
                if best is None or j >= best["swept_idx"]:
                    best = cand
                break
    return best


def _liquidity_targets(bars: Sequence[OHLC], side: str, ref: float,
                       n: int = 2) -> List[float]:
    """The next liquidity pools in the trade direction — prior swing lows below the
    reference (for shorts) / swing highs above it (for longs)."""
    if side == "short":
        lows = sorted({round(bars[i][2], 2) for i in _swing_lows(bars)}, reverse=True)
        return [p for p in lows if p < ref][:n]
    highs = sorted({round(bars[i][1], 2) for i in _swing_highs(bars)})
    return [p for p in highs if p > ref][:n]


def warthog(bars: Sequence[OHLC], side: Optional[str] = None) -> dict:
    """HTF sweep + OTE continuation setup for gold.

    Direction defaults to the BMS trend; pass ``side`` to force it. Returns a
    LONG/SHORT card (entry/stop/zone/targets/retracement) or {"signal": "NONE"}.
    """
    if len(bars) < 8:
        return {"signal": "NONE", "reason": "need >=8 HTF candles"}
    trend, leg_low, leg_high = _impulse_leg(bars)
    if side is None:
        side = "long" if trend == "bullish" else "short" if trend == "bearish" else None
    if side is None:
        return {"signal": "NONE", "reason": "no HTF impulse leg",
                "sweep": detect_sweep(bars)}

    rng = leg_high - leg_low
    if rng <= 0:
        return {"signal": "NONE", "reason": "degenerate leg"}
    ote = ote_zone(leg_low, leg_high, side)
    ob = order_block(bars, side)
    entry = ote["entry"] if ote else None
    # stop beyond the swept extreme / previous high-low, within R:R.
    if side == "long":
        stop = leg_low - _STOP_BUFFER
        if ob:
            stop = min(stop, ob["ob_low"] - _STOP_BUFFER)
    else:
        stop = leg_high + _STOP_BUFFER
        if ob:
            stop = max(stop, ob["ob_high"] + _STOP_BUFFER)

    price = bars[-1][3]
    # retracement into the leg (0 = at the extreme, 1 = full giveback of the impulse)
    retr = round(max(0.0, min(1.5, (price - leg_low) / rng if side == "short"
                                    else (leg_high - price) / rng)), 3)
    in_ote = bool(ote and ote["zone"][0] <= price <= ote["zone"][1])

    risk = abs(entry - stop) if (entry is not None) else 0.0
    sign = 1 if side == "long" else -1
    targets = [{"rr": rr, "price": round(entry + sign * rr * risk, 2)}
               for rr in (2.0, 3.0, 5.0) if risk > 0]
    liq = _liquidity_targets(bars, side, entry if entry else price)

    sweep = detect_sweep(bars)
    signal = "LONG" if side == "long" else "SHORT"
    catapult = retr >= _OTE_MIN_RETRACE
    return {
        "signal": signal, "pattern": "warthog", "trend": trend,
        "leg": [round(leg_low, 2), round(leg_high, 2)],
        "entry": round(entry, 2) if entry else None,
        "stop": round(stop, 2) if entry else None,
        "ote_zone": ote["zone"] if ote else None,
        "retracement": retr, "in_ote": in_ote,
        "catapult": bool(catapult),        # retraced past 60% → loaded to continue
        "targets": targets, "liquidity_targets": liq, "order_block": ob,
        "sweep": sweep,
        "note": (f"warthog {signal}: {trend} trend, "
                 + (f"swept the {sweep['type']} {sweep['level']}, " if sweep else "")
                 + f"{int(retr * 100)}% retrace into OTE {ote['zone'] if ote else '—'}; "
                 + ("in the OTE — catapult loaded" if (in_ote and catapult)
                    else "awaiting the OTE pullback")
                 + (f"; next liquidity {liq}" if liq else "")),
    }


def to_ohlc(raw: Sequence) -> List[OHLC]:
    """Normalise fetch_ohlc_raw dicts (or [ts,o,h,l,c]) → (o,h,l,c) tuples."""
    out: List[OHLC] = []
    for b in raw:
        if isinstance(b, dict):
            out.append((float(b["open"]), float(b["high"]), float(b["low"]), float(b["close"])))
        else:
            out.append((float(b[1]), float(b[2]), float(b[3]), float(b[4])))
    return out


def format_warthog(read: dict) -> Optional[str]:
    """Telegram line for a fired warthog setup, else None."""
    if not read or read.get("signal") not in ("LONG", "SHORT"):
        return None
    arrow = "🟢" if read["signal"] == "LONG" else "🔴"
    sw = read.get("sweep")
    swept = f"swept {sw['type']} {sw['level']} · " if sw else ""
    load = "⚡catapult loaded" if read.get("in_ote") and read.get("catapult") else "await OTE"
    liq = f" → liq {read['liquidity_targets']}" if read.get("liquidity_targets") else ""
    return (f"🐗 *WARTHOG — {read['signal']}*  ({read['trend']})\n"
            f"{arrow} {swept}{int(read['retracement'] * 100)}% OTE  ·  {load}\n"
            f"   entry `{read['entry']}`  SL `{read['stop']}`{liq}")
