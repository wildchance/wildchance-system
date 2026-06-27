"""Tests for the COT release-cadence freshness / discount engine."""

from datetime import date

import pytest

from cot.freshness import (
    freshness, cot_as_of, most_recent_tuesday,
    FRESH_DAYS, STALE_DAYS, DISCOUNT_FLOOR,
)


def test_most_recent_tuesday():
    # 2026-06-24 is a Wednesday -> most recent Tuesday is 2026-06-23
    assert most_recent_tuesday(date(2026, 6, 24)) == date(2026, 6, 23)
    # On a Tuesday it returns that day
    assert most_recent_tuesday(date(2026, 6, 23)) == date(2026, 6, 23)
    # On a Monday it returns the previous Tuesday (6 days back)
    assert most_recent_tuesday(date(2026, 6, 22)) == date(2026, 6, 16)


def test_as_of_on_release_friday_is_that_weeks_tuesday():
    # Friday 2026-06-26: this week's Tuesday (06-23) data is released today
    assert cot_as_of(date(2026, 6, 26)) == date(2026, 6, 23)


def test_as_of_midweek_before_release_uses_last_week():
    # Wednesday 2026-06-24: this week's Tuesday hasn't released yet (Fri) ->
    # latest available is the prior Tuesday 2026-06-16
    assert cot_as_of(date(2026, 6, 24)) == date(2026, 6, 16)


def test_freshness_on_release_day_is_three_days_and_full_weight():
    f = freshness(date(2026, 6, 26))           # Friday, as_of Tue 06-23
    assert f["age_days"] == FRESH_DAYS
    assert f["stale"] is False
    assert f["discount"] == 1.0


def test_freshness_grows_stale_late_in_cycle():
    # Thursday 2026-07-02: latest release is Tue 06-23 (next release is 07-03)
    f = freshness(date(2026, 7, 2))
    assert f["age_days"] >= STALE_DAYS
    assert f["stale"] is True
    assert f["discount"] < 1.0
    assert f["discount"] >= DISCOUNT_FLOOR


def test_discount_never_below_floor():
    # explicit very old as_of
    f = freshness(date(2026, 7, 30), as_of=date(2026, 6, 1))
    assert f["discount"] == DISCOUNT_FLOOR


def test_explicit_as_of_overrides_calendar():
    f = freshness(date(2026, 6, 26), as_of=date(2026, 6, 23))
    assert f["as_of"] == "2026-06-23"
    assert f["age_days"] == 3
