"""Hands-off session hold — exit at the next pre-London CBDR deviation level."""

from gold.position import prelondon_exit, HOLD_TYPES
from cbdr.engine import build_cbdr


def _levels(high, low):
    return build_cbdr(high, low).levels


def test_long_exits_at_prelondon_plus_1sd():
    lv = _levels(4020.0, 4000.0)              # 1SD=20 → +1SD=4040, +1.5SD=4050
    st = {"side": "long", "entry": 4004.0, "stop_initial": 3990.0}
    # price tags the +1SD sell-limit → close there (the 4044-style TP)
    ex = prelondon_exit(st, lv, 4041.0)
    assert ex["close"] is True and ex["exit_reason"] == "PRELONDON+1SD"
    assert ex["exit_price"] == 4040.0 and ex["result_r"] > 0


def test_long_exits_deeper_at_plus_1_5sd():
    lv = _levels(4020.0, 4000.0)
    st = {"side": "long", "entry": 4004.0, "stop_initial": 3990.0}
    ex = prelondon_exit(st, lv, 4055.0)       # tags +1.5SD
    assert ex["exit_reason"] == "PRELONDON+1.5SD" and ex["exit_price"] == 4050.0


def test_short_exits_at_prelondon_minus_1sd():
    lv = _levels(4020.0, 4000.0)              # -1SD=3980, -1.5SD=3970
    st = {"side": "short", "entry": 4015.0, "stop_initial": 4030.0}
    ex = prelondon_exit(st, lv, 3979.0)
    assert ex["exit_reason"] == "PRELONDON-1SD" and ex["exit_price"] == 3980.0
    assert ex["result_r"] > 0


def test_no_exit_before_the_level():
    lv = _levels(4020.0, 4000.0)
    st = {"side": "long", "entry": 4004.0, "stop_initial": 3990.0}
    assert prelondon_exit(st, lv, 4030.0) is None       # not yet at +1SD


def test_no_levels_is_noop():
    st = {"side": "long", "entry": 4004.0, "stop_initial": 3990.0}
    assert prelondon_exit(st, {}, 4100.0) is None


def test_hold_types_cover_the_session_tiers():
    assert set(HOLD_TYPES) == {"swing", "sniper", "prelondon", "crt"}
