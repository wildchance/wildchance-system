"""Copy-trade network — upscale ladder, prop firms, currency tiers, D/W/M grid."""

import pytest

from gold import account_network as gan


# --- upscale ladder -----------------------------------------------------------

def test_upscale_ladder_x10_rungs():
    out = gan.upscale_ladder(100, min_copies=10)
    sizes = [r["size"] for r in out["rungs"]]
    assert sizes == [100, 1000, 10000, 100000, 1_000_000, 10_000_000, 100_000_000]
    # each non-top rung requires 10 copies to graduate
    assert all(r["min_copies_to_graduate"] == 10 for r in out["rungs"][:-1])
    assert out["rungs"][-1]["multiple"] == "ceiling"


def test_upscale_ladder_from_higher_base():
    out = gan.upscale_ladder(10000)
    assert out["rungs"][0]["size"] == 10000       # starts at/above base


# --- prop firms ---------------------------------------------------------------

def test_prop_plan_phases_and_split():
    p = gan.prop_plan("fundingpips", 100000)
    assert p["firm"] == "FundingPips" and len(p["phase_plan"]) == 2
    # phase-1 target 8% of 100k = 8000
    assert p["phase_plan"][0]["profit_target"] == pytest.approx(8000.0)
    assert p["funded_split"] == 0.80


def test_prop_plan_unknown_firm():
    assert "error" in gan.prop_plan("nope", 5000)


def test_prop_firms_registry():
    firms = gan.prop_firms()["firms"]
    assert {"fundingpips", "ftmo", "the5ers", "myfundedfx"} <= set(firms)


# --- currency tiers -----------------------------------------------------------

def test_currency_deposits_converts():
    out = gan.currency_deposits(5000, ["USD", "KES", "KWD"])
    d = {r["currency"]: r["deposit"] for r in out["deposits"]}
    assert d["USD"] == 5000.0
    assert d["KES"] > d["USD"]        # weaker unit → bigger number
    assert d["KWD"] < d["USD"]        # stronger unit → smaller number


def test_set_fx_updates_rate():
    gan.set_fx({"TESTX": 7.5})
    out = gan.currency_deposits(100, ["TESTX"])
    assert out["deposits"][0]["deposit"] == pytest.approx(750.0)


# --- structured D/W/M grid ----------------------------------------------------

def test_structured_targets_bands():
    micro = gan.structured_targets(500)
    inst = gan.structured_targets(5_000_000)
    assert micro["band"] == "micro" and inst["band"] == "institutional"
    # smaller band runs a higher daily % than the institutional band
    assert micro["daily"]["pct"] > inst["daily"]["pct"]
    assert micro["monthly"]["usd"] == pytest.approx(500 * 0.60)


def test_network_structure_and_report():
    ns = gan.network_structure()
    assert len(ns["bands"]) == 5 and "upscale" in ns
    rep = gan.network_report(100, "ftmo", 50000)
    assert "upscale_ladder" in rep and rep["prop"]["firm"] == "FTMO"
    assert len(rep["currency_tiers"]) >= 1
