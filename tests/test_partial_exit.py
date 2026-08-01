"""Runner break-even scale-out — the 250/500 partial exit + BE trail (pure logic)."""

from services import trade_executor as te


def _sell_sig(lot=0.30):
    # a sized SELL card: entry 4200, stop 4212, final floor target 3885
    return {"signal": "SELL", "entry": 4200.0, "stop": 4212.0, "lot": lot,
            "kind": "limit", "profile": "optimus",
            "targets": [{"price": 4100.0}, {"price": 3885.0}],
            "exit_style": "partial", "gate": {"allow": True}}


def test_lot_fractions_sum_and_runner_remainder():
    vols = te._lot_fractions(0.30, [0.34, 0.33])       # runner = 0.33
    assert len(vols) == 3
    assert round(sum(vols), 2) == 0.30
    assert all(v >= 0.01 for v in vols)


def test_lot_fractions_degrade_when_too_small():
    # 0.02 lots can't afford 3 legs at 0.01 min → nearest 2 only, still sums
    vols = te._lot_fractions(0.02, [0.34, 0.33])
    assert len(vols) == 2 and round(sum(vols), 2) == 0.02


def test_partial_exit_legs_bank_down_and_runner_to_floor():
    legs = te.plan_partial_exit(_sell_sig(0.30))
    assert len(legs) == 3
    roles = [l["scale_role"] for l in legs]
    assert roles == ["p1", "p2", "runner"]
    # sell banks DOWN: p1 tp = 4200-250, p2 tp = 4200-500
    assert legs[0]["tp"] == 3950.0 and legs[1]["tp"] == 3700.0
    # runner rides to the deepest floor and arms break-even on p1
    runner = legs[2]
    assert runner["tp"] == 3885.0
    assert runner["be_price"] == 4200.0 and runner["be_after"] == "p1"
    # all legs share one group and sum to the lot
    assert len({l["group_id"] for l in legs}) == 1
    assert round(sum(l["volume"] for l in legs), 2) == 0.30


def test_partial_exit_small_lot_falls_back_to_ladder():
    sig = _sell_sig(0.01)                               # can't scale out
    legs = te.plan_partial_exit(sig)
    assert all(l.get("scale_role") != "p2" for l in legs)   # no partial legs


def test_build_orders_routes_partial_when_exit_style_set():
    legs = te.build_orders(_sell_sig(0.30))
    assert [l["scale_role"] for l in legs] == ["p1", "p2", "runner"]
    # without exit_style it does NOT scale into partials
    plain = dict(_sell_sig(0.30)); plain.pop("exit_style")
    assert not any(l.get("scale_role") == "runner" for l in te.build_orders(plain))


def test_breakeven_modifications_emit_only_after_p1_fills_with_ticket():
    gid = "g1"
    legs = [
        {"scale_role": "p1", "status": "filled", "group_id": gid, "ticket": 111},
        {"scale_role": "runner", "status": "sent", "group_id": gid, "ticket": 222,
         "be_price": 4200.0, "be_after": "p1", "be_done": 0,
         "symbol": "XAUUSD", "side": "sell", "tp": 3885.0, "source": "optimus"},
    ]
    mods = te.breakeven_modifications(legs)
    assert len(mods) == 1
    m = mods[0]
    assert m["order_type"] == "modify" and m["ticket"] == 222 and m["sl"] == 4200.0
    assert m["modifies_role"] == "runner"


def test_breakeven_holds_until_partial_fills():
    gid = "g2"
    legs = [
        {"scale_role": "p1", "status": "sent", "group_id": gid, "ticket": 111},
        {"scale_role": "runner", "status": "sent", "group_id": gid, "ticket": 222,
         "be_price": 4200.0, "be_after": "p1", "be_done": 0},
    ]
    assert te.breakeven_modifications(legs) == []       # p1 not filled → no BE move


def test_partials_snap_to_hvn_when_provided():
    # sell from 4200; HVNs below at 3960 (near the 250-pt raw 3950) and 3700 (=500-pt raw)
    hvn = [3960.0, 3700.0, 3600.0]
    legs = te.plan_partial_exit(_sell_sig(0.30), hvn=hvn)
    p1, p2, runner = legs
    assert p1["tp"] == 3960.0            # snapped to the HVN nearest the 3950 raw target
    assert p2["tp"] == 3700.0           # HVN at the 500-pt distance
    # runner rides to the deepest HVN at/below the card floor (3885 → 3600)
    assert runner["tp"] == 3600.0 and runner["be_price"] == 4200.0


def test_partials_keep_raw_distance_without_hvn():
    legs = te.plan_partial_exit(_sell_sig(0.30))       # no hvn
    assert legs[0]["tp"] == 3950.0 and legs[1]["tp"] == 3700.0


def test_breakeven_not_reemitted_when_done():
    gid = "g3"
    legs = [
        {"scale_role": "p1", "status": "filled", "group_id": gid, "ticket": 111},
        {"scale_role": "runner", "status": "sent", "group_id": gid, "ticket": 222,
         "be_price": 4200.0, "be_after": "p1", "be_done": 1},
    ]
    assert te.breakeven_modifications(legs) == []       # already moved → idempotent
