"""Tests for the PD-array engine (FVGs, order blocks, near-level matching)."""

import pytest

from pdarrays.engine import (
    detect_fvgs, detect_order_blocks, detect_pd_arrays, arrays_near,
)


def C(dt, o, h, l, c):
    return {"datetime": dt, "open": o, "high": h, "low": l, "close": c}


def test_bullish_fvg():
    # c0.high (100) < c2.low (102) -> bullish gap [100, 102]
    candles = [
        C("t0", 99, 100, 98, 99.5),
        C("t1", 100, 105, 99, 104),     # displacement up
        C("t2", 104, 106, 102, 105),
    ]
    fvgs = detect_fvgs(candles)
    assert len(fvgs) == 1
    assert fvgs[0].direction == "bullish"
    assert fvgs[0].bottom == 100 and fvgs[0].top == 102


def test_bearish_fvg():
    # c0.low (102) > c2.high (100) -> bearish gap [100, 102]
    candles = [
        C("t0", 103, 104, 102, 102.5),
        C("t1", 102, 103, 97, 98),      # displacement down
        C("t2", 98, 100, 96, 97),
    ]
    fvgs = detect_fvgs(candles)
    assert len(fvgs) == 1
    assert fvgs[0].direction == "bearish"
    assert fvgs[0].bottom == 100 and fvgs[0].top == 102


def test_bullish_order_block():
    # down candle, then next closes above its high -> bullish OB on the down candle
    candles = [
        C("t0", 101, 101.5, 99.5, 100),   # down candle (close<open), range [99.5,101.5]
        C("t1", 100, 104, 100, 103.5),    # closes above 101.5 -> displacement up
    ]
    obs = detect_order_blocks(candles)
    assert len(obs) == 1
    assert obs[0].direction == "bullish"
    assert obs[0].bottom == 99.5 and obs[0].top == 101.5


def test_bearish_order_block():
    candles = [
        C("t0", 99, 100.5, 98.5, 100),    # up candle (close>open), range [98.5,100.5]
        C("t1", 100, 100, 96, 97),        # closes below 98.5 -> displacement down
    ]
    obs = detect_order_blocks(candles)
    assert len(obs) == 1
    assert obs[0].direction == "bearish"


def test_no_false_positives():
    flat = [C(f"t{i}", 100, 100.5, 99.5, 100) for i in range(5)]
    assert detect_fvgs(flat) == []
    assert detect_order_blocks(flat) == []


def test_arrays_near_level():
    candles = [
        C("t0", 99, 100, 98, 99.5),
        C("t1", 100, 105, 99, 104),
        C("t2", 104, 106, 102, 105),     # bullish FVG [100,102]
    ]
    arrays = detect_pd_arrays(candles)
    # level 101 sits inside the [100,102] gap
    near = arrays_near(arrays, level=101.0, tol=0.1)
    assert any(a.kind == "FVG" for a in near)
    # level far away -> nothing
    assert arrays_near(arrays, level=200.0, tol=0.1) == []
