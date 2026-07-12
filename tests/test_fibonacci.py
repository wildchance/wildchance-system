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


# ── Trend-based (3-point A→B→C) extension ───────────────────────────────────

def test_trend_extension_projects_from_c():
    # Impulse A=100→B=142.19, pullback to C=125. r=0 sits at C; r=1 a full leg.
    assert fib.trend_extension(100, 142.19, 125, 0.0) == 125
    assert fib.trend_extension(100, 142.19, 125, 1.0) == 167.19   # C + (B-A)
    assert fib.trend_extension(100, 142.19, 125, 2.0) == 209.38


def test_trend_levels_reproduce_gold_chart():
    # The TenCapital gold ladder: anchor C=4025.34, unit leg (B-A)=42.19.
    # 0→4025.34, 1→4067.53, 2→4109.72, 4→4194.10 (chart: 4067.54/4109.73/4194.12).
    a, b, c = 4000.0, 4042.19, 4025.34
    lv = fib.trend_levels(a, b, c)
    assert lv["side"] == "long"
    assert lv["levels"]["1.000"] == 4067.53
    assert lv["levels"]["2.000"] == 4109.72
    assert abs(lv["levels"]["4.236"] - 4204.02) < 0.05
    # negative projection = the downside target zone the chart's arrow points to
    assert fib.trend_extension(a, b, c, -4.16) < 3860


def test_trend_plan_long_targets_the_extensions():
    plan = fib.trend_plan(100, 142.19, 125, buffer=1)
    assert plan["ok"] is True
    assert plan["side"] == "long"
    assert plan["kind"] == "trend-extension"
    assert plan["entry"] == 125                    # enter at the C anchor
    assert plan["stop"] == 124                      # invalidation below C - buffer
    assert plan["targets"][0]["price"] == 167.19    # 1.0 measured move
    assert all(t["price"] > 125 for t in plan["targets"])
    assert plan["zones"]["target"][0] < plan["zones"]["target"][1]


def test_trend_plan_short_projects_down():
    # Down-impulse A=200→B=100, retrace up to C=140. Targets project DOWN.
    plan = fib.trend_plan(200, 100, 140, buffer=2)
    assert plan["side"] == "short"
    assert plan["stop"] == 142                      # above C + buffer
    assert plan["targets"][0]["price"] == 40        # 140 - 1.0*100
    assert all(t["price"] < 140 for t in plan["targets"])


def test_trend_plan_stop_ref_a_widens_stop():
    near = fib.trend_plan(100, 142.19, 125, buffer=1, stop_ref="c")
    far = fib.trend_plan(100, 142.19, 125, buffer=1, stop_ref="a")
    assert far["risk"] > near["risk"]               # A-invalidation is wider
    assert far["stop"] == 99                          # A - buffer


def test_trend_plan_zero_risk_when_stop_at_entry():
    # entry == C and stop at C with no buffer → zero-risk, not ok.
    p = fib.trend_plan(100, 142.19, 125, buffer=0, stop_ref="c")
    assert p["ok"] is False


def test_trend_degenerate_impulse():
    assert fib.trend_plan(100, 100, 100)["ok"] is False
    assert fib.trend_levels(100, 100, 100)["levels"] == {}
