import math

from gold.risk_engine import (
    lot_ladder, move_for_target, size_for_risk, targets, breakeven_price,
    prop_pass_math, usd_per_point, GOLD_USD_PER_POINT,
)


def test_lot_ladder_matches_handwritten_anchors():
    assert lot_ladder(5000) == {"base_lot_60": 0.01, "agg_lot_120": 0.02, "max_lot": 0.04}
    assert lot_ladder(10000) == {"base_lot_60": 0.02, "agg_lot_120": 0.04, "max_lot": 0.08}
    assert lot_ladder(25000) == {"base_lot_60": 0.05, "agg_lot_120": 0.10, "max_lot": 0.20}


def test_min_lot_floor_on_small_balance():
    # a $1k account still rounds down but never below 0.01
    assert lot_ladder(1000)["base_lot_60"] == 0.01


def test_usd_per_point():
    assert usd_per_point(0.01) == GOLD_USD_PER_POINT * 0.01     # $1.00 per $1 move


def test_move_for_target_60_on_001():
    m = move_for_target(0.01, 60)
    assert m["price_move"] == 60.0                              # $60 move at 0.01 lot
    # with the 0.04 max lot the same $60 needs only a $15 move
    assert move_for_target(0.04, 60)["price_move"] == 15.0


def test_size_for_risk_money_first():
    # risk $20 with a $20 stop distance at $100/pt/lot → 0.01 lot
    assert size_for_risk(4325.0, 4305.0, 20.0) == 0.01
    # tighter $10 stop → double the lot for the same $20 risk
    assert size_for_risk(4325.0, 4315.0, 20.0) == 0.02


def test_targets_rr_prices_long_and_short():
    tl = targets(4325.0, 4305.0, "long", rr=(2, 3, 4))     # risk 20
    assert [t["price"] for t in tl] == [4365.0, 4385.0, 4405.0]
    ts = targets(4325.0, 4345.0, "short", rr=(2, 3))       # risk 20
    assert [t["price"] for t in ts] == [4285.0, 4265.0]


def test_breakeven_halfway():
    assert breakeven_price(4325.0, 4385.0, 0.5) == 4355.0


def test_prop_pass_math_6pct_on_5k():
    p = prop_pass_math(5000, "6")
    assert p["monthly_target_usd"] == 300.0        # 6% of 5k
    assert p["trades_at_60"] == 5 and p["trades_at_120"] == 3   # ceil(300/60), ceil(300/120)


def test_prop_pass_math_12pct_trade_count():
    p = prop_pass_math(5000, "12")
    assert p["monthly_target_usd"] == 600.0        # 12% of 5k
    assert p["trades_at_60"] == 10                 # 10 × $60 = $600
