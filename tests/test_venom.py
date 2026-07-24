"""Venom AMD clock + b2b armed-at-sweep forward mode."""

import datetime as _dt

import pytest

from gold import venom as gv
from gold import b2b as gb


# --- Venom intraday phases (UTC-4) --------------------------------------------

def test_intraday_accumulation_asian():
    assert gv.intraday_phase(16)["phase"] == "accumulation"   # 14-22
    assert gv.intraday_phase(16)["session"] == "asian"


def test_intraday_manipulation_london_wraps_midnight():
    assert gv.intraday_phase(23)["phase"] == "manipulation"   # 22-06 wraps
    assert gv.intraday_phase(2)["phase"] == "manipulation"


def test_intraday_distribution_newyork():
    assert gv.intraday_phase(9)["phase"] == "distribution"    # 06-14


# --- weekly / monthly ---------------------------------------------------------

def test_weekly_phases():
    assert gv.weekly_phase(0) == "accumulation"   # Mon
    assert gv.weekly_phase(1) == "manipulation"   # Tue
    assert gv.weekly_phase(2) == "distribution"   # Wed
    assert gv.weekly_phase(4) == "reversal"       # Fri


def test_monthly_phases():
    assert gv.monthly_phase(1) == "accumulation"
    assert gv.monthly_phase(2) == "manipulation"
    assert gv.monthly_phase(4) == "continuation_reversal"
    assert gv.week_of_month(_dt.date(2026, 7, 14)) == 2


# --- confluence ---------------------------------------------------------------

def test_venom_read_high_confluence_manipulation():
    # Tue (weekly manip) + 02:00 (intraday manip) + week 2 (monthly manip) → ×3
    now = _dt.datetime(2026, 7, 14, 2, 0)      # 2026-07-14 is a Tuesday, week 2
    r = gv.venom_read(now)
    assert r["intraday"]["phase"] == "manipulation"
    assert r["weekly"]["phase"] == "manipulation"
    assert r["monthly"]["phase"] == "manipulation"
    assert r["confluence"]["conviction"] == "high"
    assert r["confluence"]["timeframes_aligned"] == 3


def test_venom_format_line():
    r = gv.venom_read(_dt.datetime(2026, 7, 14, 2, 0))
    line = gv.format_venom(r)
    assert "VENOM" in line and "manipulation" in line.lower()


# --- b2b armed-at-sweep -------------------------------------------------------

def test_b2b_armed_fires_on_sweep():
    # latest 4H candle sweeps the prior low and reclaims → armed LONG now
    bars = [
        ("2026-07-24T00:00:00Z", 4080, 4090, 4070, 4085),   # prior liquidity
        ("2026-07-24T04:00:00Z", 4085, 4088, 4050, 4082),   # candle 1: swept low, closed back above
    ]
    r = gb.b2b_armed(bars)
    assert r["signal"] == "LONG" and r["mode"] == "armed"
    assert r["stop"] < r["entry"] < r["target"] and r["horizon_hours"] == 8


def test_b2b_armed_none_without_sweep():
    bars = [("2026-07-24T00:00:00Z", 4080, 4090, 4070, 4085),
            ("2026-07-24T04:00:00Z", 4085, 4088, 4076, 4083)]   # stayed inside prior range
    assert gb.b2b_armed(bars)["signal"] == "NONE"


def test_b2b_armed_short():
    bars = [("2026-07-24T00:00:00Z", 4080, 4090, 4070, 4085),
            ("2026-07-24T04:00:00Z", 4085, 4110, 4083, 4088)]   # swept high, closed back below
    r = gb.b2b_armed(bars)
    assert r["signal"] == "SHORT" and r["stop"] > r["entry"] > r["target"]
