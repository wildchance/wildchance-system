"""Zones + sniper layering, account-tier flip ladders, and DXY fib triggers."""

import pytest

from gold import zones as gz
from gold import flip_ladders as fl
from gold import dxy as gdxy
from gold import macro_cycle as gcycle
from gold.objective import campaign_objective


# --- named zones + pip budget ------------------------------------------------

def test_zone_for_locates_and_flanks():
    z = gz.zone_for(4018.0)                       # between the buy shelf and sell shelf
    assert z["inside"] is None
    assert z["nearest_below"]["name"] == "shelf_3886_3941"   # buy shelf below
    assert z["nearest_above"]["name"] == "sell_4045_4065"    # sell shelf above


def test_zone_for_inside_a_band():
    z = gz.zone_for(3840.0)
    assert z["inside"]["name"] == "ob_3840"


def test_zone_budget_pips_and_bag():
    b = gz.zone_budget(4018.0)
    # 4018 → shelf top 3941.26 = 76.74 pts / 0.10 = 767 pips down
    assert b["next_buy_zone"]["name"] == "shelf_3886_3941"
    assert b["next_sell_zone"]["name"] == "sell_4045_4065"
    # round-trip bag = 3941.26 → 4045.0 = 103.74 pts = ~1037 pips
    assert b["round_trip_bag_pips"] == pytest.approx(1037.4, abs=1.0)


# --- sniper stack ------------------------------------------------------------

def test_sniper_stack_buy_geometry():
    s = gz.sniper_stack("ob_3840", balance=5000, risk_usd=120, layers=3)
    assert s["signal"] == "LONG"
    assert len(s["orders"]) == 3
    # shared stop sits below the zone low (3820) by the buffer
    assert s["shared_stop"] < 3820
    # entries ladder from the high edge down into the zone
    entries = [o["entry"] for o in s["orders"]]
    assert entries == sorted(entries, reverse=True)
    assert max(entries) <= 3860 and min(entries) >= 3820
    # every order shares the one stop
    assert all(o["stop"] == s["shared_stop"] for o in s["orders"])


def test_sniper_stack_sell_geometry():
    s = gz.sniper_stack("sell_4045_4065", balance=5000, risk_usd=120, layers=2)
    assert s["signal"] == "SHORT"
    assert s["shared_stop"] > 4065          # stop above the zone high
    entries = [o["entry"] for o in s["orders"]]
    assert entries == sorted(entries)       # ladder up into premium


def test_sniper_stack_flags_min_lot_clamp():
    # A tiny budget on a wide zone stop: min-lot forces each layer above its share
    # of the budget, and that clamp must be surfaced honestly.
    s = gz.sniper_stack("ob_3840", balance=1000, risk_usd=15, layers=3)
    assert s["min_lot_clamped"] is True
    assert all(o["lot"] == 0.01 for o in s["orders"])   # every layer pinned to min lot


def test_sniper_stack_flags_cap_breach_on_wide_zone():
    # The 250-wide weekly-OB zone at min lot risks well over the $120 cap — must be
    # flagged, not silently deployed.
    s = gz.sniper_stack("weekly_ob_3250_3500", balance=1000, risk_usd=30, layers=3)
    assert s["min_lot_clamped"] is True
    assert s["within_cap"] is False
    assert "EXCEEDS" in s["note"]


def test_sniper_stack_unknown_zone():
    assert gz.sniper_stack("nope", 5000)["signal"] == "NO TRADE"


# --- account-tier flip ladders ----------------------------------------------

def test_account_tier_classification():
    assert fl.account_tier(500) == "cent_flipper"
    assert fl.account_tier(700) == "middle"
    assert fl.account_tier(5000) == "middle"
    assert fl.account_tier(5000.01) == "flipper"


def test_cent_flipper_reproduces_the_sheet():
    out = fl.cent_flipper(700, runs=10)
    # 700 + 55*(2^10 - 1) = 700 + 56,265 = 56,965 (the handwritten total)
    assert out["final_balance"] == pytest.approx(56965.0, abs=0.01)
    assert out["runs"] == 10
    assert out["ladder"][0]["gain"] == 55.0
    assert out["ladder"][-1]["gain"] == 55.0 * (2 ** 9)   # 28,160


def test_middle_ladder_cadence_and_pips():
    out = fl.middle_ladder(5000, cycles=4)
    assert out["runs"] == 12
    assert out["cadence"] == [500, 500, 1500]
    # 4 cycles × 2,500 pips = 10,000 pips
    assert out["cumulative_pips"] == 10000
    assert out["benchmark"] == 5000


def test_flipper_is_1500_only():
    out = fl.flipper(10000, runs=6)
    assert all(r["pips"] == 1500 for r in out["ladder"])
    assert out["cumulative_pips"] == 9000


def test_plan_picks_tier():
    assert fl.plan(500)["tier"] == "cent_flipper"
    assert fl.plan(3000)["tier"] == "middle"
    assert fl.plan(20000)["tier"] == "flipper"


# --- DXY fib structure triggers ---------------------------------------------

def test_dxy_last_discount_trigger():
    t = gdxy.gold_structure_trigger(94.0)
    assert t["trigger"] == "gold_last_discount"
    assert t["gold_bias"] == "long" and t["strength"] == "max"


def test_dxy_ceiling_trigger():
    t = gdxy.gold_structure_trigger(105.9)
    assert t["trigger"] == "gold_ceiling"
    assert t["gold_bias"] == "short"


def test_dxy_between_bands_no_trigger():
    assert gdxy.gold_structure_trigger(100.75)["trigger"] is None


def test_regime_gate_ceiling_blocks_long():
    # DXY parked in the ceiling band opposes a fresh gold long.
    g = gcycle.regime_gate("long", dxy_price=105.9)
    assert g["ok"] is False
    assert g["dxy_trigger"] == "gold_ceiling"


def test_regime_gate_discount_allows_long():
    g = gcycle.regime_gate("long", dxy_price=94.0)
    assert g["ok"] is True
    assert g["dxy_trigger"] == "gold_last_discount"


# --- campaign objective now carries the zone budget --------------------------

def test_campaign_objective_includes_zone_budget():
    obj = campaign_objective(4018.0)
    assert obj["zone_budget"] is not None
    assert obj["zone_budget"]["next_buy_zone"]["name"] == "shelf_3886_3941"


# --- STRATOPS muster now musters sniper-stack candidates ---------------------

def test_zone_candidates_align_with_the_campaign():
    import services.stratops_service as ss
    from gold import stratops
    cands = ss._zone_candidates(4018.0, balance=5000, risk_usd=20)
    assert len(cands) == 3                       # a 3-layer stack
    # discount campaign at 4018 → LONG stack on the buy shelf, target the sell shelf
    assert all(c["signal"] == "LONG" for c in cands)
    assert all(c["trade_type"] == "sniper" for c in cands)
    assert cands[0]["profile"] == "shelf_3886_3941"
    assert cands[0]["targets"][0]["price"] == 4045.0
    # the deepest layer (tightest stop → lowest risk) clears the prop gate and scores
    deepest = min(cands, key=lambda c: c["risk_usd"])
    assert deepest["gate"]["allow"] is True
    assert stratops.score_candidate(deepest)["score"] > 55


def test_zone_candidates_empty_at_range_extreme():
    import services.stratops_service as ss
    # above every sell shelf → no aligned zone to layer into
    assert ss._zone_candidates(4300.0, balance=5000, risk_usd=20) == [] or \
        all(c["signal"] == "SHORT" for c in ss._zone_candidates(4300.0, 5000, 20))


# --- zone plan + Telegram digest (real-time touch alerts) --------------------

def test_zone_plan_builds_both_legs():
    plan = gz.zone_plan(4018.0, balance=5000, risk_usd=20)
    roles = {l["role"] for l in plan["legs"]}
    assert roles == {"buy", "sell"}
    buy = next(l for l in plan["legs"] if l["role"] == "buy")
    assert buy["signal"] == "LONG"
    assert buy["target"] == 4045.0                 # opposite rail = the bag
    assert buy["stop"] < min(buy["entries"])       # stop below the entry ladder
    assert buy["htf"] in ("aligns", "opposes", "neutral")


def test_zone_plan_arms_only_on_touch():
    # far above both shelves but within touch of the sell shelf → armed sell leg
    near = gz.zone_plan(4050.0, balance=5000)      # inside sell_4045_4065
    assert near["armed"] is True
    # mid-range, >150 pips from either shelf → nothing armed
    far = gz.zone_plan(4005.0, balance=5000, touch_pips=10)
    assert far["armed"] is False


def test_format_zone_digest_has_levels():
    txt = gz.format_zone_digest(gz.zone_plan(4050.0, balance=5000))
    assert "GOLD Zone Plan" in txt
    assert "SL" in txt and "TP" in txt
    assert "⚡ARMED" in txt                         # 4050 is inside the sell shelf
