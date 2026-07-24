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


# --- Venom quarterly-month layer + sweep expectation --------------------------

def test_quarterly_month_phase():
    assert gv.month_in_quarter(2) == 2 and gv.quarterly_phase(2) == "manipulation"
    assert gv.month_in_quarter(3) == 3 and gv.quarterly_phase(3) == "distribution_aggressive"
    assert gv.month_in_quarter(5) == 2 and gv.quarterly_phase(5) == "manipulation"  # May
    assert gv.quarterly_phase(6) == "distribution_aggressive"                       # June


def test_venom_htf_manipulation_window():
    # August = month 2 of Q3 → quarterly manipulation; week-2 also manipulation
    r = gv.venom_read(_dt.datetime(2026, 8, 11, 2, 0))   # 2026-08-11 Tue, week 2
    assert r["quarterly"]["phase"] == "manipulation"
    assert r["confluence"]["htf_manipulation_window"] is True
    assert "FAILED sweep" in (r["sweep_expectation"] or "")


# --- gap navigator ------------------------------------------------------------

def test_gap_navigator_down_gap_scenarios():
    from gold import gap_navigator as gg
    obs = [{"name": "ob_3987", "zone": [3980, 3994], "type": "demand"}]
    r = gg.gap_read(4059.0, 3990.0, obs, htf_bias="short")   # gapped down ~690 pips
    assert r["significant"] and r["direction"] == "down"
    names = [s["name"] for s in r["scenarios"]]
    assert "continuation" in names and "gap_fill_retest" in names


def test_gap_navigator_negligible():
    from gold import gap_navigator as gg
    r = gg.gap_read(4059.0, 4061.0)      # 20-pip gap → not significant
    assert r["significant"] is False
