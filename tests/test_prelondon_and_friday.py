"""Pre-London CBDR limits + Friday→Monday gap read."""

from cbdr.engine import build_cbdr, prelondon_limits, DEFAULT_DEVIATIONS, GREY_ZONE_SD
from candlerange.engine import friday_gap_read, weekday_confidence, close_position


# --- pre-London limits ------------------------------------------------------

def test_deviations_include_one_and_a_half():
    assert 1.5 in DEFAULT_DEVIATIONS and GREY_ZONE_SD == (1.0, 1.5)


def test_prelondon_limits_levels():
    box = build_cbdr(110.0, 100.0)          # range 10 → 1SD=10
    plan = prelondon_limits(box)
    by = {o["level"]: o for o in plan["orders"]}
    assert by["-1SD"]["side"] == "long" and by["-1SD"]["entry"] == 90.0   # buy -1SD
    assert by["+1SD"]["side"] == "short" and by["+1SD"]["entry"] == 120.0  # sell +1SD
    assert by["+3SD"]["side"] == "short" and by["+3SD"]["entry"] == 140.0  # sell +3SD extreme


def test_prelondon_grey_zone():
    box = build_cbdr(110.0, 100.0)
    g = prelondon_limits(box)["grey_zone"]
    assert g["sd_band"] == [1.0, 1.5]
    assert g["buy_grey"] == [90.0, 85.0]     # -1SD, -1.5SD
    assert g["sell_grey"] == [120.0, 125.0]  # +1SD, +1.5SD


# --- Friday → Monday gap ----------------------------------------------------

def test_close_position():
    assert close_position(100, 110, 99, 109) == round(10 / 11, 3)


def test_friday_gap_up_on_strong_bull_close():
    r = friday_gap_read({"open": 100, "high": 110, "low": 99, "close": 109})
    assert r["gap_bias"] == "up" and r["strength"] == "strong"


def test_friday_gap_down_on_weak_bear_close():
    r = friday_gap_read({"open": 100, "high": 101, "low": 90, "close": 91})
    assert r["gap_bias"] == "down" and r["strength"] == "strong"


def test_friday_flat_midrange_close():
    r = friday_gap_read({"open": 100, "high": 105, "low": 95, "close": 100.5})
    assert r["gap_bias"] == "flat"


def test_weekday_confidence():
    assert weekday_confidence(4) == "high"     # Friday
    assert weekday_confidence(0) == "medium"   # Monday
    assert weekday_confidence(2) == "low"      # mid-week choppy
