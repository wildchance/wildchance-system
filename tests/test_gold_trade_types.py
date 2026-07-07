"""Gold trade-type tiers: profile→type, structural stops, per-tier horizons."""

import datetime as dt

from gold import trade_types as tt
from gold import position as pos

_Q3 = {"quarter": 3, "phase": "distribution", "session": "NY AM"}
_Q2 = {"quarter": 2, "phase": "manipulation", "session": "London"}


def _prof(pid, bias):
    return {"profile_id": pid, "bias": bias, "week_high": 4300, "week_low": 4000}


# --- classification ---------------------------------------------------------

def test_reversal_profiles_are_swings():
    for pid in (1, 2, 5, 6, 11, 12):
        t = tt.classify_tier(_prof(pid, "long"), _Q3)
        assert t["trade_type"] == "swing" and t["rr"] == (5, 6, 7, 8)


def test_continuation_in_distribution_is_intraday():
    t = tt.classify_tier(_prof(7, "long"), _Q3)
    assert t["trade_type"] == "intraday" and t["rr"] == (2, 3)


def test_continuation_outside_distribution_is_intrasession():
    t = tt.classify_tier(_prof(8, "short"), _Q2)
    assert t["trade_type"] == "intrasession" and t["rr"] == (3, 4, 5)


def test_seek_destroy_and_neutral_stand_aside():
    assert tt.classify_tier(_prof(9, "neutral"), _Q3) is None
    assert tt.classify_tier({"profile_id": None, "bias": "long"}, _Q3) is None


# --- structural stops -------------------------------------------------------

def test_swing_stop_below_weekly_low_for_long():
    ranges = {"weekly": (4300, 4000), "day": (4200, 4100), "session": (4180, 4150)}
    s = tt.tier_stop("weekly", "long", ranges)
    assert s == 4000 - tt.DEFAULT_BUFFER


def test_intraday_stop_above_day_high_for_short():
    ranges = {"weekly": (4300, 4000), "day": (4200, 4100), "session": (4180, 4150)}
    s = tt.tier_stop("day", "short", ranges)
    assert s == 4200 + tt.DEFAULT_BUFFER


def test_stop_none_when_range_missing():
    assert tt.tier_stop("session", "long", {"session": (None, None)}) is None


# --- per-tier horizons ------------------------------------------------------

def test_intraday_deadline_is_same_day_close():
    opened = dt.datetime(2026, 7, 7, 13, 0, tzinfo=dt.timezone.utc)
    d = pos.deadline_for("intraday", opened)
    assert d.date() == opened.date() and d.hour == 21


def test_session_deadline_uses_end_hour():
    opened = dt.datetime(2026, 7, 7, 13, 0, tzinfo=dt.timezone.utc)
    d = pos.deadline_for("intrasession", opened, end_hour_utc=18)
    assert d.hour == 18 and d.date() == opened.date()


def test_swing_deadline_is_monday():
    opened = dt.datetime(2026, 7, 5, 22, 0, tzinfo=dt.timezone.utc)  # Sunday
    d = pos.deadline_for("swing", opened)
    assert d.weekday() == 0 and d.hour == 22


def test_evaluate_honours_explicit_deadline():
    opened = dt.datetime(2026, 7, 7, 13, 0, tzinfo=dt.timezone.utc)
    deadline = pos.deadline_for("intraday", opened)          # same day 21:00
    state = {"side": "long", "entry": 4100, "stop": 4080,
             "targets": [4140, 4160], "be_active": False,
             "opened_at": opened, "deadline": deadline, "trade_type": "intraday"}
    # before the intraday close → holding
    assert pos.evaluate(state, 4110, opened + dt.timedelta(hours=1))["close"] is False
    # after the intraday close → time-stopped at market
    a = pos.evaluate(state, 4110, deadline + dt.timedelta(minutes=1))
    assert a["close"] and a["exit_reason"] == "TIME"
