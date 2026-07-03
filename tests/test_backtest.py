import math

from backtest.engine import simulate, compare, _stats, VARIANTS


def _wave(n=200, amp=5.0, period=15, base=100.0):
    """Oscillating series that repeatedly stretches ±2SD → generates fades."""
    bars = []
    for i in range(n):
        c = base + amp * math.sin(2 * math.pi * i / period)
        o = base + amp * math.sin(2 * math.pi * (i - 0.5) / period)
        h = max(o, c) + 0.3
        l = min(o, c) - 0.3
        bars.append((o, h, l, c))
    return bars


def test_stats_empty():
    s = _stats([])
    assert s["trades"] == 0 and s["expectancy_r"] is None


def test_stats_basic():
    s = _stats([1.0, -1.0, 2.0])
    assert s["trades"] == 3 and s["wins"] == 2 and s["losses"] == 1
    assert s["total_r"] == 2.0 and s["profit_factor"] == 3.0


def test_simulate_runs_and_produces_stats():
    r = simulate(_wave(), "baseline")
    assert r["variant"] == "baseline" and "win_rate" in r and r["trades"] >= 0


def test_all_variants_run():
    for v in VARIANTS:
        assert simulate(_wave(), v)["variant"] == v


def test_structure_filter_never_adds_trades():
    bars = _wave()
    base = simulate(bars, "baseline")["trades"]
    struct = simulate(bars, "atr_struct")["trades"]
    assert struct <= base


def test_compare_shape():
    out = compare(_wave())
    assert set(out["variants"]) == set(VARIANTS)
    assert "atr" in out["lift_vs_baseline"] and "atr_struct" in out["lift_vs_baseline"]


def test_invalid_variant_raises():
    try:
        simulate(_wave(), "nope")
        assert False
    except ValueError:
        pass
