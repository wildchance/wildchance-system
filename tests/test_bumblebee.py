"""Bumblebee — intra-session sweep-and-continuity scalper."""

import pytest

from gold import bumblebee as gbb


def _b(hour, o, h, l, c):
    return {"hour": hour, "open": o, "high": h, "low": l, "close": c}


# --- anchor range + sweep -----------------------------------------------------

def test_anchor_range_at_session_open():
    bars = [_b(23, 4050, 4060, 4045, 4055), _b(0, 4055, 4075, 4050, 4060),   # London anchor
            _b(1, 4060, 4065, 4040, 4048)]
    rng = gbb.anchor_range(bars, 0)
    assert rng["high"] == 4075 and rng["low"] == 4050


def test_detect_sweep_high_then_reclaim():
    # after the range, price sweeps above the high then closes back inside
    after = [_b(1, 4070, 4090, 4068, 4072)]     # high 4090 > 4075, close 4072 < 4075
    sw = gbb.detect_sweep(4075, 4050, after)
    assert sw["side"] == "high" and sw["reclaim"] is True


def test_detect_sweep_low():
    after = [_b(1, 4055, 4058, 4030, 4035)]     # low 4030 < 4050
    sw = gbb.detect_sweep(4075, 4050, after)
    assert sw["side"] == "low"


# --- continuity call (HTF confluence) -----------------------------------------

def test_continuity_sweep_high_bearish_htf_sells():
    call = gbb.continuity_call("high", "short")
    assert call["signal"] == "SELL" and call["confluence"] is True


def test_continuity_sweep_high_bullish_htf_waits():
    call = gbb.continuity_call("high", "long")
    assert call["signal"] == "WAIT"


def test_continuity_sweep_low_bullish_buys():
    call = gbb.continuity_call("low", "long")
    assert call["signal"] == "BUY" and call["confluence"] is True


# --- asian bias ---------------------------------------------------------------

def test_asian_bias_down_day():
    # in the 2-5 window the LOW prints after the HIGH → down-day bias
    bars = [_b(2, 4100, 4120, 4098, 4115), _b(3, 4115, 4118, 4090, 4095),
            _b(4, 4095, 4096, 4070, 4075), _b(5, 4075, 4080, 4060, 4065)]
    ab = gbb.asian_bias(bars)
    assert ab["bias"] == "short"


# --- full scan ----------------------------------------------------------------

def test_bumblebee_scan_newyork_sell():
    bars = [_b(7, 4060, 4075, 4055, 4070),      # NY anchor range
            _b(8, 4070, 4090, 4068, 4072),      # 08:00 sweeps the high, reclaims
            _b(9, 4072, 4074, 4030, 4035)]      # 09:00 continuity down
    scan = gbb.bumblebee_scan(bars, now_hour=9, htf_bias="short", session="newyork")
    assert scan["session"] == "newyork"
    assert scan["sweep"]["side"] == "high"
    assert scan["continuity"]["signal"] == "SELL" and scan["continuity"]["confluence"]


def test_phase_for_hour():
    assert gbb.phase_for_hour(0)["session"] == "london"
    assert gbb.phase_for_hour(8)["phase"] == "sweep"
    assert gbb.phase_for_hour(9)["phase"] == "continuity"
    assert gbb.phase_for_hour(3)["session"] == "asian"
    assert gbb.phase_for_hour(12) is None


def test_format_bumblebee_line():
    scan = gbb.bumblebee_scan(
        [_b(7, 4060, 4075, 4055, 4070), _b(8, 4070, 4090, 4068, 4072),
         _b(9, 4072, 4074, 4030, 4035)], now_hour=9, htf_bias="short", session="newyork")
    line = gbb.format_bumblebee(scan)
    assert line and "BUMBLEBEE" in line and "SELL" in line
