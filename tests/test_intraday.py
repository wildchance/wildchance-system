"""Tests for the intraday z-score breach detector (pure engine)."""

import pytest

from intraday.engine import scan_closes, z_at, _sample_sd


def test_sample_sd_matches_manual():
    # values 2,4,4,4,5,5,7,9 -> known sample sd ~2.138
    assert _sample_sd([2, 4, 4, 4, 5, 5, 7, 9]) == pytest.approx(2.13809, rel=1e-4)


def test_z_at_basic():
    closes = [10] * 19 + [20]          # last bar far above a flat-ish window
    z = z_at(closes, -1, 20)
    assert z is not None and z > 0


def test_z_at_none_when_insufficient():
    assert z_at([1, 2, 3], -1, 20) is None


def test_z_at_none_on_flat_window():
    assert z_at([5] * 20, -1, 20) is None     # zero variance -> undefined


def test_no_breach_when_inside_band():
    # gentle oscillation, never 2 sd out
    closes = [100 + (i % 2) for i in range(40)]
    b = scan_closes(closes, window=20, z_threshold=2.0)
    assert not b.fired
    assert not b.fresh


def test_fresh_breach_fires_once_on_crossing():
    # 20 flat-ish bars then a spike on the very last bar
    base = [100 + (0.1 if i % 2 else -0.1) for i in range(21)]
    base[-1] = 110                                    # big stretch up now
    b = scan_closes(base, window=20, z_threshold=2.0)
    assert b.fired
    assert b.fresh                                    # prior bar was inside band
    assert b.direction == "overbought"
    assert b.lean == "SELL"
    assert b.z and b.z > 0


def test_oversold_breach_is_buy_lean():
    base = [100 + (0.1 if i % 2 else -0.1) for i in range(21)]
    base[-1] = 90
    b = scan_closes(base, window=20, z_threshold=2.0)
    assert b.fired and b.direction == "oversold" and b.lean == "BUY"
    assert b.z < 0


def test_not_fresh_when_already_breached_prior_bar():
    # both last two bars are stretched -> fired but NOT fresh (debounce)
    base = [100 + (0.1 if i % 2 else -0.1) for i in range(22)]
    base[-2] = 110
    base[-1] = 111
    b = scan_closes(base, window=20, z_threshold=2.0)
    assert b.fired
    assert not b.fresh


def test_too_few_bars_no_fire():
    b = scan_closes([100, 101, 102], window=20)
    assert not b.fired and not b.fresh


def test_to_dict_shape():
    d = scan_closes([100 + (0.1 if i % 2 else -0.1) for i in range(21)]).to_dict()
    for k in ("fired", "fresh", "z", "direction", "lean", "last_close"):
        assert k in d
