"""Tests for the prop-firm risk gate + session grid (TENDAJI rules)."""

import datetime as _dt

import pytest

from propfirm.engine import (
    risk_limits, max_lot, evaluate_trade, active_sessions, grid_state, lockin,
)


# --- limits match the cheat sheet exactly ------------------------------------

def test_limits_2500_match_sheet():
    lim = risk_limits(2500)
    assert lim["daily_target_min"] == pytest.approx(7.5)
    assert lim["daily_target_max"] == pytest.approx(15.0)
    assert lim["daily_loss_cap"] == pytest.approx(7.5)
    assert lim["daily_loss_soft"] == pytest.approx(3.75)
    assert lim["weekly_target_max"] == pytest.approx(75.0)
    assert lim["monthly_target_max"] == pytest.approx(300.0)


def test_max_lot_matches_sheet():
    assert max_lot(1250) == 0.01
    assert max_lot(2500) == 0.02
    assert max_lot(5000) == 0.04
    assert max_lot(10000) == 0.08


def test_target_tiers_scale_up():
    base = risk_limits(2500, "6")
    mod = risk_limits(2500, "8")
    agg = risk_limits(2500, "12")
    assert base["tier"] == "6"
    assert base["monthly_target_min"] == pytest.approx(150.0)   # 6%
    assert mod["monthly_target_min"] == pytest.approx(200.0)    # 8%
    assert agg["monthly_target_min"] == pytest.approx(300.0)    # 12%
    assert agg["max_drawdown_usd"] > base["max_drawdown_usd"]


def test_unknown_tier_falls_back_to_base():
    assert risk_limits(2500, "99")["tier"] == "6"


def test_gate_respects_higher_tier_budget():
    blocked = evaluate_trade(2500, 9.0, day_pnl_usd=0, open_risk_usd=0, tier="6")
    allowed = evaluate_trade(2500, 9.0, day_pnl_usd=0, open_risk_usd=0, tier="12")
    assert blocked["allow"] is False        # 9 > 7.5 (tier 6 cap)
    assert allowed["allow"] is True         # 9 < 15 (tier 12 cap)


# --- the risk gate -----------------------------------------------------------

def test_gate_allows_within_budget():
    r = evaluate_trade(2500, proposed_risk_usd=2.0, day_pnl_usd=0, open_risk_usd=0)
    assert r["allow"] is True
    assert r["remaining_budget"] == pytest.approx(5.5)   # 7.5 cap - 2.0


def test_gate_blocks_when_daily_loss_hit():
    r = evaluate_trade(2500, 1.0, day_pnl_usd=-7.5, open_risk_usd=0)
    assert r["allow"] is False and "loss" in r["reason"]


def test_gate_blocks_when_would_exceed_budget():
    r = evaluate_trade(2500, 5.0, day_pnl_usd=0, open_risk_usd=4.0)  # 9 > 7.5
    assert r["allow"] is False and "budget" in r["reason"]


def test_gate_locks_in_at_daily_target():
    r = evaluate_trade(2500, 1.0, day_pnl_usd=15.0, open_risk_usd=0)
    assert r["allow"] is False and "target" in r["reason"]


# --- session grid ------------------------------------------------------------

def test_active_sessions_returns_list():
    # 14:00 UTC ~ NY 09:00/10:00 — at least one NY session open
    now = _dt.datetime(2026, 6, 30, 14, 0, tzinfo=_dt.timezone.utc)
    act = active_sessions(now)
    assert isinstance(act, list)


def test_grid_on_off_log_split():
    now = _dt.datetime(2026, 6, 30, 14, 0, tzinfo=_dt.timezone.utc)
    active = set(active_sessions(now))
    # craft one trade in an active session and one in a definitely-closed one
    closed_session = next(k for k in ["trade1", "trade2", "trade3", "trade4",
                                      "trade5", "trade6", "cbdr", "prelondon"]
                          if k not in active)
    open_session = next(iter(active)) if active else "trade1"
    trades = [
        {"id": "A", "session": open_session, "risk_usd": 2.0, "symbol": "EUR/USD"},
        {"id": "B", "session": closed_session, "risk_usd": 1.5, "symbol": "USD/JPY"},
    ]
    g = grid_state(now, 2500, trades)
    assert g["open_risk_usd"] == pytest.approx(3.5)
    assert g["remaining_budget_usd"] == pytest.approx(4.0)   # 7.5 - 3.5
    assert "B" in g["off_log"]
    if active:
        assert "A" in g["on_log"]


def test_grid_empty():
    now = _dt.datetime(2026, 6, 30, 14, 0, tzinfo=_dt.timezone.utc)
    g = grid_state(now, 2500, [])
    assert g["open_risk_usd"] == 0.0
    assert g["remaining_budget_usd"] == pytest.approx(7.5)
    assert g["can_open_more"] is True


# --- lock-in -----------------------------------------------------------------

def test_lockin_full_size_early():
    r = lockin(0, day_pnl_usd=0, balance=2500)
    assert r["risk_scale"] == 1.0 and r["progress"] == 0.0


def test_lockin_stops_when_target_banked():
    r = lockin(50, day_pnl_usd=15.0, balance=2500)   # daily max target hit
    assert r["risk_scale"] == 0.0


def test_lockin_eases_after_min_target():
    r = lockin(100, day_pnl_usd=10.0, balance=2500)  # between min and max target
    assert 0.0 < r["risk_scale"] < 1.0
    assert r["progress"] == 1.0
