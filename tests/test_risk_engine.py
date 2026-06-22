"""Tests for the TENDAJI risk engine — verifies every figure against the
TENDAJI RISK CHEAT SHEET."""

import pytest

from usdjpy.risk_engine import (
    ACCOUNT_SIZES,
    lot_for_account,
    risk_profile,
    risk_table,
    trade_money_risk,
    pip_value_per_lot,
    lot_to_risk,
)


# (account, lot, daily_min, daily_min_loss, daily_max, daily_max_loss,
#  weekly_min, monthly_min, monthly_max) straight from the cheat sheet.
CHEAT_SHEET = [
    (1_250, 0.01, 3.75, 1.875, 7.5, 3.75, 18.75, 75, 150),
    (2_500, 0.02, 7.5, 3.75, 15, 7.5, 37.5, 150, 300),
    (5_000, 0.04, 15, 7.5, 30, 15, 75, 300, 600),
    (10_000, 0.08, 30, 15, 60, 30, 150, 600, 1200),
    (25_000, 0.2, 75, 37.5, 150, 75, 375, 1500, 3000),
    (50_000, 0.4, 150, 75, 300, 150, 750, 3000, 6000),
    (100_000, 0.8, 300, 150, 600, 300, 1500, 6000, 12000),
    (200_000, 1.6, 600, 300, 1200, 600, 3000, 12000, 24000),
    (400_000, 3.2, 1200, 600, 2400, 1200, 6000, 24000, 48000),
    (800_000, 6.4, 2400, 1200, 4800, 2400, 12000, 48000, 96000),
]


@pytest.mark.parametrize("row", CHEAT_SHEET)
def test_cheat_sheet_row(row):
    (acct, lot, dmin, dmin_loss, dmax, dmax_loss,
     wmin, mmin, mmax) = row
    p = risk_profile(acct)
    assert p.lot_per_trade == pytest.approx(lot)
    assert p.targets["daily_min"]["profit"] == pytest.approx(dmin)
    assert p.targets["daily_min"]["max_loss"] == pytest.approx(dmin_loss)
    assert p.targets["daily_max"]["profit"] == pytest.approx(dmax)
    assert p.targets["daily_max"]["max_loss"] == pytest.approx(dmax_loss)
    assert p.targets["weekly_min"]["profit"] == pytest.approx(wmin)
    assert p.targets["monthly_min"]["profit"] == pytest.approx(mmin)
    assert p.targets["monthly_max"]["profit"] == pytest.approx(mmax)


def test_lot_formula():
    assert lot_for_account(1_250) == 0.01
    assert lot_for_account(125_000) == 1.0


def test_risk_table_covers_all_sizes():
    table = risk_table()
    assert len(table) == len(ACCOUNT_SIZES)
    assert [r["account_size"] for r in table] == ACCOUNT_SIZES


def test_pip_value():
    # ~$6.67 per pip per standard lot at 150.00
    assert pip_value_per_lot(150.0) == pytest.approx(1000 / 150.0)


def test_trade_money_risk_within_cap():
    r = trade_money_risk(10_000, stop_pips=50, usdjpy_price=159.5)
    assert r["lot"] == 0.08
    assert r["estimated_risk_usd"] > 0
    assert r["within_daily_cap"] is True


def test_trade_money_risk_flags_oversized_stop():
    # Absurdly wide stop should breach the daily cap.
    r = trade_money_risk(1_250, stop_pips=5000, usdjpy_price=150.0)
    assert r["within_daily_cap"] is False


def test_lot_to_risk_constant_dollar_risk():
    # 1% of $10,000 = $100 risk. At price 150, pip value/lot = 1000/150 = 6.667.
    # 50-pip stop -> lot = 100 / (6.667 * 50) = 0.30.
    lot = lot_to_risk(10_000, 0.01, stop_pips=50, usdjpy_price=150.0)
    assert lot == pytest.approx(0.30, abs=0.005)
    # the resulting risk should reproduce ~$100
    realized = lot * pip_value_per_lot(150.0) * 50
    assert realized == pytest.approx(100, abs=2)


def test_lot_to_risk_tighter_stop_bigger_lot():
    tight = lot_to_risk(10_000, 0.01, stop_pips=25, usdjpy_price=150.0)
    wide = lot_to_risk(10_000, 0.01, stop_pips=100, usdjpy_price=150.0)
    assert tight > wide   # same dollars risked -> tighter stop needs a bigger lot


def test_lot_to_risk_rejects_bad_input():
    with pytest.raises(ValueError):
        lot_to_risk(10_000, 0.01, stop_pips=0, usdjpy_price=150.0)
    with pytest.raises(ValueError):
        lot_to_risk(10_000, 5, stop_pips=25, usdjpy_price=150.0)  # risk_pct must be a fraction


def test_trade_money_risk_risk_based_mode():
    fixed = trade_money_risk(10_000, stop_pips=25, usdjpy_price=161.23)
    risk_based = trade_money_risk(10_000, stop_pips=25, usdjpy_price=161.23, risk_pct=0.01)
    assert fixed["sizing"] == "fixed" and fixed["lot"] == 0.08
    assert risk_based["sizing"] == "risk_based"
    # risk-based should target ~1% = $100, far above the fixed-lot $12 risk here
    assert risk_based["estimated_risk_usd"] > fixed["estimated_risk_usd"]
    assert risk_based["estimated_risk_usd"] == pytest.approx(100, abs=10)
