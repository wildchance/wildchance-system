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
