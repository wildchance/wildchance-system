"""WGC/macro feed + OI liquidity flag + July-21 snapshot refresh."""

import pytest

from gold import purchases_audit as gpa
from gold import macro_cycle as gcycle


@pytest.fixture(autouse=True)
def _restore():
    snap = dict(gpa.SNAPSHOT)
    inp = {k: (dict(v) if isinstance(v, dict) else v) for k, v in gcycle.INPUTS.items()}
    yield
    gpa.SNAPSHOT.clear(); gpa.SNAPSHOT.update(snap)
    gcycle.INPUTS.clear(); gcycle.INPUTS.update(inp)


# --- snapshot refreshed to July 21 -------------------------------------------

def test_snapshot_is_july21():
    assert gpa.SNAPSHOT["as_of"] == "2026-07-21"
    assert gpa.SNAPSHOT["cot_noncomm_net"] == 170000
    assert gpa.SNAPSHOT["cot_open_interest"] == 340000
    assert gpa.SNAPSHOT["etf_holdings_t"] == 4050


# --- OI liquidity flag -------------------------------------------------------

def test_liquidity_impaired_thin_tape():
    liq = gpa.liquidity_state()               # 340k vs 528k peak = -35% → impaired
    assert liq["state"] == "impaired"
    assert liq["stop_widen_mult"] == 1.25
    assert liq["vs_peak_pct"] < -20


def test_liquidity_normal_when_deep():
    gpa.SNAPSHOT["cot_open_interest"] = 500000    # near the peak → normal
    assert gpa.liquidity_state()["state"] == "normal"


def test_regime_flags_thin_liquidity():
    reg = gcycle.regime_read()
    assert reg["liquidity"]["state"] == "impaired"
    assert any("thin tape" in c for c in reg["contradictions"])


# --- operator feed -----------------------------------------------------------

def test_feed_snapshot_updates_known_keys():
    gpa.feed(as_of="2026-07-28", cot_noncomm_net=165000, cot_open_interest=335000,
             etf_holdings_t=4040, bogus_key=999)
    assert gpa.SNAPSHOT["cot_noncomm_net"] == 165000
    assert gpa.SNAPSHOT["cot_open_interest"] == 335000
    assert "bogus_key" not in gpa.SNAPSHOT           # unknown keys ignored
    assert gpa.SNAPSHOT["as_of"] == "2026-07-28"


def test_feed_inputs_updates_regime():
    gcycle.feed_inputs(real_rate_direction="falling", fed_cycle="cutting",
                       etf_flow_direction="accumulation", as_of="2026-07-28")
    assert gcycle.INPUTS["real_rate_direction"] == "falling"
    assert gcycle.INPUTS["fed_cycle"] == "cutting"
    assert gcycle.INPUTS["as_of"] == "2026-07-28"


def test_feed_flows_into_positioning():
    gpa.feed(cot_noncomm_net=230000)              # push into stretched zone
    assert gpa.positioning_state()["zone"] == "stretched"
