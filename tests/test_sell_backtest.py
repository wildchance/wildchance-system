"""Sell-setup backtest — premium-retest reject → next floor."""

from backtest.sell_backtest import backtest_sells


def _b(o, h, l, c):
    return ("t", o, h, l, c)


LEVELS = [4200.0, 4135.0]
FLOORS = [4000.0, 3885.0]


def test_winning_sell_reject_to_floor():
    bars = [
        _b(4180, 4201, 4179, 4190),   # tags 4200, closes back below → SELL @ 4200, tgt 4000
        _b(4190, 4195, 3995, 4010),   # low 3995 <= 4000 floor target → WIN
    ]
    r = backtest_sells(bars, LEVELS, FLOORS, stop_buffer=3.0)
    assert r["trades"] == 1 and r["wins"] == 1
    assert r["total_pips"] > 0 and r["win_rate"] == 100.0


def test_losing_sell_stopped_above_level():
    bars = [
        _b(4130, 4136, 4129, 4133),   # tags 4135, closes below → SELL @ 4135, stop 4138
        _b(4133, 4140, 4132, 4139),   # high 4140 >= 4138 stop → LOSS
    ]
    r = backtest_sells(bars, LEVELS, FLOORS, stop_buffer=3.0)
    assert r["trades"] == 1 and r["losses"] == 1 and r["total_pips"] < 0


def test_no_setup_when_no_reject():
    bars = [_b(4100, 4110, 4090, 4105), _b(4105, 4115, 4100, 4112)]  # never tags a level
    r = backtest_sells(bars, LEVELS, FLOORS)
    assert r["trades"] == 0 and "no sell setups" in r["note"]


def test_stats_aggregate_and_profit_factor():
    bars = [
        _b(4180, 4201, 4179, 4190), _b(4190, 4195, 3995, 4010),   # win at 4200→4000 floor
        _b(4130, 4136, 4129, 4133), _b(4133, 4140, 4132, 4139),   # loss at 4135
    ]
    r = backtest_sells(bars, LEVELS, FLOORS, stop_buffer=3.0)
    assert r["trades"] == 2 and r["wins"] == 1 and r["losses"] == 1
    assert r["win_rate"] == 50.0 and r["profit_factor"] is not None
    assert r["worst_losing_streak"] == 1
