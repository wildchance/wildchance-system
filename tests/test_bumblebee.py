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

def test_cbdr_range_and_prelondon_trigger_bias():
    # Asian CBDR box (14-20) range 4100-4120 → +SD sell-limit 4140, -SD buy-limit 4080.
    # In the 2-5 trigger window price hits the SELL limit → short day.
    bars = [_b(14, 4105, 4120, 4100, 4110), _b(17, 4110, 4118, 4102, 4112),
            _b(20, 4112, 4119, 4101, 4108),
            _b(3, 4130, 4145, 4128, 4132)]        # 4145 >= 4140 sell-limit → short
    cr = gbb.cbdr_range(bars)
    assert cr["high"] == 4120 and cr["low"] == 4100
    db = gbb.prelondon_daily_bias(cr, bars)
    assert db["bias"] == "short" and "sell-limit" in db["triggered"]
    ab = gbb.asian_bias(bars)
    assert ab["bias"] == "short"


def test_prelondon_buy_limit_triggers_long():
    bars = [_b(14, 4105, 4120, 4100, 4110), _b(20, 4112, 4119, 4101, 4108),
            _b(4, 4082, 4084, 4078, 4083)]        # 4078 <= 4080 buy-limit → long
    ab = gbb.asian_bias(bars)
    assert ab["bias"] == "long"


def test_session_timeline_present():
    tl = gbb.session_timeline()
    assert "14:00-20:00" in tl["asian_cbdr"] and "21:00" in tl["crt_1_5_9"]
    assert "02:00-05:00" in tl["prelondon_trigger"]


# --- full scan ----------------------------------------------------------------

def test_bumblebee_scan_newyork_sell():
    bars = [_b(6, 4060, 4075, 4055, 4070),      # NY anchor range (06:00)
            _b(7, 4070, 4090, 4068, 4072),      # 07:00 sweeps the high, reclaims
            _b(8, 4072, 4074, 4030, 4035)]      # 08:00 continuity down
    scan = gbb.bumblebee_scan(bars, now_hour=8, htf_bias="short", session="newyork")
    assert scan["session"] == "newyork"
    assert scan["sweep"]["side"] == "high"
    assert scan["continuity"]["signal"] == "SELL" and scan["continuity"]["confluence"]


def test_phase_for_hour():
    assert gbb.phase_for_hour(22)["session"] == "london"        # 22 anchor
    assert gbb.phase_for_hour(7)["phase"] == "sweep"            # NY 07 sweep
    assert gbb.phase_for_hour(8)["phase"] == "continuity"       # NY 08 continuity
    assert gbb.phase_for_hour(15)["session"] == "asian"         # 15 asian sweep
    assert gbb.phase_for_hour(4)["session"] == "prelondon"      # 2-5 trigger
    assert gbb.phase_for_hour(11) is None


def test_format_bumblebee_line():
    scan = gbb.bumblebee_scan(
        [_b(6, 4060, 4075, 4055, 4070), _b(7, 4070, 4090, 4068, 4072),
         _b(8, 4072, 4074, 4030, 4035)], now_hour=8, htf_bias="short", session="newyork")
    line = gbb.format_bumblebee(scan)
    assert line and "BUMBLEBEE" in line and "SELL" in line
