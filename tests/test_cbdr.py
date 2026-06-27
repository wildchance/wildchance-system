"""Tests for the CBDR engine — box, SD projections, bias read, and windows."""

import pytest

from cbdr.engine import (
    cbdr_box, build_cbdr, read_bias, nearest_levels,
    WINDOWS, in_window, session_key, _hm,
)


def test_windows_registered():
    assert "cbdr" in WINDOWS and "prelondon" in WINDOWS
    pl = WINDOWS["prelondon"]
    assert pl.tz == "UTC"
    assert pl.interval == "15min"
    assert pl.start_min == _hm(18) and pl.end_min == _hm(2, 45)


def test_in_window_non_wrapping_cbdr():
    w = WINDOWS["cbdr"]
    assert in_window(_hm(14), w.start_min, w.end_min)        # 2pm in
    assert in_window(_hm(19, 59), w.start_min, w.end_min)    # 7:59pm in
    assert not in_window(_hm(20), w.start_min, w.end_min)    # 8pm out (exclusive)
    assert not in_window(_hm(13, 59), w.start_min, w.end_min)


def test_in_window_wrapping_prelondon():
    w = WINDOWS["prelondon"]
    assert in_window(_hm(18), w.start_min, w.end_min)        # 18:00 in
    assert in_window(_hm(23, 45), w.start_min, w.end_min)    # before midnight in
    assert in_window(_hm(0), w.start_min, w.end_min)         # after midnight in
    assert in_window(_hm(2, 30), w.start_min, w.end_min)     # 02:30 bar in
    assert not in_window(_hm(2, 45), w.start_min, w.end_min)  # 02:45 out (exclusive)
    assert not in_window(_hm(12), w.start_min, w.end_min)    # midday out


def test_session_key_wrapping_groups_across_midnight():
    w = WINDOWS["prelondon"]
    # 20:00 on the 24th and 01:00 on the 25th belong to ONE session: the 24th.
    assert session_key("2026-06-24", _hm(20), w.start_min, w.end_min) == "2026-06-24"
    assert session_key("2026-06-25", _hm(1), w.start_min, w.end_min) == "2026-06-24"


def test_session_key_non_wrapping_is_same_day():
    w = WINDOWS["cbdr"]
    assert session_key("2026-06-24", _hm(15), w.start_min, w.end_min) == "2026-06-24"


def test_symmetric_sd_extensions():
    # The whole point of the user's request: buys and sells get equal extensions.
    box = build_cbdr(110.00, 109.00)          # range 1.00
    for n in (1, 2, 3):
        up = box.levels[f"+{n}SD"] - box.high
        down = box.low - box.levels[f"-{n}SD"]
        assert up == pytest.approx(down)      # identical multiples both sides


def test_box_from_bars():
    highs = [157.20, 157.55, 157.40]
    lows = [157.00, 156.85, 157.10]
    high, low = cbdr_box(highs, lows)
    assert high == 157.55
    assert low == 156.85


def test_projection_levels():
    # range = 1.00 → clean multiples
    box = build_cbdr(high=158.00, low=157.00, deviations=(1, 2, 3))
    assert box.range == pytest.approx(1.00)
    assert box.mid == pytest.approx(157.50)
    assert box.levels["+1SD"] == pytest.approx(159.00)
    assert box.levels["+2SD"] == pytest.approx(160.00)
    assert box.levels["+3SD"] == pytest.approx(161.00)
    assert box.levels["-1SD"] == pytest.approx(156.00)
    assert box.levels["-2SD"] == pytest.approx(155.00)


def test_bias_upper_half_floor():
    box = build_cbdr(158.00, 157.00)
    r = read_bias(157.80, box)            # upper half
    assert r["state"] == "bullish_half"
    assert r["key_level"] == pytest.approx(box.levels["-1SD"])  # lower SD = floor


def test_bias_lower_half_ceiling():
    box = build_cbdr(158.00, 157.00)
    r = read_bias(157.20, box)            # lower half
    assert r["state"] == "bearish_half"
    assert r["key_level"] == pytest.approx(box.levels["+1SD"])  # upper SD = ceiling


def test_bias_breakouts():
    box = build_cbdr(158.00, 157.00)
    assert read_bias(159.50, box)["state"] == "breakout_up"
    assert read_bias(156.50, box)["state"] == "breakout_down"


def test_nearest_levels():
    box = build_cbdr(158.00, 157.00)
    near = nearest_levels(159.10, box, n=1)
    assert near[0]["level"] == "+1SD"       # 159.00 is closest to 159.10


def test_invalid_box():
    with pytest.raises(ValueError):
        build_cbdr(157.00, 158.00)          # high < low
    with pytest.raises(ValueError):
        cbdr_box([], [])
