"""Fibonacci structure engine — retracement/OTE/extension math + trade planning."""

from indicators import fibonacci as fib


def test_retracement_endpoints_and_half():
    # Leg from 100 → 200. r=0 is the high, r=1 the low, r=0.5 the midpoint.
    assert fib.retracement(100, 200, 0.0) == 200
    assert fib.retracement(100, 200, 1.0) == 100
    assert fib.retracement(100, 200, 0.5) == 150
    assert fib.retracement(100, 200, 0.705) == 129.5
    # low/high order does not matter
    assert fib.retracement(200, 100, 0.5) == 150


def test_extension_direction():
    # long extends ABOVE the high, short BELOW the low; 1.0 == the extreme itself.
    assert fib.extension(100, 200, 1.0, "long") == 200
    assert fib.extension(100, 200, 1.618, "long") == 261.8
    assert fib.extension(100, 200, 1.0, "short") == 100
    assert fib.extension(100, 200, 1.618, "short") == 38.2


def test_ote_zone_long_is_discount():
    z = fib.ote_zone(100, 200, "long")
    # entry is the 70.5% pullback below the high
    assert z["entry"] == 129.5
    assert z["zone"] == [121.4, 138.2]        # 78.6% .. 61.8%


def test_ote_zone_short_is_premium():
    z = fib.ote_zone(100, 200, "short")
    # short pulls UP into premium: 70.5% of the way up from the low
    assert z["entry"] == 170.5
    assert z["zone"] == [161.8, 178.6]


def test_invalidation_beyond_extreme():
    # long stop below the low; short stop above the high; buffer widens it.
    assert fib.invalidation(100, 200, "long", buffer=0) == 100
    assert fib.invalidation(100, 200, "long", buffer=2) == 98
    assert fib.invalidation(100, 200, "short", buffer=0) == 200
    assert fib.invalidation(100, 200, "short", buffer=2) == 202


def test_plan_short_reversal_like_usdjpy():
    # The chart case: an up-leg being faded short. Swing low 153.779, exhaustion
    # high 163.6. Stop sits ABOVE the exhaustion (invalidation), targets extend
    # DOWN — the wide structural stop the tight-stop fix is all about.
    plan = fib.plan_trade(153.779, 163.6, "short", buffer=0.1)
    assert plan["ok"] is True
    assert plan["side"] == "short"
    assert plan["stop"] == 163.7                     # above exhaustion + buffer
    assert plan["entry"] > 153.779 and plan["entry"] < 163.6
    # first extension target is below the swing low
    assert plan["targets"][0]["price"] < 153.779
    # money-first: risk is the structural distance, first target clears 3R
    assert plan["rr_first"] >= 3.0
    assert plan["stop_distance"] == plan["risk"]


def test_plan_long_targets_above_high():
    plan = fib.plan_trade(100, 200, "long", buffer=1)
    assert plan["side"] == "long"
    assert plan["stop"] == 99                         # below low - buffer
    assert plan["entry"] == 129.5                     # 70.5% OTE
    assert all(t["price"] > 200 for t in plan["targets"])
    assert plan["rr_first"] > 0


def test_plan_given_entry_overrides_ote():
    plan = fib.plan_trade(100, 200, "long", entry=140, buffer=0)
    assert plan["entry"] == 140
    assert plan["entry_mode"] == "given"
    assert plan["risk"] == 40                         # 140 - 100


def test_degenerate_leg_is_not_ok():
    assert fib.plan_trade(150, 150, "long")["ok"] is False
    assert fib.ote_zone(150, 150, "long") is None


def test_levels_has_both_ladders():
    lv = fib.levels(100, 200)
    assert lv["retracements"]["0.705"] == 129.5
    assert lv["extensions"]["1.618"] == 261.8
    assert lv["range"] == 100
