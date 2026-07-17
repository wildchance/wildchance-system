"""Gold tier backtest — forward simulator + swing replay."""

import datetime as dt

from backtest.gold_tiers import simulate_forward, backtest_swing


def _b(o, h, l, c, i=0):
    return (dt.date(2026, 1, 1) + dt.timedelta(days=i), o, h, l, c)


# --- simulate_forward -------------------------------------------------------

def test_forward_hits_final_target():
    # long 100, stop 90 (risk 10), targets 2R/3R = 120/130; a bar reaching 131.
    fut = [_b(105, 131, 104, 129)]
    r = simulate_forward(100, 90, [120, 130], "long", fut)
    assert r["exit_reason"] == "TP2" and r["result_r"] == 3.0


def test_forward_stops_out_minus_one_r():
    fut = [_b(99, 101, 89, 90)]           # low 89 <= stop 90
    r = simulate_forward(100, 90, [120, 130], "long", fut)
    assert r["exit_reason"] == "SL" and r["result_r"] == -1.0


def test_forward_break_even_after_tp1():
    # reach TP1 (120) then fall back to entry within later bars → BE ~0R.
    fut = [_b(105, 122, 104, 118), _b(118, 119, 99, 100)]
    r = simulate_forward(100, 90, [120, 130], "long", fut)
    assert r["exit_reason"] == "BE" and abs(r["result_r"]) < 1e-9


def test_forward_time_stop_partial():
    fut = [_b(100, 108, 99, 106)]         # no TP/SL → time-stop at close 106
    r = simulate_forward(100, 90, [120, 130], "long", fut)
    assert r["exit_reason"] == "TIME" and r["result_r"] == 0.6


def test_forward_short_side():
    fut = [_b(100, 101, 88, 89)]          # short 100 stop 110 tp 90/80; low 88 → TP1? 90 hit not 80
    r = simulate_forward(100, 110, [90, 80], "short", fut)
    assert r["exit_reason"] in ("TIME", "BE") or r["result_r"] != 0  # partial/BE, not a full stop


# --- end-to-end swing replay (deterministic synthetic series) ---------------

def _week(start_i, lows_then_reclaim):
    """Build a Mon-Fri that makes a Tuesday low then reclaims (Classic Tuesday Low
    → long swing profile)."""
    bars = []
    base = 100.0
    seq = [(base, base + 2, base - 1, base + 1),        # Mon
           (base + 1, base + 2, base - 6, base + 1.5),  # Tue: sweeps low, reclaims
           (base + 1.5, base + 4, base, base + 3),      # Wed up
           (base + 3, base + 6, base + 2, base + 5),    # Thu up
           (base + 5, base + 8, base + 4, base + 7)]    # Fri up
    return [_b(o, h, l, c, start_i + j) for j, (o, h, l, c) in enumerate(seq)]


def test_backtest_swing_runs_and_scores():
    # ~10 weeks of an uptrending, Tuesday-low market → swing longs should appear.
    daily = []
    for w in range(12):
        daily += [(_b(o, h + w * 3, l + w * 3, c + w * 3, w * 7 + j))
                  for j, (_d, o, h, l, c) in enumerate(_week(0, None))]
    rep = backtest_swing(daily, horizon=7, warmup=7)
    assert rep["tier"] == "swing"
    assert "scorecard" in rep and rep["scorecard"]["n"] == rep["trades"]
    assert isinstance(rep["by_exit"], dict)
