"""Tests for the ATR-based SL/TP architecture, including the framework's
worked examples."""

import pytest

from portfolio.sltp import (
    SL_ATR,
    TP_ATR,
    pip_size,
    build_plan,
    plan_from_stop,
)


def test_frozen_multiples():
    assert SL_ATR == 1.0
    assert TP_ATR == (2.0, 4.0, 8.0)


def test_pip_size_families():
    assert pip_size("EURJPY") == 0.01
    assert pip_size("EURUSD") == 0.0001
    assert pip_size("XAUUSD") == 0.01
    assert pip_size("GBPCAD") == 0.0001


def test_risk_reward_ladder():
    plan = build_plan("EURUSD", "LONG", entry=1.1000, atr=0.0050)
    assert [t.risk_reward for t in plan.targets] == [2.0, 4.0, 8.0]


def test_eurjpy_worked_example():
    # Entry 184.97, stop 183.80 -> 1.17 risk (117 pips) = 1 ATR.
    plan = plan_from_stop("EURJPY", "LONG", entry=184.97, stop=183.80)
    assert plan.atr == pytest.approx(1.17)
    assert plan.risk_pips == pytest.approx(117.0)
    prices = [t.price for t in plan.targets]
    assert prices[0] == pytest.approx(187.31, abs=0.01)
    assert prices[1] == pytest.approx(189.65, abs=0.01)
    assert prices[2] == pytest.approx(194.33, abs=0.01)


def test_usdjpy_worked_example():
    # Entry 160.21, stop 158.90 -> 1.31 risk; TP1 ~ 162.83 (framework 162.80).
    plan = plan_from_stop("USDJPY", "LONG", entry=160.21, stop=158.90)
    assert plan.atr == pytest.approx(1.31)
    assert plan.targets[0].price == pytest.approx(162.83, abs=0.01)


def test_short_targets_go_down():
    plan = build_plan("EURGBP", "SHORT", entry=0.8500, atr=0.0040)
    assert plan.stop > plan.entry            # stop above entry for a short
    assert all(t.price < plan.entry for t in plan.targets)
    assert plan.targets[2].price == pytest.approx(0.8500 - 8 * 0.0040)


def test_plan_from_stop_rejects_equal_prices():
    with pytest.raises(ValueError):
        plan_from_stop("EURUSD", "LONG", entry=1.10, stop=1.10)


def test_build_plan_rejects_nonpositive_atr():
    with pytest.raises(ValueError):
        build_plan("EURUSD", "LONG", entry=1.10, atr=0.0)
