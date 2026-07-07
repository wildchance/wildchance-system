"""Session liquidity + protraction (manipulation) detection (pure, stdlib-only).

The intraday edge the chart shows: at a session open the price *protracts* — sweeps
a session extreme (the liquidity), then reverses back inside — and then runs to the
OPPOSITE liquidity within the day's CBDR range. This module reads the 8-hour session
range and detects that sweep+reversal, returning the direction and the opposite
liquidity target.

Bars are (date, open, high, low, close), oldest-first (use H1 or M15 intraday bars).
"""

from __future__ import annotations

from typing import Optional, Sequence, Tuple

OHLCBar = Tuple[object, float, float, float, float]


def eight_hour_range(bars: Sequence[OHLCBar], hours: int = 8) -> Optional[dict]:
    """High/low/mid of the last ``hours`` of bars (the 8-hour session range)."""
    if not bars:
        return None
    window = bars[-hours:] if len(bars) >= hours else list(bars)
    high = max(b[2] for b in window)
    low = min(b[3] for b in window)
    return {"high": round(high, 3), "low": round(low, 3),
            "mid": round((high + low) / 2.0, 3), "bars": len(window)}


def detect_protraction(bars: Sequence[OHLCBar], high: float, low: float,
                       lookback: int = 6) -> dict:
    """Did price sweep a session extreme and reverse (the manipulation / Judas)?

    Swept the high then closed back below it → reversal DOWN (short bias, target the
    low). Swept the low then reclaimed it → reversal UP (long, target the high).
    Both sides swept = Seek & Destroy, no clean protraction.
    """
    if not bars:
        return {"swept": None, "direction": None, "note": "no bars"}
    recent = bars[-lookback:] if len(bars) >= lookback else list(bars)
    last_close = recent[-1][4]
    swept_high = any(b[2] > high for b in recent) and last_close < high
    swept_low = any(b[3] < low for b in recent) and last_close > low

    if swept_high and swept_low:
        return {"swept": "both", "direction": None, "swept_level": None, "target": None,
                "note": "both sides swept — Seek & Destroy, no clean protraction"}
    if swept_high:
        return {"swept": "high", "direction": "short", "swept_level": high, "target": low,
                "note": "swept the session high and reversed → short, target the low"}
    if swept_low:
        return {"swept": "low", "direction": "long", "swept_level": low, "target": high,
                "note": "swept the session low and reclaimed → long, target the high"}
    return {"swept": None, "direction": None, "swept_level": None, "target": None,
            "note": "no session sweep+reversal yet"}


def most_recent_at_hour(hourly: Sequence[dict], hour: int) -> Optional[dict]:
    """High/low of the most recent hourly bar at ``hour`` (e.g. 1 = 1am, 7 = 7am).

    ``hourly`` = [{date, hour, high, low}] (from ohlc_service.fetch_hourly_raw).
    """
    best = None
    for b in hourly:
        if b.get("hour") == hour:
            if best is None or (b["date"], b["hour"]) >= (best["date"], best["hour"]):
                best = b
    if best is None:
        return None
    return {"high": round(best["high"], 3), "low": round(best["low"], 3), "date": best["date"]}


def prior_day_hl(daily: Sequence) -> Optional[dict]:
    """Previous COMPLETED day's high/low (PDH/PDL). daily = (date,o,h,l,c) oldest-first."""
    if len(daily) < 2:
        return None
    d = daily[-2]                       # -1 is today (forming), -2 the prior day
    return {"high": round(d[2], 3), "low": round(d[3], 3)}


def prior_week_hl(daily: Sequence) -> Optional[dict]:
    """Previous ISO-week high/low (PWH/PWL) from daily bars."""
    import datetime as _dt
    if not daily:
        return None
    cur = _dt.date.fromisoformat(str(daily[-1][0])[:10]).isocalendar()[:2]
    prev_week = [b for b in daily
                 if _dt.date.fromisoformat(str(b[0])[:10]).isocalendar()[:2] < cur]
    if not prev_week:
        return None
    last_wk = max(_dt.date.fromisoformat(str(b[0])[:10]).isocalendar()[:2] for b in prev_week)
    bars = [b for b in prev_week
            if _dt.date.fromisoformat(str(b[0])[:10]).isocalendar()[:2] == last_wk]
    return {"high": round(max(b[2] for b in bars), 3), "low": round(min(b[3] for b in bars), 3)}


def build_liquidity(hourly: Sequence[dict], daily: Sequence,
                    hours=(1, 7)) -> dict:
    """The full session-liquidity map the chart draws: 1am/7am highs-lows, the
    8-hour range, PDH/PDL and PWH/PWL. Missing pieces are simply omitted."""
    out: dict = {}
    labels = {1: "1am", 7: "7am"}
    for h in hours:
        lv = most_recent_at_hour(hourly, h)
        if lv:
            out[labels.get(h, f"{h:02d}00")] = lv
    e8 = eight_hour_range([(b["date"], b["open"], b["high"], b["low"], b["close"])
                           for b in hourly]) if hourly else None
    if e8:
        out["8h"] = e8
    pd = prior_day_hl(daily)
    if pd:
        out["PDH"], out["PDL"] = pd["high"], pd["low"]
    pw = prior_week_hl(daily)
    if pw:
        out["PWH"], out["PWL"] = pw["high"], pw["low"]
    return out


def _flat_levels(levels: dict) -> list:
    """Flatten the liquidity map into [(name, price)] for target selection."""
    out = []
    for k, v in (levels or {}).items():
        if isinstance(v, dict):
            if "high" in v:
                out.append((f"{k}H", v["high"]))
            if "low" in v:
                out.append((f"{k}L", v["low"]))
        elif isinstance(v, (int, float)):
            out.append((k, float(v)))
    return out


def opposite_liquidity(direction: str, entry: float, levels: dict) -> Optional[dict]:
    """The nearest liquidity on the target side (above for a long, below for a
    short) from the full map — the draw-on-liquidity target."""
    pts = _flat_levels(levels)
    long = direction.lower() in ("long", "buy")
    side = [(n, p) for (n, p) in pts if (p > entry if long else p < entry)]
    if not side:
        return None
    n, p = min(side, key=lambda np: abs(np[1] - entry))
    return {"level": n, "price": round(p, 3)}


def protraction_gate(side: str, protraction: dict) -> dict:
    """Does a proposed side line up with the detected protraction? Returns the
    opposite-liquidity target when it does."""
    want = "long" if side.lower() in ("long", "buy") else "short"
    d = (protraction or {}).get("direction")
    if d is None:
        return {"ok": False, "target": None,
                "reason": "no session protraction (sweep+reversal) yet — wait for the manipulation"}
    if d == want:
        return {"ok": True, "target": protraction.get("target"),
                "reason": (f"protraction confirms {want}: swept the {protraction['swept']}, "
                           f"target the opposite liquidity {protraction.get('target')}")}
    return {"ok": False, "target": None,
            "reason": f"protraction points {d}, not {want} — stand aside"}
