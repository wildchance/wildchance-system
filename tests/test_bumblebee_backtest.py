"""gold.bumblebee sweep→continuity backtest (bias-gated, train/test)."""

from backtest.bumblebee_backtest import backtest


def _bar(date, h, o, hi, lo, c):
    return {"date": date, "hour": h, "open": o, "high": hi, "low": lo, "close": c}


def _sell_day(date):
    """asian session 14/15/16: swept the HIGH, continuity SELL, falls to the target."""
    return [
        _bar(date, 14, 100, 110, 90, 105),    # anchor range 90–110
        _bar(date, 15, 105, 114, 100, 108),   # sweep HIGH (114>110), closes back in
        _bar(date, 16, 108, 109, 104, 106),   # continuity open 108 → SELL, stop 114, tgt 83
        _bar(date, 17, 106, 107, 98, 100),
        _bar(date, 18, 100, 101, 88, 90),
        _bar(date, 19, 90, 91, 82, 84),        # low 82 <= target 83 → win
    ]


def test_sell_continuation_wins_and_splits():
    days = {f"2026-07-{d:02d}": _sell_day(f"2026-07-{d:02d}") for d in range(6, 14)}
    bars = [b for day in days.values() for b in day]
    r = backtest(bars, sessions=("asian",), target_pips=250, pip=0.1, max_hold=6)
    ov = r["overall"]
    assert ov["trades"] >= 6 and ov["hit_rate"] == 1.0
    assert ov["expectancy_r"] and ov["expectancy_r"] > 0
    # continuation SELL after a swept high → short side
    assert r["by_side"]["short"]["trades"] == ov["trades"]
    assert r["train"]["trades"] + r["test"]["trades"] == ov["trades"]
    assert "asian" in r["by_session"]


def test_stop_is_minus_one_r():
    # swept high → SELL, but price rips UP through the stop → -1R
    date = "2026-07-06"
    bars = [
        _bar(date, 14, 100, 110, 90, 105),
        _bar(date, 15, 105, 114, 100, 108),   # sweep high
        _bar(date, 16, 108, 109, 104, 106),   # SELL entry 108, stop 114
        _bar(date, 17, 108, 120, 107, 118),   # high 120 >= stop 114 → loss
    ]
    r = backtest(bars, sessions=("asian",), max_hold=6)
    assert r["overall"]["losses"] == 1 and r["overall"]["avg_loss_r"] == -1.0


def test_require_bias_suppresses_against_trend():
    # Rising context → SMA bias = long. A swept-HIGH SELL in a bullish regime is
    # "stand down" per the engine's continuity gate → require_bias drops it.
    date = "2026-07-20"
    bars = [_bar(date, h, 80 + h, 81 + h, 79 + h, 80 + h) for h in range(0, 14)]  # rising
    bars += [
        _bar(date, 14, 100, 110, 90, 108),    # anchor (above the rising SMA)
        _bar(date, 15, 108, 114, 104, 109),   # sweep high
        _bar(date, 16, 109, 110, 105, 107),   # continuity → SELL (ungated)
        _bar(date, 17, 107, 108, 95, 96),
    ]
    ungated = backtest(bars, sessions=("asian",), max_hold=6)
    gated = backtest(bars, sessions=("asian",), max_hold=6, require_bias=True, bias_window=16)
    assert ungated["overall"]["trades"] == 1
    assert gated["overall"]["trades"] == 0     # bullish bias vetoes the swept-high sell


def test_hold_to_session_marks_at_close():
    # SELL, far target never hit, held to the asian close hour (22) and marked there.
    date = "2026-07-06"
    bars = [
        _bar(date, 14, 100, 110, 90, 105),
        _bar(date, 15, 105, 114, 100, 108),   # sweep high
        _bar(date, 16, 108, 109, 104, 106),   # SELL entry 108, stop 114
    ]
    for h in range(17, 23):                    # drift down, never 250 pips, to hour 22
        bars.append(_bar(date, h, 104, 105, 101, 103))
    r = backtest(bars, sessions=("asian",), target_pips=250, pip=0.1, hold_to_session=True)
    assert r["overall"]["trades"] == 1
    t = r["trades"][0]
    assert t["outcome"] == "win" and t["r"] > 0   # 103 < entry 108 on a short → small win
