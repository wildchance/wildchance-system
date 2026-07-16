"""AMD hourly-triad engine (range→sweep→reaction) + train/test backtest."""

from indicators.amd_triad import TRIGGERS, classify, triad_signal
from backtest.amd_triad_backtest import backtest


def _bar(date, h, o, hi, lo, c):
    return {"date": date, "hour": h, "open": o, "high": hi, "low": lo, "close": c}


# ---- engine ----------------------------------------------------------------

def test_classify_four_probabilities():
    rng = _bar("2026-07-06", 14, 100, 110, 90, 105)      # range 90–110
    assert classify(rng, _bar("d", 15, 105, 112, 95, 100)) == "sweep_high"
    assert classify(rng, _bar("d", 15, 105, 108, 88, 95)) == "sweep_low"
    assert classify(rng, _bar("d", 15, 105, 112, 88, 100)) == "sweep_both"
    assert classify(rng, _bar("d", 15, 105, 109, 91, 100)) == "sweep_none"


def test_sweep_high_reject_is_short():
    rng = _bar("2026-07-06", 14, 100, 110, 90, 105)
    manip = _bar("2026-07-06", 15, 105, 114, 100, 108)     # sweeps 110 high
    react = _bar("2026-07-06", 16, 108, 109, 104, 106)     # closes back below 110
    sig = triad_signal(rng, manip, react, buffer=0.5)
    assert sig["signal"] == "SHORT"
    assert sig["entry"] == 106 and sig["stop"] == 114.5 and sig["target"] == 90


def test_sweep_low_reject_is_long():
    rng = _bar("2026-07-06", 14, 100, 110, 90, 95)
    manip = _bar("2026-07-06", 15, 95, 100, 86, 92)        # sweeps 90 low
    react = _bar("2026-07-06", 16, 92, 96, 91, 94)         # closes back above 90
    sig = triad_signal(rng, manip, react, buffer=1.0)
    assert sig["signal"] == "LONG"
    assert sig["stop"] == 85 and sig["target"] == 110


def test_sweep_high_but_stays_out_is_none():
    rng = _bar("d", 14, 100, 110, 90, 105)
    manip = _bar("d", 15, 105, 114, 100, 112)
    react = _bar("d", 16, 112, 116, 111, 113)             # still ABOVE 110 → no reversal
    assert triad_signal(rng, manip, react)["signal"] == "NONE"


# ---- backtest --------------------------------------------------------------

def _short_day(date):
    """14:00 range 90-110, 15:00 sweeps the high, 16:00 rejects, then falls to target 90."""
    return [
        _bar(date, 14, 100, 110, 90, 105),
        _bar(date, 15, 105, 114, 100, 108),   # sweep high
        _bar(date, 16, 108, 109, 104, 106),   # reject → SHORT entry 106, stop 114, tgt 90
        _bar(date, 17, 106, 107, 95, 98),
        _bar(date, 18, 98, 99, 89, 91),       # low 89 <= target 90 → win
    ]


def test_backtest_win_and_splits():
    days = {}
    for d in range(6, 14):
        days[f"2026-07-{d:02d}"] = _short_day(f"2026-07-{d:02d}")
    bars = [b for day in days.values() for b in day]
    r = backtest(bars, triggers=(14,), max_hold=6)
    ov = r["overall"]
    assert ov["trades"] >= 6 and ov["hit_rate"] == 1.0
    assert ov["expectancy_r"] and ov["expectancy_r"] > 0
    # splits present and partition the trades
    assert r["train"]["trades"] + r["test"]["trades"] == ov["trades"]
    assert "14" in r["by_trigger"]


def test_backtest_stop_is_minus_one_r():
    # sweep high, reject to SHORT, but price rips UP through the stop → -1R
    date = "2026-07-06"
    bars = [
        _bar(date, 14, 100, 110, 90, 105),
        _bar(date, 15, 105, 114, 100, 108),   # sweep high
        _bar(date, 16, 108, 109, 104, 106),   # SHORT entry 106, stop 114 (no buffer)
        _bar(date, 17, 106, 120, 105, 118),   # high 120 >= stop 114 → loss
    ]
    r = backtest(bars, triggers=(14,), max_hold=6)
    assert r["overall"]["losses"] == 1 and r["overall"]["avg_loss_r"] == -1.0


def test_backtest_by_side_and_bias_filter():
    days = {}
    for d in range(6, 14):
        days[f"2026-07-{d:02d}"] = _short_day(f"2026-07-{d:02d}")
    bars = [b for day in days.values() for b in day]
    r = backtest(bars, triggers=(14,), max_hold=6)
    # by_side split present; these are all SHORT fades (swept high)
    assert "short" in r["by_side"] and "long" in r["by_side"]
    assert r["by_side"]["short"]["trades"] == r["overall"]["trades"]
    # require_bias with a short-only downtrend proxy: these shorts sit BELOW the SMA
    rb = backtest(bars, triggers=(14,), max_hold=6, require_bias=True, bias_window=10)
    assert rb["params"]["require_bias"] is True
    # the bias filter can only reduce or keep the trade count, never increase it
    assert rb["overall"]["trades"] <= r["overall"]["trades"]


def test_triggers_default():
    assert TRIGGERS == (14, 7, 0)
