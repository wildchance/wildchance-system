"""VAULTUM Phase 6 (HMM regime) + Phase 10 (portfolio VaR/ES gate) — pure modules."""

import math
import random

from gold import hmm_regime as hm
from gold import portfolio_risk as pr


# --- HMM regime -------------------------------------------------------------------

def _bars_from_returns(rets, start=4000.0):
    px = start
    bars = [(0, px, px, px, px)]
    for r in rets:
        px = px * math.exp(r)
        bars.append((0, px, px, px, px))
    return bars


def test_hmm_needs_min_data():
    out = hm.regime_hmm([(0, 1, 1, 1, 1)] * 5)
    assert out["available"] is False


def test_hmm_detects_bull_trend():
    random.seed(1)
    rets = [0.004 + random.gauss(0, 0.002) for _ in range(80)]   # steady up-drift
    out = hm.regime_hmm(_bars_from_returns(rets))
    assert out["available"] and out["gold_bias"] == "long"
    assert 0.0 <= out["confidence"] <= 1.0
    assert len(out["state_profiles"]) in (2, 3)


def test_hmm_detects_bear_trend():
    random.seed(2)
    rets = [-0.004 + random.gauss(0, 0.002) for _ in range(80)]  # steady down-drift
    out = hm.regime_hmm(_bars_from_returns(rets))
    assert out["available"] and out["gold_bias"] == "short"


def test_hmm_probabilities_sum_to_one():
    random.seed(3)
    rets = [random.gauss(0, 0.01) for _ in range(60)]
    out = hm.regime_hmm(_bars_from_returns(rets))
    total = sum(s["posterior_now"] for s in out["state_profiles"])
    assert abs(total - 1.0) < 0.05


# --- portfolio VaR / ES gate ------------------------------------------------------

def _returns(n=60, sigma=0.01, seed=0):
    random.seed(seed)
    return [random.gauss(0, sigma) for _ in range(n)]


def test_net_exposure_nets_longs_and_shorts():
    pos = [{"side": "buy", "lot": 0.10, "price": 4000},
           {"side": "sell", "lot": 0.04, "price": 4000}]
    r = pr.portfolio_risk(pos, _returns())
    # net 0.06 lot * 100 oz * 4000 = 24,000; gross = 56,000
    assert abs(r["net_exposure"] - 24000) < 1 and abs(r["gross_exposure"] - 56000) < 1
    assert r["net_side"] == "long"


def test_var_scales_with_exposure_and_conf():
    small = pr.portfolio_risk([{"side": "buy", "lot": 0.01, "price": 4000}], _returns())
    big = pr.portfolio_risk([{"side": "buy", "lot": 1.0, "price": 4000}], _returns())
    assert big["var"] > small["var"]
    v95 = pr.portfolio_risk([{"side": "buy", "lot": 0.5, "price": 4000}], _returns(), conf=0.95)
    v99 = pr.portfolio_risk([{"side": "buy", "lot": 0.5, "price": 4000}], _returns(), conf=0.99)
    assert v99["var"] >= v95["var"]


def test_es_ge_var():
    r = pr.portfolio_risk([{"side": "buy", "lot": 0.5, "price": 4000}], _returns())
    assert r["es"] >= r["var"] > 0


def test_gate_blocks_when_over_budget():
    pos = [{"side": "buy", "lot": 2.0, "price": 4000}]          # large book
    g = pr.risk_gate(pos, equity=5000, returns=_returns(sigma=0.02), limit_pct=5.0)
    assert g["approved"] is False and g["var_pct"] > 5.0


def test_gate_approves_within_budget():
    pos = [{"side": "buy", "lot": 0.01, "price": 4000}]
    g = pr.risk_gate(pos, equity=100000, returns=_returns(), limit_pct=5.0)
    assert g["approved"] is True


def test_gate_fail_open_without_data():
    g = pr.risk_gate([{"side": "buy", "lot": 0.1, "price": 4000}], equity=1000, returns=None)
    assert g["approved"] is True        # no VaR estimate → gate open
