"""Tests for the setup-zone builder + CRT mapping."""

import pytest

from cbdr.engine import build_cbdr
from setups.engine import build_setup, crt_to_setup


def _box():
    # range 1.00 → +1SD 11.0, +2SD 12.0, +2.5SD 12.5, +4SD 14.0 ; mid 10.5
    return build_cbdr(11.0, 10.0)


def test_long_continuation_tps_on_sds():
    box = _box()
    s = build_setup(box, "long", entry=11.0, mode="continuation")
    prices = [t["price"] for t in s["targets"]]
    assert prices == [12.0, 13.0, 15.0]        # +1,+2,+4 SD from build_cbdr
    assert s["stop_loss"] == pytest.approx(10.0 - 0.5)   # box.low - 0.5*range
    assert s["side"] == "long" and len(s["targets"]) == 3


def test_short_continuation_tps_downward():
    box = _box()
    s = build_setup(box, "short", entry=10.0, mode="continuation")
    prices = [t["price"] for t in s["targets"]]
    assert prices == [9.0, 8.0, 6.0]           # -1,-2,-4 SD
    assert s["stop_loss"] == pytest.approx(11.0 + 0.5)


def test_r_multiples_present_and_positive():
    box = _box()
    s = build_setup(box, "long", entry=11.0, mode="continuation")
    for t in s["targets"]:
        assert t["r"] is not None and t["r"] > 0
    # further targets have bigger R
    rs = [t["r"] for t in s["targets"]]
    assert rs[0] < rs[1] < rs[2]


def test_reversal_long_targets_back_toward_mean():
    box = _box()
    # bought a low extreme at 9.5, expect targets back up toward mid/high
    s = build_setup(box, "long", entry=9.5, mode="reversal")
    prices = [t["price"] for t in s["targets"]]
    assert prices[0] == pytest.approx(box.mid)   # TP1 = mid
    assert s["stop_loss"] < 9.5                   # stop below the extreme


def test_zero_range_raises():
    box = build_cbdr(10.0, 10.0)        # range 0
    with pytest.raises(ValueError):
        build_setup(box, "long", 10.0)


def test_crt_mapping():
    # bullish 4h candle that continues → long; that reverses → short
    assert crt_to_setup("bullish", "continuation") == {"side": "long", "mode": "continuation"}
    assert crt_to_setup("bullish", "reversal") == {"side": "short", "mode": "reversal"}
    assert crt_to_setup("bearish", "continuation") == {"side": "short", "mode": "continuation"}
    assert crt_to_setup("bearish", "reversal") == {"side": "long", "mode": "reversal"}
    assert crt_to_setup("bullish", "inside") is None
