"""MT5 scale-out — split a position across the trend-TP ladder into partial legs."""

from services import trade_executor as te


# ---- plan_scale_out: volume math -------------------------------------------

def test_scale_out_sums_exactly_to_total():
    for total in (0.04, 0.07, 0.10, 0.20, 0.33, 1.0):
        legs = te.plan_scale_out(total, [10, 20, 30, 40])
        assert round(sum(l["volume"] for l in legs), 2) == round(total, 2)
        assert all(l["volume"] >= 0.01 for l in legs)          # never below min lot


def test_scale_out_is_front_loaded():
    # default weights 0.4/0.3/0.2/0.1 → nearest TP gets the most volume
    legs = te.plan_scale_out(0.20, [10, 20, 30, 40])
    vols = [l["volume"] for l in legs]
    assert vols[0] >= vols[-1]
    assert [l["tp"] for l in legs] == [10, 20, 30, 40]         # nearest → furthest


def test_scale_out_tiny_lot_uses_what_it_can_afford():
    # 0.02 over 4 TPs → only 2 legs (nearest two), each 0.01
    legs = te.plan_scale_out(0.02, [10, 20, 30, 40])
    assert len(legs) == 2
    assert [l["volume"] for l in legs] == [0.01, 0.01]
    assert [l["tp"] for l in legs] == [10, 20]
    # 0.01 cannot split → a single 0.01 leg
    assert te.plan_scale_out(0.01, [10, 20, 30, 40]) == [{"volume": 0.01, "tp": 10}]
    # below min lot → nothing
    assert te.plan_scale_out(0.0, [10, 20]) == []


def test_scale_out_enforces_min_lot_above_step():
    # broker with min lot 0.10 but 0.01 step: legs must be ≥ 0.10, never 0.01
    legs = te.plan_scale_out(0.20, [10, 20, 30, 40], min_lot=0.10, step=0.01)
    assert all(l["volume"] >= 0.10 for l in legs)
    assert round(sum(l["volume"] for l in legs), 2) == 0.20
    # 0.15 over a 0.10 floor → only ONE affordable leg (0.15), not two sub-min legs
    legs2 = te.plan_scale_out(0.15, [10, 20, 30, 40], min_lot=0.10, step=0.01)
    assert len(legs2) == 1 and legs2[0]["volume"] == 0.15
    # below the floor entirely → nothing
    assert te.plan_scale_out(0.05, [10, 20], min_lot=0.10, step=0.01) == []


def test_scale_out_fewer_tps_than_units():
    legs = te.plan_scale_out(0.10, [10, 20])                   # 2 TPs, 10 units
    assert len(legs) == 2
    assert round(sum(l["volume"] for l in legs), 2) == 0.10


# ---- build_orders: wiring to the signal ------------------------------------

def _sig(lot=0.20):
    return {
        "signal": "LONG", "entry": 4119.68, "stop": 4090.0, "lot": lot,
        "gate": {"allow": True}, "profile": "Bullish Expansion",
        "targets": [{"price": 4179.0}],                        # base RR TP1
        "trend_targets": {"targets": [
            {"label": "TP1", "price": 4214.2}, {"label": "TP2", "price": 4272.6},
            {"label": "TP3", "price": 4367.1}, {"label": "TP4", "price": 4520.1}]},
    }


def test_build_orders_scales_across_trend_ladder():
    orders = te.build_orders(_sig(0.20), source="gold_intraday")
    assert len(orders) == 4
    # volumes sum to the sized lot; each leg has its own trend rung + shared stop
    assert round(sum(o["volume"] for o in orders), 2) == 0.20
    assert [o["tp"] for o in orders] == [4214.2, 4272.6, 4367.1, 4520.1]
    assert all(o["sl"] == 4090.0 for o in orders)
    assert all(o["side"] == "buy" for o in orders)
    assert [o["scale_leg"] for o in orders] == ["1/4", "2/4", "3/4", "4/4"]
    assert all(len(o["comment"]) <= 31 for o in orders)        # MT5 comment cap


def test_build_orders_single_when_no_trend_ladder():
    sig = _sig(0.20)
    sig.pop("trend_targets")
    orders = te.build_orders(sig, source="gold_intraday")
    assert len(orders) == 1
    assert orders[0]["tp"] == 4179.0                           # falls back to base TP1


def test_build_orders_single_when_scale_out_disabled():
    assert len(te.build_orders(_sig(0.20), scale_out=False)) == 1


def test_build_orders_single_when_lot_too_small_to_split():
    # 0.01 can't split into ≥2 legs → one order (base)
    orders = te.build_orders(_sig(0.01), source="gold_intraday")
    assert len(orders) == 1


def test_build_orders_none_when_not_tradeable():
    assert te.build_orders({"signal": "NO TRADE"}) == []
    assert te.build_orders({"signal": "LONG", "gate": {"allow": False}}) == []
