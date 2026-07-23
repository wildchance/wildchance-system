"""Kingdom v2 additions — volume profile, 4-scenario, breaker blocks, narrative,
price-inelastic demand, kingdom digest."""

import pytest

from gold import volume_profile as gvp
from gold import scenarios as gsc
from gold import radar as grd
from gold import macro_cycle as mc
from cbdr.engine import build_cbdr


def _bar(o, h, l, c, t="d"):
    return (t, o, h, l, c)


# --- B2 volume/TPO profile ----------------------------------------------------

def test_poc_vah_val_ordered():
    # price spends most time around 4050 → POC there; value area brackets it
    bars = [_bar(4040, 4060, 4030, 4050, f"d{i}") for i in range(20)]
    bars += [_bar(4048, 4055, 4045, 4050, f"e{i}") for i in range(20)]   # cluster
    vp = gvp.volume_profile(bars, bins=30)
    assert vp["val"] <= vp["poc"] <= vp["vah"]
    assert "TPO" in vp["source"]           # no volume → TPO fallback


def test_profile_location():
    bars = [_bar(4040, 4060, 4030, 4050, f"d{i}") for i in range(30)]
    vp = gvp.profile_read(bars, price=4200.0)
    assert vp["location"] == "above_value" and vp["vs_poc"] == "above"


# --- B13 four-scenario --------------------------------------------------------

def test_scenario_liquidity_sweep():
    box = build_cbdr(4060.0, 4040.0)          # +1SD 4080, +1.5SD 4090
    # price tagged above +1SD then closed back inside → sweep
    bars = [_bar(4075, 4079, 4072, 4078), _bar(4078, 4095, 4076, 4079)]  # wick 4095, close 4079<4080
    out = gsc.classify_scenario(box, 4079.0, htf_bias="short", bars=bars)
    assert abs(sum(out["probabilities"].values()) - 1.0) < 0.01
    assert out["scenario"] in ("liquidity_sweep", "direct_expansion",
                               "deep_institutional_hunt", "dead_cat_bounce")


def test_scenario_deep_hunt_beyond_2sd():
    box = build_cbdr(4060.0, 4040.0)          # +2SD = 4100
    bars = [_bar(4098, 4102, 4096, 4101)]
    out = gsc.classify_scenario(box, 4105.0, htf_bias="long", bars=bars)
    assert out["beyond_2sd"] is True
    assert out["probabilities"]["deep_institutional_hunt"] > 0


# --- B4 breaker blocks + narrative cycle --------------------------------------

def test_breaker_block_detected():
    # bearish OB forms then price closes ABOVE its top → bullish breaker
    bars = [
        _bar(4000, 4010, 3995, 4005, "d1"),
        _bar(4005, 4040, 4004, 4035, "d2"),   # up-close
        _bar(4035, 4036, 3980, 3985, "d3"),   # down displacement → bearish OB [4005,4040]
        _bar(3985, 4060, 3984, 4055, "d4"),   # closes above 4040 → breaks it (bullish breaker)
    ]
    bb = grd.breaker_blocks(bars)
    assert any(b["type"] == "bullish" and b["kind"] == "breaker" for b in bb)


def test_narrative_cycle_phases():
    box = build_cbdr(4060.0, 4040.0)          # mid 4050
    acc = grd.narrative_cycle("long", 4030.0, box, swept=False)   # discount + long
    assert acc["phase"] == "accumulation"
    manip = grd.narrative_cycle("long", 4030.0, box, swept=True)
    assert manip["phase"] == "manipulation"


# --- B8 price-inelastic demand ------------------------------------------------

def test_price_inelastic_demand_score():
    out = mc.price_inelastic_demand()
    assert 0.0 <= out["score"] <= 1.0 and out["band"] in ("low", "moderate", "high")


# --- kingdom digest formatter -------------------------------------------------

def test_format_kingdom_digest():
    from services.kingdom_service import format_kingdom
    rep = {"asset": "XAU/USD", "price": 4100.0,
           "kingdom_consensus": {"net_bias": "short", "vote_score": -2,
                                 "bullish": 1, "bearish": 3},
           "vaultum_directive": {"regime_invalidation": {"verdict": "AMBER", "failed": ["dxy_flip"]},
                                 "temporal_risk": {"vol_multiplier": 1.4}},
           "branches": {"B13": {"scenario": {"scenario": "liquidity_sweep", "lean": "short"}},
                        "B1": {"macro_paradox_resolution": "DXY not flipped — longs locked"},
                        "B9": {"regime": {"regime": "high"}}}}
    line = format_kingdom(rep)
    assert "KINGDOM REPORT" in line and "SHORT" in line and "liquidity sweep" in line
