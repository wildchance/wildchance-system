"""gold.warthog OTE limit-fill backtest (event-driven, train/test)."""

from backtest.warthog_backtest import backtest


def _b(date, o, hi, lo, c):
    return {"date": date, "open": o, "high": hi, "low": lo, "close": c}


def _impulse(px0=3800.0, steps=10, step=15):
    """A clean rising impulse leg → warthog frames a LONG with an OTE entry below."""
    raw, px = [], px0
    for i in range(steps):
        o = px; px += step
        raw.append(_b(f"2026-01-{1 + i:02d}", o, px + 1, o - 1, px))
    return raw, px


def test_long_limit_fills_and_wins():
    raw, px = _impulse()
    for i, (lo, cl) in enumerate([(3880, 3885), (3855, 3860), (3845, 3852)]):  # dip into OTE
        raw.append(_b(f"2026-01-{11 + i:02d}", px, px + 1, lo, cl)); px = cl
    for i, (hi, cl) in enumerate([(3980, 3975), (4050, 4045)]):                 # rally
        raw.append(_b(f"2026-01-{14 + i:02d}", px, hi, px - 1, cl)); px = cl
    r = backtest(raw, window=8, rr=2.0, max_wait=8, max_hold=20)
    assert r["overall"]["trades"] >= 1
    assert r["overall"]["wins"] >= 1
    t = r["trades"][0]
    assert t["side"] == "long" and t["r"] == 2.0


def test_long_limit_fills_then_stops():
    raw, px = _impulse()
    for i, (lo, cl) in enumerate([(3880, 3885), (3855, 3860)]):                 # dip fills
        raw.append(_b(f"2026-01-{11 + i:02d}", px, px + 1, lo, cl)); px = cl
    raw.append(_b("2026-01-13", px, px + 1, 3780, 3785))                        # crash < stop
    raw.append(_b("2026-01-14", 3785, 3790, 3770, 3775))
    r = backtest(raw, window=8, rr=2.0, max_wait=8, max_hold=20)
    assert r["overall"]["trades"] >= 1
    assert any(t["outcome"] == "loss" and t["r"] == -1.0 for t in r["trades"])


def test_flat_market_no_signal():
    raw = [_b(f"2026-02-{i + 1:02d}", 3900, 3901, 3899, 3900) for i in range(20)]
    r = backtest(raw, window=10)
    assert r["overall"]["trades"] == 0


def test_splits_and_by_side_present():
    raw, px = _impulse()
    for i, (lo, cl) in enumerate([(3880, 3885), (3855, 3860), (3845, 3852)]):
        raw.append(_b(f"2026-01-{11 + i:02d}", px, px + 1, lo, cl)); px = cl
    for i, (hi, cl) in enumerate([(3980, 3975), (4050, 4045)]):
        raw.append(_b(f"2026-01-{14 + i:02d}", px, hi, px - 1, cl)); px = cl
    r = backtest(raw, window=8, rr=2.0)
    assert r["train"]["trades"] + r["test"]["trades"] == r["overall"]["trades"]
    assert "long" in r["by_side"] and "short" in r["by_side"]
