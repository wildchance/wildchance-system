"""STRATOPS: campaign objective, exposure cap, scoring/ranking/allocation."""

from gold.objective import campaign_objective, advances
from gold.exposure import can_open, open_risk, open_count
from gold import stratops


# --- campaign objective (uses the HTF timeline anchor) ----------------------

def test_objective_discount_targets_next_zone_up():
    obj = campaign_objective(3986.28)            # HTF discount → long campaign
    assert obj["direction"] == "long"
    assert obj["objective"]["zone"] == "bullish mean"     # next zone above
    assert obj["leg_usd"] and obj["leg_usd"] > 0


def test_advances_and_retreats():
    assert advances("long", 3986.28)["status"] == "advances"
    assert advances("short", 3986.28)["status"] == "retreats"
    assert advances("short", 4950.0)["status"] == "advances"   # premium campaign short


# --- exposure cap -----------------------------------------------------------

def _pos(risk, status="OPEN"):
    return {"risk_usd": risk, "status": status}


def test_exposure_open_risk_and_count():
    ps = [_pos(20), _pos(30), _pos(10, "CLOSED"), _pos(15, "PENDING")]
    assert open_risk(ps) == 65.0 and open_count(ps) == 3   # closed excluded


def test_cap_blocks_over_risk_and_count():
    ps = [_pos(50), _pos(50)]                                  # 100 open
    assert can_open(ps, 30, risk_cap=150)["ok"] is True        # 100+30 <= 150
    assert can_open(ps, 40, risk_cap=120)["ok"] is False       # 100+40 > 120
    assert can_open([_pos(5)] * 6, 5, max_positions=6)["ok"] is False   # count cap


# --- scoring + allocation ---------------------------------------------------

def _cand(score_high=True, risk=20, tt="intraday"):
    if score_high:
        return {"signal": "LONG", "trade_type": tt, "entry": 4000, "risk_usd": risk,
                "gate": {"allow": True}, "campaign": {"status": "advances"},
                "htf_confluence": "aligns", "regime": {"status": "confirms"},
                "location": {"ok": True}, "protraction": {"direction": "long"},
                "liquidity_draw": {"price": 4050}, "targets": [{"rr": 8}]}
    return {"signal": "LONG", "trade_type": tt, "entry": 4000, "risk_usd": risk,
            "gate": {"allow": True}, "campaign": {"status": "retreats"},
            "htf_confluence": "opposes", "regime": {"status": "diverges"},
            "location": {"ok": False}, "protraction": {"direction": "short"},
            "targets": [{"rr": 2}]}


def test_score_high_and_low():
    assert stratops.score_candidate(_cand(True))["score"] > 90
    assert stratops.score_candidate(_cand(False))["score"] < 40
    # gate-blocked → excluded 0
    blocked = dict(_cand(True), gate={"allow": False})
    assert stratops.score_candidate(blocked)["score"] == 0


def test_allocate_takes_best_within_cap():
    cands = [_cand(True, risk=50), _cand(True, risk=50, tt="swing"),
             _cand(True, risk=50, tt="crt"), _cand(False, risk=20)]
    out = stratops.allocate(cands, positions=[], risk_cap=120, max_positions=6, min_score=55)
    assert len(out["take"]) == 2               # 50+50 fit, 3rd 50 would exceed 120
    assert any("score" in h.get("reason", "") for h in out["stand_down"])  # the low one
    assert len(out["hold"]) == 1               # 3rd high-score held on risk cap
