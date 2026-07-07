"""Quarterly Theory — daily session (AMD) model for XAU/USD (pure).

Time is fractal: the trading day splits into four 6-hour quarters that follow the
Accumulation → Manipulation → Distribution → Continuation/Reversal cycle. The
edge is timing: the London manipulation (Q2, the 'Judas swing') sets up the New
York distribution (Q3, the real expansion) — that's the prime intraday window.

Sessions (UTC). Asia is the 8h ACCUMULATION, London the MANIPULATION (Judas /
no-entry — the pre-London CBDR limits sit here), New York the DISTRIBUTION. Entries
fire in Asia + New York; London is setup only.
  Q1  Accumulation           Asia     00:00–08:00   (8h — entries)
  Q2  Manipulation (Judas)   London   08:00–13:00   (setup only, no entries)
  Q3  Distribution           NY       13:00–21:00   (entries)
  Q4  Rollover / close       Sydney   21:00–24:00
"""

from __future__ import annotations

import datetime as dt
from typing import Optional

# (quarter, phase, session, start_hour_utc, end_hour_utc)
_QUARTERS = [
    (1, "accumulation", "Asia", 0, 8),
    (2, "manipulation", "London", 8, 13),
    (3, "distribution", "New York", 13, 21),
    (4, "rollover", "Sydney/close", 21, 24),
]


def session_quarter(now: dt.datetime) -> dict:
    """Which daily quarter/phase/session is live for a UTC datetime."""
    h = now.hour
    for q, phase, sess, a, b in _QUARTERS:
        if a <= h < b:
            return {
                "quarter": q, "phase": phase, "session": sess,
                "window_utc": f"{a:02d}:00-{b:02d}:00",
                "is_distribution": q == 3,
                "is_manipulation": q == 2,
            }
    # h == 24 never happens; fallback to Q4
    q, phase, sess, a, b = _QUARTERS[-1]
    return {"quarter": q, "phase": phase, "session": sess,
            "window_utc": f"{a:02d}:00-24:00", "is_distribution": False,
            "is_manipulation": False}


def is_trade_window(now: dt.datetime, allow_accumulation: bool = True) -> bool:
    """True in an ENTRY session: NY distribution (Q3), or Asia accumulation (Q1).

    London manipulation (Q2) is NOT an entry window — it's the Judas / setup that
    the pre-London CBDR limits cover.
    """
    q = session_quarter(now)["quarter"]
    return q == 3 or (allow_accumulation and q == 1)


def weekday_quarter(now: dt.datetime) -> dict:
    """Weekly Quarterly Theory: Mon=Q1 … Thu=Q4 (Fri = reversal/no-trade)."""
    wd = now.weekday()          # 0=Mon
    m = {0: (1, "accumulation"), 1: (2, "manipulation"),
         2: (3, "distribution"), 3: (4, "continuation_or_reversal")}
    q, phase = m.get(wd, (0, "friday_reversal"))
    return {"weekday": wd, "quarter": q, "phase": phase,
            "tradeable": wd <= 3}     # Mon–Thu
