"""Tests for the DXY correlation / divergence + COT-extreme engine."""

import pytest

from correlation.engine import (
    returns, pearson, dxy_alignment, dxy_direction, signal_vs_dxy,
    cot_rank, cot_extreme,
)


def test_pearson_perfect():
    a = [0.01, -0.02, 0.03, -0.01]
    assert pearson(a, a) == pytest.approx(1.0)
    assert pearson(a, [-x for x in a]) == pytest.approx(-1.0)


def test_pearson_guards():
    assert pearson([1], [1]) is None
    assert pearson([1, 1, 1], [2, 2, 2]) is None    # zero variance


def test_dxy_alignment_confirming_inverse():
    # Alternating series → deterministic correlation.
    instr = [100, 102, 100, 102, 100]      # up/down/up/down
    dxy = [100, 98, 100, 98, 100]          # the mirror → negative corr
    a = dxy_alignment(instr, dxy, dxy_sign=-1)
    assert a["actual"] == "negative"
    assert a["state"] == "confirming"      # negative corr matches dxy_sign -1


def test_dxy_alignment_diverging():
    instr = [100, 102, 100, 102, 100]
    dxy = [100, 102, 100, 102, 100]        # same path → positive corr
    a = dxy_alignment(instr, dxy, dxy_sign=-1)
    assert a["actual"] == "positive"
    assert a["state"] == "diverging"       # expected negative, got positive


def test_dxy_direction():
    assert dxy_direction([100, 101]) == "up"
    assert dxy_direction([100, 99]) == "down"
    assert dxy_direction([100, 100.01]) == "flat"


def test_signal_vs_dxy():
    # DXY up, USD/JPY (dxy_sign +1) should rise -> a BUY is confirmed
    assert signal_vs_dxy("BUY", dxy_sign=+1, dxy_dir="up") == "confirmed"
    assert signal_vs_dxy("SELL", dxy_sign=+1, dxy_dir="up") == "divergent"
    # DXY up, EUR/USD (dxy_sign -1) should fall -> a SELL is confirmed
    assert signal_vs_dxy("SELL", dxy_sign=-1, dxy_dir="up") == "confirmed"
    assert signal_vs_dxy("LONG", dxy_sign=-1, dxy_dir="up") == "divergent"
    # crosses / flat -> neutral
    assert signal_vs_dxy("BUY", dxy_sign=0, dxy_dir="up") == "neutral"
    assert signal_vs_dxy("BUY", dxy_sign=1, dxy_dir="flat") == "neutral"


def test_cot_rank_and_extreme():
    series = list(range(20))               # latest is the max
    assert cot_rank(series) == pytest.approx(1.0)
    is_ext, side, r = cot_extreme(series)
    assert is_ext and side == "long_extreme"
    series2 = list(range(20))[::-1]        # latest is the min
    is_ext2, side2, _ = cot_extreme(series2)
    assert is_ext2 and side2 == "short_extreme"
    mid = [5, 1, 9, 4, 6, 5]               # latest 5 is middling
    assert cot_extreme(mid)[0] is False
