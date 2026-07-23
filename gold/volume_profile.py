"""Volume/TPO profile (B2) — POC / VAH / VAL from the price distribution.

True volume-at-price needs a volume feed we don't have, so this builds a Market-
Profile TPO (Time-Price-Opportunity) distribution from OHLC — each bar spreads one
unit of "time" across the price cells it traded through — which yields POC/VAH/VAL
that closely track the volume version for a liquid market. If bars DO carry volume
(OHLCV), it weights by real volume automatically. Honest about which it used.

  • POC — Point of Control: the most-traded price (the magnet).
  • VAH / VAL — Value Area High/Low: the band holding ``va_pct`` (default 70%) of
    the distribution around the POC (the fair-value range; edges are the fade/break
    lines).
"""

from __future__ import annotations

from typing import List, Optional, Sequence


def _ohlcv(bar):
    """(o,h,l,c,volume) — volume=None when absent (dict/tuple both handled)."""
    if isinstance(bar, dict):
        return (float(bar["open"]), float(bar["high"]), float(bar["low"]),
                float(bar["close"]), (float(bar["volume"]) if bar.get("volume") else None))
    if len(bar) >= 6:                       # (t,o,h,l,c,v)
        return (float(bar[1]), float(bar[2]), float(bar[3]), float(bar[4]),
                float(bar[5]) if bar[5] else None)
    if len(bar) == 5:                       # (t,o,h,l,c) — no volume
        return (float(bar[1]), float(bar[2]), float(bar[3]), float(bar[4]), None)
    return (float(bar[0]), float(bar[1]), float(bar[2]), float(bar[3]), None)


def volume_profile(bars: Sequence, bins: int = 30, va_pct: float = 0.70) -> dict:
    """POC/VAH/VAL from a TPO (or volume-weighted) price histogram."""
    if not bars or len(bars) < 3:
        return {"status": "need >=3 bars"}
    rows = [_ohlcv(b) for b in bars]
    lo = min(r[2] for r in rows)
    hi = max(r[1] for r in rows)
    if hi <= lo:
        return {"status": "degenerate range"}
    has_vol = any(r[4] is not None for r in rows)
    width = (hi - lo) / bins
    hist = [0.0] * bins

    for o, h, l, c, v in rows:
        weight = v if (has_vol and v is not None) else 1.0
        b_lo = max(0, min(bins - 1, int((l - lo) / width)))
        b_hi = max(0, min(bins - 1, int((h - lo) / width)))
        span = b_hi - b_lo + 1
        share = weight / span
        for i in range(b_lo, b_hi + 1):
            hist[i] += share

    poc_idx = max(range(bins), key=lambda i: hist[i])
    poc = round(lo + (poc_idx + 0.5) * width, 2)

    total = sum(hist)
    target = total * va_pct
    lo_i = hi_i = poc_idx
    acc = hist[poc_idx]
    while acc < target and (lo_i > 0 or hi_i < bins - 1):
        left = hist[lo_i - 1] if lo_i > 0 else -1.0
        right = hist[hi_i + 1] if hi_i < bins - 1 else -1.0
        if right >= left:
            hi_i += 1
            acc += hist[hi_i]
        else:
            lo_i -= 1
            acc += hist[lo_i]
    val = round(lo + lo_i * width, 2)
    vah = round(lo + (hi_i + 1) * width, 2)

    return {
        "source": "volume" if has_vol else "TPO (time-at-price, no volume feed)",
        "poc": poc, "vah": vah, "val": val,
        "range": [round(lo, 2), round(hi, 2)], "bins": bins, "va_pct": va_pct,
        "note": (f"POC {poc} (magnet); value area {val}–{vah}. "
                 "Above VAH = premium/fade or breakout; below VAL = discount/reload."),
    }


def profile_read(bars: Sequence, price: Optional[float] = None, bins: int = 30) -> dict:
    """Profile + where the live price sits relative to POC / value area."""
    vp = volume_profile(bars, bins)
    if vp.get("poc") is None:
        return vp
    if price is None:
        price = _ohlcv(bars[-1])[3]
    price = float(price)
    if price > vp["vah"]:
        loc = "above_value"      # premium — fade back to VAH or accept breakout
    elif price < vp["val"]:
        loc = "below_value"      # discount — reload toward VAL/POC
    else:
        loc = "in_value"         # fair — rotate between VAL/VAH
    vp["price"] = round(price, 2)
    vp["location"] = loc
    vp["vs_poc"] = "above" if price > vp["poc"] else "below"
    return vp
