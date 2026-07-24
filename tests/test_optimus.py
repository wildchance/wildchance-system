"""Optimus Prime — zone-precision locator, reject-gating, 250/2500 capture."""

import pytest

from gold import optimus as gop


def _bar(o, h, l, c, t="d"):
    return (t, o, h, l, c)


def test_locate_bearish_ob_at_supply():
    # an up-close candle sitting in the 4152-4163 supply band = the bearish OB
    zone = {"name": "supply_4152_4163", "lo": 4152.40, "hi": 4163.07}
    bars = [_bar(4140, 4150, 4138, 4148),
            _bar(4150, 4165, 4149, 4160),   # up-close, high 4165 in band → bearish OB
            _bar(4160, 4162, 4090, 4095)]   # displacement down
    ob = gop.locate_ob(bars, zone, "sell")
    assert ob is not None and ob["side"] == "sell"
    assert ob["ob_zone"][1] >= 4163           # wick-high captured
    assert ob["stop"] > ob["ob_zone"][1]      # stop beyond the wick


def test_precision_entry_waits_without_reject():
    # price in the zone but no sweep+reject → WAIT (this is the early-entry guard)
    zone = {"name": "supply_4152_4163", "lo": 4152.40, "hi": 4163.07}
    bars = [_bar(4150, 4158, 4149, 4156), _bar(4156, 4160, 4152, 4158),
            _bar(4158, 4161, 4155, 4159)]     # drifting, no reject close
    pe = gop.precision_entry(bars, zone, "sell")
    assert pe["armed"] is False and "WAIT" in pe["status"]


def test_precision_entry_arms_on_reject():
    # sweep above 4163 then close back below (bearish reject) → ARMED
    zone = {"name": "supply_4152_4163", "lo": 4152.40, "hi": 4163.07}
    bars = [_bar(4150, 4158, 4149, 4156),
            _bar(4156, 4160, 4152, 4158),
            _bar(4160, 4170, 4150, 4155)]     # wick 4170 > 4163, close 4155 back below, bearish
    pe = gop.precision_entry(bars, zone, "sell")
    assert pe["armed"] is True and pe["side"] == "SELL"
    assert pe["entry"] is not None and pe["stop"] is not None


def test_capture_targets_next_zone_and_grades():
    # a sell from 4152/4163 should target a lower buy zone and grade the pip capture
    zone = {"name": "supply_4152_4163", "lo": 4152.40, "hi": 4163.07}
    bars = [_bar(4156, 4160, 4152, 4158), _bar(4158, 4160, 4152, 4157),
            _bar(4160, 4170, 4150, 4155)]
    pe = gop.precision_entry(bars, zone, "sell")
    assert pe["target"] is not None and pe["target"] < pe["entry"]
    assert pe["capture_pips"] and pe["capture_pips"] > 0
    assert "meets_250_floor" in pe


def test_optimus_scan_anticipates_next_zone():
    # price below the bullish mean → sell bias, anticipate the next lower zone
    bars = [_bar(4160, 4170, 4150, 4155), _bar(4155, 4158, 4020, 4030),
            _bar(4030, 4035, 4020, 4028)]
    scan = gop.optimus_scan(bars, 4029.0)
    assert scan["price"] == 4029.0
    assert "sell" in scan["bias_vs_mean"]
    assert scan["next_zone"] is not None       # e.g. ob_3987_4h / shelf / central limit


def test_set_live_zones_updates_map():
    out = gop.set_live_zones(
        sell=[{"name": "x", "lo": 4200, "hi": 4210}],
        buy=[{"name": "y", "lo": 3900, "hi": 3910}],
        pivots={"bullish_mean": 4050})
    assert out["sell"][0]["name"] == "x" and out["pivots"]["bullish_mean"] == 4050
    # restore the default map so other tests/live see the real zones
    gop.set_live_zones(
        sell=[{"name": "supply_4179_4190", "lo": 4179.79, "hi": 4190.60},
              {"name": "supply_4152_4163", "lo": 4152.40, "hi": 4163.07}],
        buy=[{"name": "ob_4001", "lo": 3995.0, "hi": 4001.60},
             {"name": "ob_3987_4h", "lo": 3980.0, "hi": 3994.0},
             {"name": "shelf_3944_3958", "lo": 3944.11, "hi": 3958.50},
             {"name": "central_limit_3885", "lo": 3880.0, "hi": 3888.0}],
        pivots={"bullish_mean": 4133.90})
