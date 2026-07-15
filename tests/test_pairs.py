"""Gold–silver ratio mean-reversion engine + train/test backtest."""

import math

from indicators.pairs import ratio_series, rolling_z, drift, pair_signal
from backtest.pairs_backtest import backtest


def test_ratio_and_rolling_z():
    g = [80, 82, 78, 81, 79, 100]     # last bar spikes the ratio
    s = [1.0] * 6
    r = ratio_series(g, s)
    assert r == [80, 82, 78, 81, 79, 100]
    z = rolling_z(r, 5)
    assert z[0] is None and z[3] is None       # warm-up
    assert z[-1] is not None and z[-1] > 1.5    # the spike is a high z


def test_pair_signal_rich_ratio_shorts_gold():
    # ratio stretched HIGH → short gold / long silver
    g = [80.0] * 30 + [92.0]                    # gold jumps, ratio rich
    s = [1.0] * 31
    sig = pair_signal(g, s, lookback=20, entry_z=2.0, trend_guard=False)
    assert sig["signal"] == "SHORT_RATIO"
    assert sig["legs"] == {"XAU/USD": "sell", "XAG/USD": "buy"}
    assert sig["z"] >= 2.0


def test_pair_signal_cheap_ratio_buys_gold():
    g = [80.0] * 30 + [70.0]                    # gold drops, ratio cheap
    s = [1.0] * 31
    sig = pair_signal(g, s, lookback=20, entry_z=2.0, trend_guard=False)
    assert sig["signal"] == "LONG_RATIO"
    assert sig["legs"] == {"XAU/USD": "buy", "XAG/USD": "sell"}


def test_trend_guard_blocks_structural_drift():
    # a steadily rising ratio (structural trend) → guard suppresses the fade
    g = [70.0 + i * 0.5 for i in range(130)]    # +~90% drift over the window
    s = [1.0] * 130
    sig = pair_signal(g, s, lookback=20, entry_z=1.0, trend_guard=True,
                      trend_window=100, max_drift=0.15)
    assert sig["signal"] == "FLAT" and sig["regime_ok"] is False
    # with the guard off, the same data would try to trade
    off = pair_signal(g, s, lookback=20, entry_z=1.0, trend_guard=False)
    assert off["regime_ok"] is True


def test_drift_gauge():
    assert drift([100, 110], 1) == 0.1
    assert drift([100], 5) is None


# ---- backtest --------------------------------------------------------------

def _ar1_series(n=400, base=80.0, phi=0.85, sigma=1.0, seed=42):
    """AR(1) mean-reverting ratio (base + phi·dev + noise) — the canonical process
    pairs trading exploits. A long-enough lookback profits as deviations revert."""
    import random
    random.seed(seed)
    x, g, dates = base, [], []
    for i in range(n):
        x = base + phi * (x - base) + random.gauss(0, sigma)
        g.append(x)
        dates.append(f"{2024 + i // 360}-{1 + (i // 30) % 12:02d}-{1 + i % 28:02d}")
    return dates, g, [1.0] * n


def test_backtest_profits_on_mean_reverting_ratio():
    dates, gold, silver = _ar1_series()
    r = backtest(dates, gold, silver, lookback=20, entry_z=1.5, exit_z=0.3,
                 stop_z=3.5, trend_guard=False)
    ov = r["overall"]
    assert ov["trades"] >= 10
    assert ov["hit_rate"] >= 0.7                  # mean reversion is high-hit
    assert ov["total_return_pct"] > 0
    # and it HOLDS out-of-sample — the whole point of the split
    assert r["test"]["expectancy_pct"] is not None and r["test"]["expectancy_pct"] > 0
    assert r["train"]["trades"] + r["test"]["trades"] == ov["trades"]


def test_backtest_reports_train_test_and_by_side():
    dates, gold, silver = _ar1_series()
    r = backtest(dates, gold, silver, lookback=20, entry_z=1.5, trend_guard=False)
    assert "train" in r and "test" in r and "by_side" in r
    assert r["test"]["from"] is not None
    assert set(r["by_side"]) == {"long_ratio", "short_ratio"}
