"""Seek & Destroy is now detected mid-week (both sides swept on different days)."""

import datetime as dt

from gold.ict import classify_week


def _bars(seq, start):
    return [(start + dt.timedelta(days=i), o, h, l, c) for i, (o, h, l, c) in enumerate(seq)]


def test_seek_destroy_flagged_by_wednesday():
    mon = dt.date(2026, 7, 6)                       # a Monday
    prior = _bars([(100, 101, 99, 100)] * 15, mon - dt.timedelta(days=20))
    week = [
        (mon,                         100, 112, 99, 101),   # Mon sweeps the high
        (mon + dt.timedelta(days=1),  101, 103, 98, 100),   # Tue inside
        (mon + dt.timedelta(days=2),  100, 101, 88, 95),    # Wed sweeps the low
    ]
    read = classify_week(prior + week)
    assert read["profile_id"] in (9, 10)           # Seek & Destroy
    assert read["bias"] == "neutral"
    assert "seek & destroy" in read["reason"]


def test_single_wide_bar_is_not_seek_destroy():
    mon = dt.date(2026, 7, 6)
    prior = _bars([(100, 101, 99, 100)] * 15, mon - dt.timedelta(days=20))
    # One wide Tuesday bar spans both extremes on the SAME day → not S&D.
    week = [(mon, 100, 101, 99, 100),
            (mon + dt.timedelta(days=1), 100, 112, 88, 100)]
    read = classify_week(prior + week)
    assert read["profile_id"] not in (9, 10)


def test_trending_week_is_not_seek_destroy():
    # A steady up-trend also makes its high/low on different days — but its close
    # sits AT the extreme, so it must NOT flag as S&D (the backtest caught this).
    base = 4000.0
    daily = []
    start = dt.date(2026, 6, 1)                    # Monday
    for day in range(10):
        d = start + dt.timedelta(days=day)
        if d.weekday() >= 5:
            continue
        daily.append((d, base, base + 12, base - 4, base + 8))
        base += 8
    read = classify_week(daily)                    # mid-second-week
    assert read["profile_id"] not in (9, 10)
