from services.trade_executor import build_order
from gold.compounding import ladder, plan, target_to_lot, RATE


# ---------------- executor order translation ----------------
def _sig(**kw):
    base = {"signal": "LONG", "entry": 4308.5, "stop": 4298.0, "lot": 0.01,
            "profile": "Classic Tuesday Low", "gate": {"allow": True},
            "targets": [{"price": 4329.5}, {"price": 4340.0}]}
    base.update(kw)
    return base


def test_build_order_structure_is_limit():
    o = build_order(_sig(entry_mode="structure"))
    assert o["order_type"] == "limit" and o["price"] == 4308.5
    assert o["side"] == "buy" and o["sl"] == 4298.0 and o["tp"] == 4329.5
    assert o["tp_levels"] == [4329.5, 4340.0] and o["symbol"] == "XAUUSD"


def test_build_order_market_default():
    o = build_order(_sig())
    assert o["order_type"] == "market" and o["price"] is None


def test_build_order_short_and_notrade():
    assert build_order(_sig(signal="SHORT"))["side"] == "sell"
    assert build_order({"signal": "NO TRADE"}) is None


def test_build_order_blocked_when_gate_denies():
    assert build_order(_sig(gate={"allow": False})) is None


# ---------------- compounding ladders (from the handwritten plan) ----------------
def test_swing_ladder_matches_handwriting():
    l = ladder(700, "swing", "USD")
    bals = [s["balance"] for s in l["steps"]]
    assert bals == [850.0, 2350.0, 17350.0, 167350.0]      # 700→…→167,350
    profits = [s["profit"] for s in l["steps"]]
    assert profits == [150.0, 1500.0, 15000.0, 150000.0]   # 15,000 × lot


def test_low_ladder_rate():
    l = ladder(500, "low", "KES")
    assert l["steps"][0]["profit"] == 55.0                  # 0.01 lot × 5,500
    assert l["steps"][1]["profit"] == 110.0                 # 0.02 lot
    assert l["currency"] == "KES"


def test_mixed_switches_rate():
    l = ladder(700, "mixed", "USD", mix_switch_lot=0.32)
    by_lot = {s["lot"]: s["profit"] for s in l["steps"]}
    assert by_lot[0.16] == 0.16 * RATE["low"]               # below switch → low rate
    assert by_lot[0.32] == 0.32 * RATE["swing"]             # at/above switch → swing rate


def test_target_to_lot():
    assert target_to_lot(150, "swing") == 0.01              # $150 swing = 0.01 lot
    assert target_to_lot(1500, "swing") == 0.10


def test_plan_picks_tier_by_deposit():
    assert plan(500, "USD")["tier"] == "low"
    assert plan(700, "USD")["tier"] == "swing"
    assert "swing" in plan(700)["recommended"]["mode"]
