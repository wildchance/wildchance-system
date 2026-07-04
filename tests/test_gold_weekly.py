import datetime as dt

from gold.weekly import weekly_bias, confluence


def _bars(rows, start=dt.date(2026, 7, 6)):  # 2026-07-06 is a Monday
    """rows: list of (open,high,low,close) starting Monday."""
    return [(start + dt.timedelta(days=i), *r) for i, r in enumerate(rows)]


def test_no_sweep_is_neutral():
    # Mon range 100-110, Tue-Wed stay inside
    bars = _bars([(105, 110, 100, 106), (106, 109, 102, 104), (104, 108, 103, 107)])
    r = weekly_bias(bars)
    assert r["bias"] == "neutral" and r["monday_high"] == 110 and r["monday_low"] == 100


def test_sweep_high_then_reject_is_short():
    # Tue pokes above 110 then closes back to 104
    bars = _bars([(105, 110, 100, 106), (106, 112, 105, 104)])
    r = weekly_bias(bars)
    assert r["swept_high"] and r["bias"] == "short" and r["profile"] == "reversal_high_sweep"


def test_sweep_low_then_reclaim_is_long():
    bars = _bars([(105, 110, 100, 106), (104, 108, 98, 107)])
    r = weekly_bias(bars)
    assert r["swept_low"] and r["bias"] == "long" and r["profile"] == "reversal_low_sweep"


def test_continuation_up_holds_above():
    # breaks above 110 and closes above it
    bars = _bars([(105, 110, 100, 106), (108, 115, 107, 114)])
    r = weekly_bias(bars)
    assert r["bias"] == "long" and r["profile"] == "continuation_up"


def test_confluence_maps_side():
    short = {"bias": "short"}
    assert confluence(short, "sell") == "confirms"
    assert confluence(short, "long") == "diverges"
    assert confluence({"bias": "neutral"}, "long") == "neutral"


def test_empty_and_no_monday():
    assert weekly_bias([]) is None
    # only a Tuesday bar, no Monday
    tue = [(dt.date(2026, 7, 7), 100, 105, 99, 101)]
    assert weekly_bias(tue)["bias"] == "neutral"
