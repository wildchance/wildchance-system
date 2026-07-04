"""Wade / SMC entry engine — BMS → OTE Fib → Order Block / FVG (pure).

Refines a signal's ENTRY from 'current price' to a structure zone:

  BMS   Break of Market Structure — close beyond the prior swing high/low = the
        impulse leg that sets direction.
  OTE   Optimal Trade Entry — the 0.62 / 0.705 / 0.79 retracement of the BMS leg;
        enter on the pullback into that band (tighter stop, better RR).
  OB    Order Block — the last opposing candle before the impulse (demand/supply).
  FVG   Fair Value Gap — a 3-candle imbalance left by the impulse.

Bars are (open, high, low, close) tuples, oldest-first (use H1/M15 intraday bars).
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

OHLC = Tuple[float, float, float, float]
_FIBS = (0.62, 0.705, 0.79)


def _swing_highs(bars: Sequence[OHLC], left: int = 2, right: int = 2) -> List[int]:
    out = []
    for i in range(left, len(bars) - right):
        h = bars[i][1]
        win = bars[i - left:i + right + 1]
        if h == max(b[1] for b in win) and h > max(
                bars[j][1] for j in range(i - left, i + right + 1) if j != i):
            out.append(i)
    return out


def _swing_lows(bars: Sequence[OHLC], left: int = 2, right: int = 2) -> List[int]:
    out = []
    for i in range(left, len(bars) - right):
        lo = bars[i][2]
        win = bars[i - left:i + right + 1]
        if lo == min(b[2] for b in win) and lo < min(
                bars[j][2] for j in range(i - left, i + right + 1) if j != i):
            out.append(i)
    return out


def break_of_structure(bars: Sequence[OHLC]) -> dict:
    """Most recent BMS + the impulse leg (low→high for bullish, high→low bearish)."""
    if len(bars) < 6:
        return {"bms": None}
    sh, sl = _swing_highs(bars), _swing_lows(bars)
    close = bars[-1][3]

    if sh and close > bars[sh[-1]][1]:
        shi = sh[-1]
        prior_lows = [i for i in sl if i < shi]
        lo_idx = prior_lows[-1] if prior_lows else max(0, shi - 5)
        leg_low = min(bars[j][2] for j in range(lo_idx, shi + 1))
        leg_high = max(bars[j][1] for j in range(lo_idx, len(bars)))
        return {"bms": "bullish", "broke_level": round(bars[shi][1], 4),
                "leg_low": round(leg_low, 4), "leg_high": round(leg_high, 4)}

    if sl and close < bars[sl[-1]][2]:
        sli = sl[-1]
        prior_highs = [i for i in sh if i < sli]
        hi_idx = prior_highs[-1] if prior_highs else max(0, sli - 5)
        leg_high = max(bars[j][1] for j in range(hi_idx, sli + 1))
        leg_low = min(bars[j][2] for j in range(hi_idx, len(bars)))
        return {"bms": "bearish", "broke_level": round(bars[sli][2], 4),
                "leg_low": round(leg_low, 4), "leg_high": round(leg_high, 4)}

    return {"bms": None}


def ote_zone(leg_low: float, leg_high: float, side: str) -> Optional[dict]:
    """0.62 / 0.705 / 0.79 retracement band of the leg, for entering on the pullback."""
    rng = leg_high - leg_low
    if rng <= 0:
        return None
    long = side.lower() in ("long", "buy", "bullish")
    lvls = {f: round((leg_high - f * rng) if long else (leg_low + f * rng), 4) for f in _FIBS}
    band = sorted((lvls[0.62], lvls[0.79]))
    return {"levels": lvls, "zone": [round(band[0], 4), round(band[1], 4)],
            "entry": lvls[0.705]}


def order_block(bars: Sequence[OHLC], side: str) -> Optional[dict]:
    """Last opposing candle (down for a long / up for a short) — the OB zone."""
    long = side.lower() in ("long", "buy", "bullish")
    for i in range(len(bars) - 1, -1, -1):
        o, h, l, c = bars[i]
        if (long and c < o) or (not long and c > o):
            return {"ob_low": round(l, 4), "ob_high": round(h, 4),
                    "ob_mid": round((h + l) / 2, 4)}
    return None


def fair_value_gap(bars: Sequence[OHLC], side: str) -> Optional[dict]:
    """Most recent 3-candle imbalance (gap between candle1 and candle3)."""
    long = side.lower() in ("long", "buy", "bullish")
    for i in range(len(bars) - 2, 1, -1):
        c1, c3 = bars[i - 1], bars[i + 1]
        if long and c1[1] < c3[2]:                 # c1.high < c3.low → bullish gap
            return {"fvg_low": round(c1[1], 4), "fvg_high": round(c3[2], 4)}
        if not long and c1[2] > c3[1]:             # c1.low > c3.high → bearish gap
            return {"fvg_low": round(c3[1], 4), "fvg_high": round(c1[2], 4)}
    return None


def refined_entry(bars: Sequence[OHLC], side: str, buffer: float = 0.0) -> dict:
    """Structure entry for ``side``: OTE 0.705 entry + stop beyond the OB/leg.

    Returns {'ok': False, ...} when there's no BMS in the wanted direction (so the
    caller falls back to the live-price / fixed-pip entry).
    """
    want = "bullish" if side.lower() in ("long", "buy") else "bearish"
    bos = break_of_structure(bars)
    if bos.get("bms") != want:
        return {"ok": False, "reason": f"no {want} BMS yet", "bms": bos.get("bms")}

    ote = ote_zone(bos["leg_low"], bos["leg_high"], side)
    ob = order_block(bars, side)
    fvg = fair_value_gap(bars, side)
    entry = ote["entry"] if ote else None

    if side.lower() in ("long", "buy"):
        stop = bos["leg_low"]
        if ob:
            stop = min(stop, ob["ob_low"])
        stop -= buffer
    else:
        stop = bos["leg_high"]
        if ob:
            stop = max(stop, ob["ob_high"])
        stop += buffer

    return {"ok": True, "bms": bos["bms"], "broke_level": bos["broke_level"],
            "entry": entry, "stop": round(stop, 4),
            "zone": ote["zone"] if ote else None, "ote": ote,
            "order_block": ob, "fvg": fvg}
