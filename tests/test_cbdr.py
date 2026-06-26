"""Tests for the CBDR engine — box, SD projections, and bias read."""

import pytest

from cbdr.engine import cbdr_box, build_cbdr, read_bias, nearest_levels


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
