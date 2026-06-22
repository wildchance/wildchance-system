"""Tests for the USD/JPY mean-reversion engine — checks faithfulness to the
FROZEN RULES and the workbook arithmetic."""

import statistics

import pytest

from usdjpy.engine import (
    FROZEN_RULES,
    evaluate_close,
    rolling_stats,
    result_r,
    stop_breached,
    build_scoreboard,
    is_new_stretch,
    realized_r,
)


def test_frozen_rules_unchanged():
    # These are locked. If this test ever needs editing to pass, stop and think.
    assert FROZEN_RULES["lookback"] == 20
    assert FROZEN_RULES["z_entry_buy"] == -2.0
    assert FROZEN_RULES["z_entry_sell"] == 2.0
    assert FROZEN_RULES["stop_sd_multiple"] == 0.5
    assert FROZEN_RULES["hold_trading_days"] == 3


def test_rolling_stats_needs_20():
    assert rolling_stats([1.0] * 19) == (None, None)
    ma, sd = rolling_stats([float(i) for i in range(20)])
    assert ma == pytest.approx(9.5)
    # sample stdev (n-1) to match Excel STDEV
    assert sd == pytest.approx(statistics.stdev(range(20)))


def test_no_trade_when_inside_band():
    # Spread of values around 100; last close is well within +/-2 sigma.
    closes = [99.0, 101.0] * 10  # 20 closes, mean 100, last = 101 -> z ~ 0.97
    sig = evaluate_close(closes)
    assert -2.0 < sig.z < 2.0
    assert sig.action == "NO TRADE"
    assert sig.entry is None and sig.stop is None


def test_buy_fires_below_minus_two():
    # 19 closes at 100, last one far below -> z <= -2
    closes = [100.0] * 19 + [90.0]
    sig = evaluate_close(closes)
    assert sig.action == "BUY"
    assert sig.z <= -2.0
    assert sig.entry == 90.0
    # stop is 0.5*SD below entry for a buy
    assert sig.stop == pytest.approx(90.0 - 0.5 * sig.sd20)
    assert sig.stop < sig.entry


def test_sell_fires_above_plus_two():
    closes = [100.0] * 19 + [110.0]
    sig = evaluate_close(closes)
    assert sig.action == "SELL"
    assert sig.z >= 2.0
    assert sig.stop == pytest.approx(110.0 + 0.5 * sig.sd20)
    assert sig.stop > sig.entry


def test_result_r_buy_clean_stopout_is_minus_one():
    # exit at stop -> exactly -1R
    assert result_r("BUY", entry=100.0, stop=99.0, exit_price=99.0) == pytest.approx(-1.0)
    # exit one risk-unit in favour -> +1R
    assert result_r("BUY", entry=100.0, stop=99.0, exit_price=101.0) == pytest.approx(1.0)


def test_result_r_sell():
    assert result_r("SELL", entry=100.0, stop=101.0, exit_price=101.0) == pytest.approx(-1.0)
    assert result_r("SELL", entry=100.0, stop=101.0, exit_price=99.0) == pytest.approx(1.0)


def test_stop_breached_buy():
    assert stop_breached("BUY", stop=99.0, closes_during_hold=[99.5, 98.9]) is True
    assert stop_breached("BUY", stop=99.0, closes_during_hold=[99.5, 99.2]) is False


def test_scoreboard_verdict_inconclusive_under_20():
    sb = build_scoreboard([1.0, -1.0, 1.0])
    assert sb.trades_completed == 3
    assert "INCONCLUSIVE" in sb.verdict


def test_scoreboard_pass_path():
    # 20+ trades, profit factor > 1.2, positive total R
    rs = [2.0] * 15 + [-1.0] * 8  # gross win 30, gross loss -8 -> pf 3.75
    sb = build_scoreboard(rs)
    assert sb.trades_completed == 23
    assert sb.profit_factor == pytest.approx(30 / 8)
    assert sb.verdict.startswith("PASS")


def test_scoreboard_fail_path():
    rs = [1.0] * 5 + [-1.0] * 20  # pf 5/20 = 0.25 < 1.0
    sb = build_scoreboard(rs)
    assert sb.profit_factor < 1.0
    assert sb.verdict.startswith("FAIL")


def test_workbook_numbers_match():
    # First 20 closes from the workbook DAILY LOG (rows 5..24).
    closes = [157.088, 157.248, 157.896, 156.37, 156.938, 156.686, 157.226,
              157.615, 157.894, 158.384, 158.776, 158.858, 159.08, 158.934,
              158.99, 159.206, 158.929, 159.314, 159.518, 159.246]
    sig = evaluate_close(closes, "2026-05-27")
    # Matches the sheet: MA ~158.21, SD ~1.005, z ~1.03 -> no trade yet.
    assert sig.ma20 == pytest.approx(158.2098, abs=1e-3)
    assert sig.sd20 == pytest.approx(1.0053, abs=1e-3)
    assert sig.z == pytest.approx(1.0307, abs=1e-3)
    assert sig.action == "NO TRADE"


def test_is_new_stretch_first_trade_has_no_priors():
    assert is_new_stretch(5, []) is True


def test_is_new_stretch_blocks_within_hold_window():
    # 06-19 entered at index 18; 06-20 is index 19 -> 1 day later, within HOLD(3).
    assert is_new_stretch(19, [18]) is False        # the live 06-20 case
    assert is_new_stretch(20, [18]) is False         # +2 days, still covered
    assert is_new_stretch(21, [18]) is False         # +3 days == HOLD, still covered


def test_is_new_stretch_allows_after_hold_window():
    # Resume scanning at entry+HOLD+1 (= index 22), matching the i=end+1 backtest.
    assert is_new_stretch(22, [18]) is True


def test_is_new_stretch_ignores_priors_on_or_after_entry():
    assert is_new_stretch(18, [18]) is True          # same index handled elsewhere
    assert is_new_stretch(18, [20]) is True          # a later entry can't cover this one


def test_is_new_stretch_uses_nearest_prior():
    # Multiple priors: blocked if ANY earlier trade still covers entry_idx.
    assert is_new_stretch(23, [10, 21]) is False     # 21 covers 23 (diff 2)
    assert is_new_stretch(25, [10, 21]) is True      # nearest prior 21 is 4 days back


def test_realized_r_collapses_breached_to_minus_one():
    # A profitable day+3 R that nonetheless breached the stop -> stop-out -1R.
    assert realized_r(0.8, True) == -1.0
    assert realized_r(2.0, True) == -1.0


def test_realized_r_keeps_faithful_when_not_breached():
    assert realized_r(0.8, False) == 0.8
    assert realized_r(-0.5, False) == -0.5


def test_realized_r_passes_none_through():
    assert realized_r(None, True) is None
    assert realized_r(None, False) is None
