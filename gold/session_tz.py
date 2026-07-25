"""Session timezone offset — the broker/chart clock the AMD windows are read in.

Defaults to UTC-4 (the operator's OANDA feed in EDT). If the broker shifts to UTC-5
in winter (EST), set SESSION_TZ_OFFSET=-5 — every session module (venom / bumblebee /
b2b) reads its hour through here, so one env var re-aligns the whole session clock.
"""

from __future__ import annotations

import datetime as _dt

from decouple import config


def tz_offset() -> int:
    """Hours to add to UTC to get the session clock (default -4 = EDT)."""
    try:
        return int(config("SESSION_TZ_OFFSET", default=-4, cast=int))
    except Exception:
        return -4


def now_session() -> _dt.datetime:
    """The current datetime in the session timezone."""
    return _dt.datetime.utcnow() + _dt.timedelta(hours=tz_offset())


def session_hour() -> int:
    return now_session().hour
