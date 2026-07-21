"""Gold options-flow confluence — walls, expected-move envelope, and scoring."""

import pytest

from gold import options_flow as ofl
from gold import stratops
from gold import recon as gr


@pytest.fixture(autouse=True)
def _reset_inputs():
    """Each test starts un-fed and restores INPUTS after."""
    saved = {k: (dict(v) if isinstance(v, dict) else v) for k, v in ofl.INPUTS.items()}
    for k in ("future", "put_wall", "call_wall", "put_vol", "call_vol", "as_of"):
        ofl.INPUTS[k] = None
    ofl.INPUTS["sigma"] = {"1": None, "2": None, "3": None}
    yield
    ofl.INPUTS.update(saved)


def _feed():
    # from the chart: future 4030.4, put wall 4000, call wall 4050, 1σ 21.4 …
    ofl.set_inputs(future=4030.4, put_wall=4000.0, call_wall=4050.0,
                   sigma1=21.4, sigma2=42.6, sigma3=63.7, put_vol=635, call_vol=511)


def test_not_configured_is_neutral():
    assert ofl.configured() is False
    assert ofl.confluence("short", 4050.0)["status"] == "neutral"
    assert ofl.snapshot()["configured"] is False


def test_expected_move_bands():
    _feed()
    em = ofl.expected_move()
    assert em["bands"]["1sigma"]["up"] == pytest.approx(4051.8)
    assert em["bands"]["2sigma"]["down"] == pytest.approx(3987.8)


def test_sell_confirms_at_call_wall():
    _feed()
    c = ofl.confluence("short", 4050.0)          # exactly the call wall
    assert c["status"] == "confirms" and "call wall" in c["reason"]


def test_sell_confirms_at_2sigma_top():
    _feed()
    c = ofl.confluence("short", 4073.0)          # ~ the 2σ top (4030.4+42.6)
    assert c["status"] == "confirms"


def test_buy_confirms_at_put_wall():
    _feed()
    assert ofl.confluence("long", 4000.5)["status"] == "confirms"


def test_sell_into_put_wall_opposes():
    _feed()
    assert ofl.confluence("short", 4000.0)["status"] == "opposes"


def test_flow_bias_put_heavy_is_bearish():
    _feed()
    assert ofl.flow_bias()["bias"] == "bearish"   # 635 puts vs 511 calls


# --- scoring + recon wiring --------------------------------------------------

def _cand(opt=None):
    c = {"signal": "SHORT", "trade_type": "sniper", "entry": 4050, "risk_usd": 20,
         "gate": {"allow": True}, "campaign": {"status": "advances"},
         "htf_confluence": "aligns", "regime": {"status": "confirms"},
         "location": {"ok": True}, "protraction": {"direction": "short"},
         "liquidity_draw": {"price": 4000}, "targets": [{"rr": 8}]}
    if opt is not None:
        c["options_confluence"] = opt
    return c


def test_options_bonus_in_score():
    base = stratops.score_candidate(_cand(opt=False))
    conf = stratops.score_candidate(_cand(opt=True))
    assert conf["parts"]["options"] == stratops.OPTIONS_BONUS
    assert base["parts"]["options"] == 0 and conf["options"] is True


def test_recon_shows_options_when_fed():
    _feed()
    from cbdr.engine import build_cbdr
    box = build_cbdr(4030.0, 4010.0)             # +1SD 4050 → sell anchor at 4055
    sweep = gr.recon_sweep(4050.0, dxy_price=100.75, box=box)
    assert sweep["options"] is not None and sweep["options"]["configured"] is True
    assert "📊 options" in gr.format_recon(sweep)
