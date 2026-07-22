"""Live retracement read — SELL-the-OTE / scalp-the-bounce / LEAVE classifier."""

import pytest

from gold import retracement as gret
from cbdr.engine import build_cbdr


def _bar(o, h, l, c, t="d"):
    return (t, o, h, l, c)


# Clean DOWN impulse 4235 -> 4000, then a retrace that sweeps the 4200 pivot high
# and closes back below (reject) — the SELL-the-OTE tape.
SELL_BARS = [
    _bar(4230, 4235, 4225, 4228, "d0"),   # origin / leg high 4235 (global max)
    _bar(4228, 4232, 4180, 4185, "d1"),
    _bar(4185, 4190, 4150, 4155, "d2"),
    _bar(4155, 4160, 4140, 4145, "d3"),
    _bar(4145, 4200, 4140, 4150, "d4"),   # pivot high 4200
    _bar(4150, 4155, 4090, 4095, "d5"),
    _bar(4095, 4100, 4010, 4015, "d6"),
    _bar(4015, 4040, 4000, 4035, "d7"),   # leg low 4000 (global min)
    _bar(4035, 4120, 4030, 4110, "d8"),
    _bar(4180, 4205, 4100, 4170, "d9"),   # sweeps 4200, closes 4170 back below (bearish)
]

# UP impulse to 4130, then a pullback that sweeps the 3995 pivot low and reclaims.
SCALP_BARS = [
    _bar(4020, 4030, 4015, 4025, "d0"),
    _bar(4025, 4035, 4010, 4030, "d1"),
    _bar(4010, 4015, 3995, 4000, "d2"),   # pivot low 3995
    _bar(4000, 4050, 3998, 4045, "d3"),
    _bar(4045, 4090, 4040, 4085, "d4"),
    _bar(4085, 4130, 4080, 4125, "d5"),   # up leg high 4130
    _bar(4125, 4130, 4050, 4055, "d6"),
    _bar(4055, 4060, 4020, 4025, "d7"),
    _bar(3990, 4030, 3985, 4008, "d8"),   # sweeps 3995 low, reclaims at 4008 (bullish)
]


# --- SELL-the-OTE: down-leg retraced into OTE, swept a high, rejected ----------

def test_sell_the_ote_state():
    out = gret.retracement_state(SELL_BARS, price=4170.0, htf_bias="short")
    assert out["state"] == "SELL_OTE"
    assert out["signal"] == "SHORT" and out["size"] == "full"
    assert 0.62 <= out["retracement"] <= 0.79
    assert out["entry"] is not None and out["stop"] is not None


def test_sell_blocked_when_dxy_unlocked_bull():
    # same tape, but HTF is long AND the DXY flip has unlocked a real bull trend →
    # do NOT sell the retracement (that's the dangerous counter-trend sell).
    out = gret.retracement_state(SELL_BARS, price=4170.0, htf_bias="long",
                                 dxy_unlocked=True)
    assert out["state"] != "SELL_OTE"


# --- scalp-the-bounce: swept a low + reclaimed at a −SD extreme ----------------

def test_scalp_the_bounce_state():
    box = build_cbdr(4060.0, 4040.0)          # range 20 → −1SD 4020, −1.5SD 4010
    out = gret.retracement_state(SCALP_BARS, price=4008.0, box=box, htf_bias="neutral")
    assert out["state"] == "SCALP_BOUNCE"
    assert out["signal"] == "LONG" and out["size"] == "scalp"
    assert out["range_fade_only"] is True and out["conviction_scaled"] is False


def test_scalp_needs_extreme_or_ob():
    # swept-low reclaim but NO −SD extreme and NO buy OB → not a scalp
    out = gret.retracement_state(SCALP_BARS, price=4030.0, htf_bias="neutral")
    assert out["state"] != "SCALP_BOUNCE"


# --- LEAVE: the dangerous middle ----------------------------------------------

def test_leave_mid_retracement():
    # price sitting at ~40% retrace of the down leg, no OTE sweep → LEAVE
    out = gret.retracement_state(SELL_BARS, price=4094.0, htf_bias="short")
    assert out["state"] == "LEAVE"
    assert out["actionable"] is False
    assert 0.30 <= out["retracement"] <= 0.50


def test_too_few_bars_is_leave():
    out = gret.retracement_state([_bar(1, 2, 0.5, 1.5)] * 3)
    assert out["state"] == "LEAVE" and "need >=8" in out["reason"]


# --- format --------------------------------------------------------------------

def test_format_lines():
    out = gret.retracement_state(SELL_BARS, price=4170.0, htf_bias="short")
    line = gret.format_retracement(out)
    assert "SELL-the-OTE" in line and "SHORT" in line
