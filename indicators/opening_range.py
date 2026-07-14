"""Opening-Range breakout engine — session OR, break, RETEST entry, structural stop.

Pure & timeframe-agnostic. Feed it a single session-day's timestamped OHLC bars
(M5/M15/H1) and the session's opening window; it returns the opening range, the
breakout direction, and the **retest** entry — not the raw break, because the
retest of the OR boundary is what dodges the session-open fakeout. The stop sits
at the OPPOSITE OR extreme, so the plan drops straight into ``build_orders``
scale-out and the fib trend-TP ladder.

Bars are ``(date, open, high, low, close)``, oldest-first. ``date`` may be a
datetime or an ISO string — only its UTC hour is read to slice the OR window.

Sessions (UTC, matching Quarterly Theory):
  asia / tokyo  00:00   ·  london  08:00   ·  ny / newyork  13:00

  range_of(bars)                          hi/lo/mid of a bar slice
  opening_bars(bars, start_hour, hours)   the bars inside the OR window
  opening_range(bars, session, …)         full OR + breakout + retest + stop
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

Bar = Tuple[object, float, float, float, float]

# Session → opening hour (UTC). Asia/Tokyo, London, New York.
SESSIONS = {"asia": 0, "tokyo": 0, "london": 8, "ny": 13, "newyork": 13, "new_york": 13}


def _hour(ts) -> Optional[int]:
    """UTC hour of a bar timestamp (datetime or 'YYYY-MM-DD HH:MM[:SS]' string)."""
    if hasattr(ts, "hour"):
        return ts.hour
    try:
        s = str(ts).replace("T", " ")
        return int(s.split(" ")[1][:2])
    except (IndexError, ValueError):
        return None


def range_of(bars: Sequence[Bar]) -> Optional[dict]:
    """High/low/mid of a slice of bars, or None if empty."""
    if not bars:
        return None
    hi = max(b[2] for b in bars)
    lo = min(b[3] for b in bars)
    return {"high": round(hi, 3), "low": round(lo, 3),
            "mid": round((hi + lo) / 2.0, 3), "bars": len(bars)}


def opening_bars(bars: Sequence[Bar], start_hour: int, or_hours: int = 1) -> List[Bar]:
    """The bars whose UTC hour is in ``[start_hour, start_hour+or_hours)``."""
    end = start_hour + or_hours
    return [b for b in bars if (_hour(b[0]) is not None and start_hour <= _hour(b[0]) < end)]


def opening_range(bars: Sequence[Bar], session: str = "ny", or_hours: int = 1,
                  start_hour: Optional[int] = None, buffer: float = 0.0,
                  require_retest: bool = True) -> dict:
    """The opening-range breakout read for ONE session day.

    Steps: (1) build the OR from the opening window; (2) find the first bar to
    CLOSE beyond the OR (the breakout); (3) require a RETEST — a later bar that
    trades back to the broken boundary (within ``buffer``). Entry is the boundary
    (retest fill), stop is the opposite OR extreme.

    Returns ``ok`` True only when a breakout (and, if required, its retest) is
    present. NO TRADE (not an error) otherwise, with a ``reason``.
    """
    sh = start_hour if start_hour is not None else SESSIONS.get(session.lower())
    if sh is None:
        return {"ok": False, "reason": f"unknown session {session!r}"}

    ob = opening_bars(bars, sh, or_hours)
    orng = range_of(ob)
    if not orng or orng["high"] <= orng["low"]:
        return {"ok": False, "reason": "no opening range yet", "session": session}
    or_high, or_low = orng["high"], orng["low"]

    end = sh + or_hours
    post = [b for b in bars if (_hour(b[0]) is not None and _hour(b[0]) >= end)]

    breakout = None          # "long" | "short"
    broke_idx = None
    for i, b in enumerate(post):
        close = b[4]
        if close > or_high:
            breakout, broke_idx = "long", i
            break
        if close < or_low:
            breakout, broke_idx = "short", i
            break

    base = {"session": session, "or_high": or_high, "or_low": or_low,
            "or_mid": orng["mid"], "or_bars": orng["bars"]}
    if breakout is None:
        return {**base, "ok": False, "breakout": None,
                "reason": "inside the opening range — no breakout close yet"}

    # Retest: after the breakout bar, a bar returns to the broken boundary.
    level = or_high if breakout == "long" else or_low
    retest = False
    for b in post[broke_idx + 1:]:
        if breakout == "long" and b[3] <= level + buffer:      # low tags OR high
            retest = True
            break
        if breakout == "short" and b[2] >= level - buffer:     # high tags OR low
            retest = True
            break

    entry = round(level, 3)
    stop = round((or_low - buffer) if breakout == "long" else (or_high + buffer), 3)
    out = {**base, "breakout": breakout, "retest": retest,
           "entry": entry, "stop": stop,
           "side": "long" if breakout == "long" else "short"}
    if require_retest and not retest:
        out["ok"] = False
        out["reason"] = f"{breakout} breakout — awaiting retest of {level}"
        return out
    out["ok"] = True
    out["reason"] = (f"{breakout} OR breakout"
                     + (" + retest" if retest else " (no retest required)"))
    return out
