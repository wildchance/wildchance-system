"""Unit tests for the performance scorecard + reflection loop (pure functions)."""

import pytest

from usdjpy.scorecard import (
    build_scorecard, max_drawdown_r, by_group,
    MIN_SAMPLE, MIN_FACTOR, MAX_FACTOR,
)


# ---------------------------------------------------------------------------
# core metrics
# ---------------------------------------------------------------------------

def test_empty():
    c = build_scorecard([])
    assert c.n == 0
    assert c.verdict == "INCONCLUSIVE"
    assert c.confidence_factor == 1.0


def test_basic_counts_and_expectancy():
    # 3 wins of +2R, 2 losses of -1R  -> total +4R over 5 trades
    c = build_scorecard([2, 2, 2, -1, -1])
    assert c.n == 5
    assert c.wins == 3 and c.losses == 2 and c.scratches == 0
    assert c.win_rate == pytest.approx(0.6)
    assert c.total_r == pytest.approx(4.0)
    assert c.expectancy == pytest.approx(0.8)
    assert c.avg_win_r == pytest.approx(2.0)
    assert c.avg_loss_r == pytest.approx(-1.0)


def test_profit_factor():
    # gross win 6, gross loss 2 -> PF 3.0
    c = build_scorecard([2, 2, 2, -1, -1])
    assert c.profit_factor == pytest.approx(3.0)


def test_profit_factor_none_when_no_losses():
    c = build_scorecard([1, 1, 1])
    assert c.profit_factor is None       # undefined, not infinity


def test_scratches_counted():
    c = build_scorecard([1, 0, -1, 0])
    assert c.scratches == 2


def test_best_worst():
    c = build_scorecard([0.5, -1, 3.2, -0.4])
    assert c.best_r == pytest.approx(3.2)
    assert c.worst_r == pytest.approx(-1.0)


# ---------------------------------------------------------------------------
# max drawdown
# ---------------------------------------------------------------------------

def test_max_drawdown_simple():
    # equity: +2, +1, +3, +1, -1  -> peak 3 then down to -1 => dd of 4
    assert max_drawdown_r([2, -1, 2, -2, -2]) == pytest.approx(4.0)


def test_max_drawdown_never_negative_on_monotonic_up():
    assert max_drawdown_r([1, 1, 1]) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# reflection loop: verdict + confidence factor
# ---------------------------------------------------------------------------

def test_inconclusive_below_min_sample():
    c = build_scorecard([2, 2, 2])          # great but tiny sample
    assert c.n < MIN_SAMPLE
    assert c.verdict == "INCONCLUSIVE"
    assert c.confidence_factor == 1.0       # stays neutral


def test_green_when_edge_strong():
    rs = [2, 2, 2, 2, 2, 2, -1, -1, -1, -1]  # 10 trades, +0.8R expectancy, PF 3
    c = build_scorecard(rs)
    assert c.n >= MIN_SAMPLE
    assert c.verdict == "GREEN"
    assert c.confidence_factor > 1.0
    assert c.confidence_factor <= MAX_FACTOR


def test_red_when_edge_negative():
    rs = [-1] * 7 + [1] * 5                  # 12 trades, negative expectancy
    c = build_scorecard(rs)
    assert c.verdict == "RED"
    assert c.confidence_factor < 1.0
    assert c.confidence_factor >= MIN_FACTOR


def test_amber_when_marginal():
    # 12 trades, slightly positive expectancy but PF below green threshold
    rs = [1] * 6 + [-1] * 5 + [0.1]          # total ~1.1R over 12 -> ~0.09R
    c = build_scorecard(rs)
    assert c.verdict == "AMBER"


def test_confidence_factor_is_clamped():
    # Enormous edge should still cap at MAX_FACTOR.
    rs = [10] * 12
    c = build_scorecard(rs)
    assert c.confidence_factor == MAX_FACTOR
    # Catastrophic edge floors at MIN_FACTOR.
    rs2 = [-10] * 12
    c2 = build_scorecard(rs2)
    assert c2.confidence_factor == MIN_FACTOR


# ---------------------------------------------------------------------------
# grouping
# ---------------------------------------------------------------------------

def test_by_group_month():
    trades = [
        {"month": "2026-05", "result_r": 2},
        {"month": "2026-05", "result_r": -1},
        {"month": "2026-06", "result_r": 1},
    ]
    g = by_group(trades, "month")
    assert set(g) == {"2026-05", "2026-06"}
    assert g["2026-05"]["n"] == 2
    assert g["2026-06"]["n"] == 1
    assert g["2026-06"]["total_r"] == pytest.approx(1.0)


def test_by_group_action_skips_none():
    trades = [
        {"action": "BUY", "result_r": 1},
        {"action": "SELL", "result_r": None},   # dropped
        {"action": "BUY", "result_r": -1},
    ]
    g = by_group(trades, "action")
    assert g["BUY"]["n"] == 2
    assert "SELL" not in g                       # had no scoreable trade


def test_to_dict_round_trip():
    d = build_scorecard([2, -1, 2, -1, 2, -1, 2, -1, 2, -1]).to_dict()
    assert d["verdict"] in ("GREEN", "AMBER", "RED", "INCONCLUSIVE")
    assert "confidence_factor" in d
    assert "lesson" in d
    assert isinstance(d["lesson"], str) and d["lesson"]
