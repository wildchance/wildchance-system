"""Opening-Range engine + TPO profile + session_breakout tier composition."""

import datetime as dt

from indicators import opening_range as orb
from indicators import profile as prof
from gold.trade_types import session_breakout_plan


def _bar(h, o, hi, lo, c, day=7):
    return (dt.datetime(2026, 7, day, h, 0, tzinfo=dt.timezone.utc), o, hi, lo, c)


# ---- Opening Range ---------------------------------------------------------

def test_or_long_breakout_and_retest():
    # NY session opens 13:00 UTC. OR window = the 13:00 bar (or_hours=1).
    bars = [
        _bar(13, 4000, 4010, 3995, 4005),     # OR: high 4010, low 3995
        _bar(14, 4005, 4020, 4004, 4018),     # closes above OR high → long breakout
        _bar(15, 4018, 4019, 4009, 4014),     # low 4009 tags OR high 4010 → retest
        _bar(16, 4014, 4030, 4013, 4028),
    ]
    r = orb.opening_range(bars, session="ny", or_hours=1, buffer=1.0)
    assert r["ok"] is True
    assert r["side"] == "long"
    assert r["or_high"] == 4010 and r["or_low"] == 3995
    assert r["entry"] == 4010                  # retest of the broken boundary
    assert r["stop"] == 3994                   # opposite extreme - buffer
    assert r["retest"] is True


def test_or_no_breakout_is_no_trade():
    bars = [_bar(13, 4000, 4010, 3995, 4005), _bar(14, 4005, 4009, 3996, 4002)]
    r = orb.opening_range(bars, session="ny", or_hours=1)
    assert r["ok"] is False and r["breakout"] is None


def test_or_awaiting_retest():
    bars = [_bar(13, 4000, 4010, 3995, 4005), _bar(14, 4005, 4025, 4011, 4022)]
    r = orb.opening_range(bars, session="ny", or_hours=1, require_retest=True)
    assert r["ok"] is False and r["breakout"] == "long" and r["retest"] is False


def test_or_short_breakout():
    bars = [
        _bar(0, 4000, 4010, 3995, 4002),       # Asia OR
        _bar(1, 4002, 4003, 3980, 3985),       # closes below OR low → short
        _bar(2, 3985, 3996, 3984, 3990),       # high 3996 tags OR low 3995 → retest
    ]
    r = orb.opening_range(bars, session="asia", or_hours=1, buffer=1.0)
    assert r["side"] == "short" and r["entry"] == 3995 and r["stop"] == 4011


# ---- TPO / Market Profile --------------------------------------------------

def test_tpo_poc_and_value_area():
    # price coils tightly around 4000, one probe up — POC should be ~4000
    bars = [
        _bar(0, 4000, 4001, 3999, 4000), _bar(1, 4000, 4001, 3999, 4000),
        _bar(2, 4000, 4002, 3999, 4001), _bar(3, 4000, 4001, 3998, 3999),
        _bar(4, 4001, 4010, 4000, 4008),      # one expansion probe up
    ]
    p = prof.tpo_profile(bars, bin_size=1.0)
    # POC sits in the tight balance zone (4000/4001 dominate the tallies)
    assert p is not None and 3999 <= p["poc"] <= 4002
    va = prof.value_area(p, coverage=0.70)
    assert va["val"] <= va["poc"] <= va["vah"]
    assert va["coverage"] >= 0.70
    # the lone 4008 probe is an outlier the 70% value area excludes
    assert va["vah"] < 4008


def test_breakout_confirmed_leaving_balance():
    bars = [_bar(h, 4000, 4001, 3999, 4000) for h in range(4)]
    p = prof.tpo_profile(bars, bin_size=1.0)
    va = prof.value_area(p)
    poc = va["poc"]
    # POC sits inside an OR of 3998-4002; price 4010 clears VAH → confirmed
    c = prof.breakout_confirmed("long", 4010, va, or_high=4002, or_low=3998)
    assert c["ok"] is True
    # price still inside the value area → not confirmed
    assert prof.breakout_confirmed("long", 4000, va, 4002, 3998)["ok"] is False
    # POC well outside the OR (range isn't the balance) → not confirmed
    assert prof.breakout_confirmed("long", 4010, va, or_high=poc - 5, or_low=poc - 10)["ok"] is False


# ---- session_breakout tier composition -------------------------------------

def _or_ok(side="long"):
    return {"ok": True, "side": side, "entry": 4010, "stop": 3994,
            "or_high": 4010, "or_low": 3995}


def test_session_breakout_fires_when_all_agree():
    plan = session_breakout_plan(_or_ok("long"), {"ok": True, "reason": "leaving balance"},
                                 bias="long", session="ny")
    assert plan["signal"] == "LONG"
    assert plan["trade_type"] == "intraday"        # NY OR → intraday tier
    assert plan["entry"] == 4010 and plan["stop"] == 3994
    assert plan["kind"] == "limit"                 # retest = limit at the boundary


def test_session_breakout_tier_by_session():
    assert session_breakout_plan(_or_ok(), {"ok": True}, "long", "asia")["trade_type"] == "intrasession"
    assert session_breakout_plan(_or_ok(), {"ok": True}, "long", "composite")["trade_type"] == "swing"


def test_session_breakout_blocks_against_bias():
    plan = session_breakout_plan(_or_ok("short"), {"ok": True}, bias="long", session="ny")
    assert plan["signal"] == "NO TRADE" and "opposes weekly" in plan["reason"]


def test_session_breakout_blocks_when_profile_denies():
    plan = session_breakout_plan(_or_ok("long"), {"ok": False, "reason": "inside value area"},
                                 bias="long", session="ny")
    assert plan["signal"] == "NO TRADE" and "value area" in plan["reason"]


def test_session_breakout_blocks_without_breakout():
    plan = session_breakout_plan({"ok": False, "reason": "no breakout"}, {"ok": True},
                                 bias="long", session="ny")
    assert plan["signal"] == "NO TRADE"
