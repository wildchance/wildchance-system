"""Macro-cycle stack: purchases audit, DXY inverse regime, fused regime gate."""

from gold import purchases_audit as gpa
from gold import dxy as gdxy
from gold import macro_cycle as gcycle


# --- purchases audit --------------------------------------------------------

def test_audit_pct_changes_reconcile():
    a = gpa.audit()
    yoy = {r["period"]: r["change_pct"] for r in a["central_bank"]["yoy"]}
    assert yoy["2023"] == -8.6 and yoy["2025"] == -17.4
    qoq = {r["period"]: r["change_pct"] for r in a["central_bank"]["qoq"]}
    assert qoq["Q2'25"] == -56.9 and qoq["Q2'26"] == -37.9
    mom = {r["period"]: r["change_pct"] for r in a["central_bank"]["mom_2026"]}
    assert mom["Apr"] == -51.4


def test_positioning_state_moderate_room_to_add():
    ps = gpa.positioning_state()
    assert ps["net"] == 181339 and ps["zone"] == "neutral"
    assert ps["below_peak"] == 251238 - 181339


def test_positioning_zones():
    gpa.SNAPSHOT["cot_noncomm_net"] = 120000
    assert gpa.positioning_state()["zone"] == "moderate"
    gpa.SNAPSHOT["cot_noncomm_net"] = 220000
    assert gpa.positioning_state()["zone"] == "stretched"
    gpa.SNAPSHOT["cot_noncomm_net"] = -5000
    assert gpa.positioning_state()["zone"] == "net_short"
    gpa.SNAPSHOT["cot_noncomm_net"] = 181339          # restore


# --- DXY inverse ------------------------------------------------------------

def test_dollar_downtrend_implies_gold_long():
    # No price → anticipated primary decline (dollar SMS) → gold long.
    assert gdxy.gold_from_dollar()["gold_bias"] == "long"
    assert gdxy.dollar_regime()["regime"] == "sms"


def test_dollar_cycle_high_implies_gold_short():
    g = gdxy.gold_from_dollar(115.0)          # above the 114.5 cycle high
    assert g["gold_bias"] == "short" and g["dollar_regime"] == "bms"


def test_dollar_bounce_band_still_buys_gold_dip():
    g = gdxy.gold_from_dollar(106.0)          # in the 105–107 sell-dollar band
    assert g["gold_bias"] == "long"           # temporary headwind = the dip


def test_dxy_confluence():
    assert gdxy.confluence("long") == "confirms"     # dollar weak
    assert gdxy.confluence("short") == "diverges"


# --- fused regime + gate ----------------------------------------------------

def test_regime_read_is_accumulation_long():
    r = gcycle.regime_read()
    assert r["gold_bias"] == "long" and r["regime"] == "accumulation"
    assert r["confluence_score"] >= 2
    assert any("real rate" in c for c in r["contradictions"])   # honest two-way flag


def test_regime_gate_allows_long_blocks_short():
    long_g = gcycle.regime_gate("long")
    assert long_g["ok"] is True and long_g["status"] == "confirms"
    short_g = gcycle.regime_gate("short")
    assert short_g["ok"] is False and short_g["status"] == "diverges"


def test_regime_gate_blocks_stretched_long():
    gpa.SNAPSHOT["cot_noncomm_net"] = 230000
    g = gcycle.regime_gate("long")
    assert g["ok"] is False and "crowded long" in g["reason"]
    gpa.SNAPSHOT["cot_noncomm_net"] = 181339          # restore


def test_sources_registry_present():
    assert set(gcycle.SOURCES) == {"BIS", "FRED", "WGC", "CFTC"}


def test_live_rbusbis_overrides_dxy_structure():
    # No live read → anticipated DXY structure (weak dollar → gold long).
    gcycle.INPUTS["dollar_gold_bias"] = None
    assert gcycle.regime_gate("long")["dollar"] == "confirms"
    # Live RBUSBIS rising = dollar strengthening → gold short: the gate flips.
    gcycle.INPUTS["dollar_gold_bias"] = "short"
    gcycle.INPUTS["dollar_rbusbis_dir"] = "rising"
    try:
        assert gcycle.dollar_gold_bias() == "short"
        assert gcycle.regime_gate("long")["dollar"] == "diverges"
        assert gcycle.regime_gate("short")["dollar"] == "confirms"
        assert "RBUSBIS live" in gcycle.regime_read()["htf"][0]["reading"]
    finally:
        gcycle.INPUTS["dollar_gold_bias"] = None       # restore
        gcycle.INPUTS["dollar_rbusbis_dir"] = None
