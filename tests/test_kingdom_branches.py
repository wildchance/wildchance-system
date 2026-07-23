"""14-branch framework additions — volatility, trap, event horizon, regime checklist."""

import pytest

from gold import volatility as gv
from gold import trap_probability as gt
from gold import event_horizon as eh
from gold import regime_checklist as rc


def _bar(o, h, l, c, t="d"):
    return (t, o, h, l, c)


# --- B9 volatility ------------------------------------------------------------

def _series(n=60, base=4000.0, rng=20.0):
    return [_bar(base + i, base + i + rng, base + i - rng, base + i + 2, f"d{i}")
            for i in range(n)]


def test_atr_and_percentile():
    bars = _series()
    a = gv.atr(bars, 14)
    assert a is not None and a > 0
    p = gv.atr_percentile(bars, 14)
    assert p is None or 0.0 <= p <= 1.0


def test_expected_range_bands_ordered():
    bars = _series()
    er = gv.expected_range(bars, price=4100.0, period=14)
    s = er["scenarios"]
    assert s["expansion"]["range_usd"] > s["base"]["range_usd"] > s["contraction"]["range_usd"]
    assert s["base"]["upper"] > 4100.0 > s["base"]["lower"]


def test_vol_regime_and_size_modifier():
    bars = _series()
    reg = gv.vol_regime(bars, 14)
    assert reg["regime"] in ("low", "normal", "high", "unknown")
    m = gv.size_modifier(bars, 14)
    assert 0.6 <= m <= 1.25


# --- B10 trap probabilities ---------------------------------------------------

def test_trap_probs_sum_to_one():
    # sweep above 4100 then close back below → bull trap lean
    bars = [_bar(4090, 4095, 4085, 4092), _bar(4092, 4098, 4088, 4094),
            _bar(4094, 4130, 4090, 4096)]   # wick 4130 over 4100, closes 4096 below
    out = gt.trap_probabilities(bars, 4100.0)
    assert abs(sum(out["probabilities"].values()) - 1.0) < 0.01
    assert out["most_likely"] in ("clean_breakout", "bull_trap", "bear_trap", "capitulation")


def test_trap_bull_trap_detected():
    bars = [_bar(4090, 4095, 4085, 4092), _bar(4092, 4098, 4088, 4094),
            _bar(4094, 4135, 4092, 4096)]   # strong poke over, rejected back under
    out = gt.trap_probabilities(bars, 4100.0)
    assert out["probabilities"]["bull_trap"] > 0
    assert out["implied_bias"] in ("short", "long")


# --- B14 event horizon --------------------------------------------------------

def test_event_classify_and_decay():
    assert gt is not None
    assert eh.classify_event("US CPI m/m") >= 0.9
    assert eh.classify_event("FOMC Statement") == 1.0
    assert eh.classify_event("random speech") == eh.DEFAULT_WEIGHT
    # nearer event = higher live impact
    near = eh.propagate(1.0, 5)
    far = eh.propagate(1.0, 500)
    assert near > far


def test_event_stack_and_size():
    events = [{"name": "FOMC", "hours_until": 10},
              {"name": "US CPI", "hours_until": 12}]
    out = eh.event_horizon(events)
    assert out["stack"]["near_events"] == 2
    assert out["stack"]["vol_multiplier"] > 1.0
    assert out["size_modifier"] <= 1.0


# --- B12 regime checklist -----------------------------------------------------

def test_checklist_green_when_all_hold():
    cl = rc.build_checklist("long", dxy_unlocked=True, real_rate_direction="falling",
                            cot_zone="moderate", liquidity_state="normal",
                            htf_bias="long", price=4100.0, invalidation_level=3900.0)
    assert cl["verdict"] == "GREEN" and cl["fail_count"] == 0


def test_checklist_red_on_multiple_breaks():
    cl = rc.build_checklist("long", dxy_unlocked=False, real_rate_direction="rising",
                            cot_zone="stretched", liquidity_state="impaired",
                            htf_bias="short", price=3800.0, invalidation_level=3900.0)
    assert cl["verdict"] == "RED" and cl["fail_count"] >= 3
    assert "dxy_flip" in cl["failed"]
