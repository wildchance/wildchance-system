"""HTF-ORB conviction scaling — leans size on range-fade, never unlocks trend."""

import pytest

from gold import conviction as gc


# --- eligibility: only range-fade tiers, only on agreement --------------------

def test_range_fade_long_agrees_gets_bump():
    mult, reason = gc.conviction_multiplier("LONG", "sniper", "long")
    assert mult == gc.DEFAULT_AGREE_MULT and "agrees" in reason


def test_trend_tier_never_scaled():
    # a swing/intraday long is NEVER conviction-scaled — the DXY gate owns it
    assert gc.conviction_multiplier("LONG", "swing", "long")[0] == 1.0
    assert gc.conviction_multiplier("LONG", "intraday", "long")[0] == 1.0


def test_opposing_side_no_bump():
    assert gc.conviction_multiplier("SHORT", "sniper", "long")[0] == 1.0


def test_no_htf_bias_no_bump():
    assert gc.conviction_multiplier("LONG", "sniper", "neutral")[0] == 1.0
    assert gc.conviction_multiplier("LONG", "sniper", None)[0] == 1.0


def test_ineligible_tier_no_bump():
    assert gc.conviction_multiplier("LONG", "mystery", "long")[0] == 1.0


def test_multiplier_capped():
    mult, _ = gc.conviction_multiplier("LONG", "sniper", "long", agree_mult=5.0)
    assert mult == gc.MAX_MULT


# --- apply: scales lot + risk, tags, never shrinks ---------------------------

def test_apply_scales_lot_and_risk():
    card = {"signal": "LONG", "trade_type": "sniper", "lot": 0.10, "risk_usd": 20.0}
    out = gc.apply_conviction(card, 1.35)
    assert out["lot"] == pytest.approx(0.13, abs=0.01)   # 0.10 × 1.35 rounded down
    assert out["risk_usd"] == pytest.approx(27.0)
    assert out["conviction_mult"] == 1.35
    # original untouched (returns a copy)
    assert card["lot"] == 0.10


def test_apply_noop_at_or_below_one():
    card = {"lot": 0.10, "risk_usd": 20.0}
    assert gc.apply_conviction(card, 1.0) is card         # no-op returns same object


def test_end_to_end_only_bumps_eligible_long():
    # simulate the muster loop: HTF ORB long → sniper long bumped, swing long not,
    # sniper short not
    htf = "long"
    cards = [
        {"signal": "LONG", "trade_type": "sniper", "lot": 0.1, "risk_usd": 20},
        {"signal": "LONG", "trade_type": "swing", "lot": 0.1, "risk_usd": 20},
        {"signal": "SHORT", "trade_type": "sniper", "lot": 0.1, "risk_usd": 20},
    ]
    bumped = []
    for c in cards:
        m, _ = gc.conviction_multiplier(c["signal"], c["trade_type"], htf)
        if m > 1.0:
            bumped.append(gc.apply_conviction(c, m))
    assert len(bumped) == 1 and bumped[0]["trade_type"] == "sniper"
    assert bumped[0]["risk_usd"] == pytest.approx(27.0)
