"""Radar v3 — mitigation/fresh filter, HTF bias, auto-continuity, sweep+OB, fusion."""

import pytest

from gold import radar as rd
from gold import stratops
from gold import recon as gr


def _bar(o, h, l, c, t="d"):
    return (t, o, h, l, c)


# --- mitigation / fresh ------------------------------------------------------

def test_fresh_vs_mitigated_ob():
    # bearish OB at d2 [4105,4140]; later price returns INTO it → mitigated
    bars = [
        _bar(4100, 4110, 4095, 4105, "d1"),
        _bar(4105, 4140, 4104, 4135, "d2"),   # bearish OB candidate
        _bar(4135, 4136, 4060, 4065, "d3"),   # displacement down (confirm)
        _bar(4065, 4130, 4060, 4120, "d4"),   # trades back UP into the OB zone → mitigated
    ]
    obs = rd.order_blocks(bars)
    bear = [o for o in obs if o["type"] == "bearish" and o["zone"] == [4105.0, 4140.0]][0]
    assert bear["mitigated"] is True and bear["fresh"] is False


def test_unmitigated_kept_fresh():
    bars = [
        _bar(4100, 4110, 4095, 4105, "d1"),
        _bar(4105, 4140, 4104, 4135, "d2"),
        _bar(4135, 4136, 4060, 4065, "d3"),
        _bar(4065, 4070, 4030, 4040, "d4"),   # never returns to the OB → fresh
    ]
    fresh = rd.unmitigated(rd.order_blocks(bars))
    assert any(o["zone"] == [4105.0, 4140.0] and o["fresh"] for o in fresh)


# --- HTF bias + fusion -------------------------------------------------------

def test_htf_bias_from_fresh_ob():
    bars = [
        _bar(4000, 4005, 3990, 3995, "d1"),
        _bar(3995, 3998, 3960, 3965, "d2"),   # bullish demand
        _bar(3965, 4010, 3964, 4005, "d3"),   # up displacement
        _bar(4005, 4030, 4000, 4025, "d4"),
    ]
    hb = rd.htf_bias(rd.order_blocks(bars))
    assert hb["bias"] == "long"


def test_combine_htf_weights_higher_tf():
    # monthly bullish (weight 3) beats daily bearish (weight 1)
    monthly = [{"type": "bullish", "zone": [1, 2], "fresh": True, "timeframe": "1M"}]
    daily = [{"type": "bearish", "zone": [3, 4], "fresh": True, "timeframe": "1D"}]
    out = rd.combine_htf(daily=daily, monthly=monthly)
    assert out["htf_bias"] == "long" and out["score"] == 2      # +3 -1


def test_fuse_with_weekly_profile():
    agree = rd.fuse_with_weekly_profile("long", "long")
    assert agree["conviction"] == "high" and agree["agree"] is True
    clash = rd.fuse_with_weekly_profile("long", "short")
    assert clash["conviction"] == "low"


# --- auto-continuity ---------------------------------------------------------

def test_derive_continuity_from_obs():
    obs = [
        {"type": "bullish", "top": 3960, "bottom": 3940, "fresh": True},
        {"type": "bearish", "top": 4200, "bottom": 4180, "fresh": True},
    ]
    c = rd.derive_continuity(obs, price=4100.0)
    assert c["source"] == "derived"
    assert 3940 in c["sell_targets"] and 4200 in c["buy_targets"]


# --- sweep + OB combo --------------------------------------------------------

def test_sweep_ob_confluence():
    bars = [_bar(4050, 4055, 4048, 4053), _bar(4053, 4058, 4051, 4056),
            _bar(4135, 4145, 4054, 4130)]   # bearish: wick 4145 sweeps above, closes back 4130
    ob = {"type": "bearish", "top": 4140.0, "bottom": 4120.0, "zone": [4120.0, 4140.0]}
    out = rd.sweep_ob_confluence(bars, 4130.0, ob)
    assert out["confluence"] is True and out["side"] == "short"


# --- scoring + recon wiring --------------------------------------------------

def test_radar_bonus_in_score():
    c = {"signal": "SHORT", "trade_type": "sniper", "entry": 4130, "risk_usd": 20,
         "gate": {"allow": True}, "campaign": {"status": "advances"},
         "htf_confluence": "aligns", "regime": {"status": "confirms"},
         "location": {"ok": True}, "protraction": {"direction": "short"},
         "targets": [{"rr": 8}], "radar_confluence": True}
    s = stratops.score_candidate(c)
    assert s["parts"]["radar"] == stratops.RADAR_BONUS


def test_recon_folds_radar():
    from cbdr.engine import build_cbdr
    box = build_cbdr(4030.0, 4010.0)          # +1SD 4050 → sell anchor
    radar = {"bias": "short", "active_retest": {"type": "bearish", "zone": [4120, 4140],
             "fresh": True}}
    sweep = gr.recon_sweep(4055.0, dxy_price=100.75, box=box, radar=radar)
    sell = next((s for s in sweep["setups"] if s["side"] == "SHORT"), None)
    assert sell is not None and sell["radar_confluence"] is True
    assert "📡" in gr.format_recon(sweep)
