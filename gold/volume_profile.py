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


# --- node structure: HVN (magnets) / LVN (voids) ----------------------------------
def _histogram(bars: Sequence, bins: int = 30):
    """The shared TPO/volume histogram: (lo, hi, width, hist, has_vol) or None."""
    if not bars or len(bars) < 3:
        return None
    rows = [_ohlcv(b) for b in bars]
    lo = min(r[2] for r in rows)
    hi = max(r[1] for r in rows)
    if hi <= lo:
        return None
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
    return lo, hi, width, hist, has_vol


def nodes(bars: Sequence, bins: int = 30, hvn_mult: float = 1.15,
          lvn_mult: float = 0.70, max_each: int = 5) -> dict:
    """Surface the profile's node structure — the piece we used to throw away.

      • HVN (High-Volume Node) — a histogram PEAK: a price the market accepted and
        keeps returning to. A MAGNET: price stalls here → bank partials INTO HVNs.
      • LVN (Low-Volume Node) — a histogram VALLEY between peaks: a price the market
        rejected. A VOID: price travels FAST through it → run the runner THROUGH LVNs.
    """
    H = _histogram(bars, bins)
    if H is None:
        return {"hvn": [], "lvn": [], "hvn_detail": [], "lvn_detail": []}
    lo, hi, width, hist, has_vol = H
    avg = sum(hist) / len(hist) if hist else 0.0
    price = lambda i: round(lo + (i + 0.5) * width, 2)
    hvn, lvn = [], []
    for i in range(len(hist)):
        left = hist[i - 1] if i > 0 else -1.0
        right = hist[i + 1] if i < len(hist) - 1 else -1.0
        if hist[i] >= left and hist[i] >= right and hist[i] >= avg * hvn_mult:
            hvn.append({"price": price(i), "weight": round(hist[i], 2)})
        # LVN = an interior valley (>0 excludes the empty tails beyond the range)
        elif hist[i] <= left and hist[i] <= right and 0 < hist[i] <= avg * lvn_mult:
            lvn.append({"price": price(i), "weight": round(hist[i], 2)})
    hvn = sorted(hvn, key=lambda x: -x["weight"])[:max_each]
    lvn = sorted(lvn, key=lambda x: x["weight"])[:max_each]
    return {
        "hvn": sorted(n["price"] for n in hvn),
        "lvn": sorted(n["price"] for n in lvn),
        "hvn_detail": sorted(hvn, key=lambda x: x["price"]),
        "lvn_detail": sorted(lvn, key=lambda x: x["price"]),
        "avg": round(avg, 2), "source": "volume" if has_vol else "TPO",
    }


def scenario(bars: Sequence, price: Optional[float] = None, bins: int = 30) -> dict:
    """Classify the graphic's BULLISH / BEARISH anatomy:

      • BULLISH — price ABOVE the value area with the POC LEFT BELOW it (accumulation
        in value → markup out of it). Best-BUY posture.
      • BEARISH — price BELOW value with the POC ABOVE (distribution → markdown).
      • NEUTRAL — inside value: rotation between VAL and VAH, no edge.
    """
    vp = profile_read(bars, price, bins)
    if vp.get("poc") is None:
        return vp
    p, poc = vp["price"], vp["poc"]
    if vp["location"] == "above_value" and poc < p:
        s, note = "bullish", "price accepted ABOVE value, POC left below — markup (best-buy)"
    elif vp["location"] == "below_value" and poc > p:
        s, note = "bearish", "price accepted BELOW value, POC left above — markdown (best-sell)"
    else:
        s, note = "neutral", "inside value — rotation between VAL/VAH, no directional edge"
    nd = nodes(bars, bins)
    vp.update({"scenario": s, "scenario_note": note, "nodes": nd})
    return vp


# --- Asian-range-anchored profile + the London/NY breakout play -------------------
def _session_hour(bar, tz_offset: int = 0):
    """Session-clock hour of a bar. dict 'hour' is taken as an EXPLICIT session hour;
    a timestamp string (dict 'time' or tuple[0]) is treated as UTC and shifted by
    ``tz_offset``; a small int in tuple[0] is treated as an already-session hour."""
    raw = None
    if isinstance(bar, dict):
        if bar.get("hour") is not None:
            try:
                return int(bar["hour"]) % 24
            except (TypeError, ValueError):
                return None
        raw = bar.get("time")
    else:
        raw = bar[0]
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)) and 0 <= raw <= 23 and float(raw).is_integer():
        return int(raw) % 24
    try:                                        # parse "YYYY-MM-DD HH:.." / "..THH:.."
        s = str(raw)
        hh = int(s[11:13]) if len(s) >= 13 else int(s[:2])
        return (hh + tz_offset) % 24
    except (ValueError, IndexError):
        return None


def asian_profile(bars: Sequence, window=(14, 20), tz_offset: Optional[int] = None,
                  bins: int = 24) -> dict:
    """Build the profile from ONLY the Asian-session bars (the accumulation window —
    14:00–20:00 broker/UTC-4 by default, the system's CBDR box). Returns its POC/VAH/VAL
    plus HVN/LVN node structure — the value area London/NY then breaks out of."""
    if tz_offset is None:
        try:
            from gold.session_tz import tz_offset as _tz
            tz_offset = _tz()
        except Exception:
            tz_offset = 0
    lo_h, hi_h = window
    asian = [b for b in bars
             if (lambda h: h is not None and lo_h <= h < hi_h)(_session_hour(b, tz_offset))]
    if len(asian) < 3:
        return {"ok": False, "reason": "not enough Asian-session bars",
                "asian_bars": len(asian), "window": list(window)}
    vp = volume_profile(asian, bins)
    nd = nodes(asian, bins)
    return {"ok": True, "window": list(window), "asian_bars": len(asian),
            "poc": vp["poc"], "vah": vp["vah"], "val": vp["val"],
            "range": vp["range"], "nodes": nd,
            "note": (f"Asian value {vp['val']}–{vp['vah']}, POC {vp['poc']}. "
                     "London/NY breaking ABOVE VAH = buy scenario; below VAL = sell.")}


def asian_breakout(bars: Sequence, price: Optional[float] = None,
                   window=(14, 20), tz_offset: Optional[int] = None,
                   bins: int = 24) -> dict:
    """The graphic's setup: profile the Asian range, then read the London/NY break of its
    value area. Above VAH → BUY (retest VAH, target the LVN gap then the next HVN above);
    below VAL → SELL (retest VAL, target the LVN gap then the next HVN below). Inside value
    → WAIT. Entry is the value edge (the retest), stop beyond the Asian POC."""
    ap = asian_profile(bars, window, tz_offset, bins)
    if not ap.get("ok"):
        return {"armed": False, "status": "WAIT", "reason": ap.get("reason"), "profile": ap}
    if price is None:
        price = _ohlcv(bars[-1])[3]
    price = float(price)
    vah, val, poc = ap["vah"], ap["val"], ap["poc"]
    hvn, lvn = ap["nodes"]["hvn"], ap["nodes"]["lvn"]

    def _beyond(levels, ref, above):
        picks = [x for x in levels if (x > ref if above else x < ref)]
        return (min(picks) if above else max(picks)) if picks else None

    if price > vah:
        side = "BUY"
        entry, stop = vah, round(poc, 2)
        gap = _beyond(lvn, vah, True)                 # the void it accelerates through
        target = _beyond(hvn, gap if gap else vah, True) or round(vah + (vah - val), 2)
    elif price < val:
        side = "SELL"
        entry, stop = val, round(poc, 2)
        gap = _beyond(lvn, val, False)
        target = _beyond(hvn, gap if gap else val, False) or round(val - (vah - val), 2)
    else:
        return {"armed": False, "status": "WAIT — price inside Asian value",
                "side": None, "profile": ap, "price": round(price, 2)}

    risk = abs(entry - stop)
    reward = abs(target - entry)
    return {
        "armed": True, "status": f"{side} — London/NY broke the Asian value area",
        "side": side, "entry": round(entry, 2), "stop": stop, "target": round(target, 2),
        "lvn_gap": gap, "risk": round(risk, 2), "reward": round(reward, 2),
        "rr": round(reward / risk, 2) if risk else None,
        "price": round(price, 2), "profile": ap,
        "note": (f"{side} the retest of Asian {'VAH' if side=='BUY' else 'VAL'} {entry}; "
                 f"accelerate through LVN {gap}; magnet target HVN {target}."),
    }
