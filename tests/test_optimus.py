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


def test_target_ladder_maps_void():
    # selling from ~4029: floors cascade 4001→3987→3958→3885, then a VOID to 3506
    lad = gop.target_ladder("sell", 4029.0)
    names = [r["zone"] for r in lad["ladder"]]
    assert "central_limit_3885" in names and "weekly_buy_3506" in names
    # the 3885→3506 leg is flagged as a void (no floor between)
    void_rows = [r for r in lad["ladder"] if r["void_before"]]
    assert any(r["zone"] == "weekly_buy_3506" for r in void_rows)
    # disciplined last floor before the void is 3885
    assert lad["last_floor"]["zone"] == "central_limit_3885"


def test_scan_includes_target_ladder():
    def b(o, h, l, c):
        return ("d", o, h, l, c)
    bars = [b(4160, 4170, 4150, 4155), b(4155, 4158, 4020, 4030), b(4030, 4035, 4020, 4029)]
    scan = gop.optimus_scan(bars, 4029.0)
    assert "target_ladder" in scan and scan["last_floor"]["zone"] == "central_limit_3885"


def test_sell_limit_ladder_pinpoints_retests():
    lad = gop.sell_limit_ladder(4029.0)
    levels = [r["sell_limit"] for r in lad["sell_limits"]]
    # the break-retest premium levels are all mapped as sell-limits
    assert 4163.07 in levels and 4074.86 in levels and 4002.31 in levels
    # each has a stop above and a target below (proper sell-limit geometry)
    for r in lad["sell_limits"]:
        assert r["stop"] > r["sell_limit"] >= r["target"]
        assert r["rr"] > 0


def test_sell_limit_cbdr_confluence():
    from cbdr.engine import build_cbdr
    box = build_cbdr(4165.0, 4145.0)          # +1SD ~4185, tags a premium level
    lad = gop.sell_limit_ladder(4150.0, box)
    assert any(r.get("cbdr") for r in lad["sell_limits"])   # at least one aligns


def test_campaign_projection_journaling():
    cp = gop.campaign_projection(4029.0)
    assert cp["macro_legs_250usd"] == 4
    assert cp["micro_tiers"]["50usd"]["min"] == 60
    assert cp["micro_tiers"]["150usd"]["max"] == 48
    assert cp["progress_pct"] is not None and cp["progress_pct"] > 0


def test_bounce_plan_ob_targets():
    # price bouncing up from 4060 → targets the 4074 daily OB then 4133/4135 4H OB
    bp = gop.bounce_plan(4060.0)
    levels = [t["level"] for t in bp["buy_targets"]]
    assert 4074.86 in levels
    daily = next(t for t in bp["buy_targets"] if t["level"] == 4074.86)
    assert "Daily order block" in (daily["ob"] or "")
    # each buy target carries the sell re-arm (buy the bounce, sell the OB)
    assert daily["sell_rearm"]["target"] < daily["level"]
    assert "counter-trend bounce" in bp["bias"]


def test_sell_limit_ladder_tags_ob_timeframe():
    lad = gop.sell_limit_ladder(4029.0)
    tagged = {r["sell_limit"]: r["ob_timeframe"] for r in lad["sell_limits"]}
    assert "Daily order block" in (tagged.get(4074.86) or "")
    assert "4H order block" in (tagged.get(4133.90) or "")


def test_set_fib_map_updates():
    out = gop.set_fib_map(bullish_mean=4200.0)
    assert out["bullish_mean"] == 4200.0
    gop.set_fib_map(bullish_mean=4133.90)     # restore


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
             {"name": "central_limit_3885", "lo": 3880.0, "hi": 3888.0},
             {"name": "weekly_buy_3506", "lo": 3500.0, "hi": 3512.0},
             {"name": "macro_buy_3291", "lo": 3285.0, "hi": 3298.0}],
        pivots={"bullish_mean": 4133.90})
