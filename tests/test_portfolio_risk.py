"""Tests for the dynamic risk-management model."""

import pytest

from portfolio.risk_model import (
    PER_TRADE_RISK,
    MAX_EXPOSURE,
    per_trade_risk_pct,
    position_risk,
    check_group_exposure,
    aggregate_exposure,
    drawdown_action,
    risk_budget,
)


def test_per_trade_risk_by_tier():
    assert per_trade_risk_pct(1) == 0.015
    assert per_trade_risk_pct(2) == 0.010
    assert per_trade_risk_pct(3) == 0.005


def test_position_risk_dollars():
    # Tier 1 on a $10k account risks 1.5% = $150.
    assert position_risk(10_000, 1) == 150.0
    assert position_risk(10_000, 3) == 50.0


def test_exposure_caps_match_framework():
    assert MAX_EXPOSURE == {
        "JPY": 0.25, "USD": 0.25, "EUR": 0.20, "METALS": 0.15, "EM_FX": 0.15,
    }


def test_check_group_exposure_within_and_over():
    assert check_group_exposure("JPY", 0.20)["within_cap"] is True
    over = check_group_exposure("EUR", 0.25)
    assert over["within_cap"] is False
    assert over["headroom"] == pytest.approx(-0.05)


def test_group_aliases():
    assert check_group_exposure("EM FX", 0.10)["group"] == "EM_FX"
    assert check_group_exposure("gold", 0.10)["group"] == "METALS"


def test_aggregate_exposure_reports_breaches():
    res = aggregate_exposure({"JPY": 0.30, "USD": 0.10})
    assert res["all_within_caps"] is False
    assert res["breaches"] == ["JPY"]


def test_drawdown_ladder():
    assert drawdown_action(0.03)["triggered"] is False
    assert drawdown_action(0.03)["risk_multiplier"] == 1.0
    assert drawdown_action(0.05)["risk_multiplier"] == 0.75
    assert drawdown_action(0.12)["risk_multiplier"] == 0.50
    assert drawdown_action(0.15)["action"] == "Close weakest positions"
    assert drawdown_action(0.25)["risk_multiplier"] == 0.0
    assert drawdown_action(0.25)["action"] == "Portfolio reset"


def test_drawdown_rejects_negative():
    with pytest.raises(ValueError):
        drawdown_action(-0.01)


def test_risk_budget_scales_with_drawdown():
    base = risk_budget(10_000, drawdown_pct=0.0)
    assert base.per_trade[1] == 150.0
    cut = risk_budget(10_000, drawdown_pct=0.10)
    # 50% de-risk halves every per-trade figure.
    assert cut.risk_multiplier == 0.50
    assert cut.per_trade[1] == 75.0
