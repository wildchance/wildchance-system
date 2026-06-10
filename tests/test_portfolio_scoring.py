"""Tests for the Entry Precision Engine."""

import pytest

from portfolio.scoring import (
    WEIGHTS,
    ENTRY_THRESHOLD,
    trend_score,
    momentum_score,
    positioning_score,
    volatility_score,
    score_entry,
)


def test_weights_sum_to_one_and_threshold_frozen():
    assert round(sum(WEIGHTS.values()), 6) == 1.0
    assert ENTRY_THRESHOLD == 80.0


def test_trend_wrong_side_scores_zero():
    # LONG below the 200 EMA = against the trend.
    assert trend_score("LONG", price=99.0, ema200=100.0) == 0.0
    assert trend_score("SHORT", price=101.0, ema200=100.0) == 0.0


def test_trend_aligned_with_confirming_slope_is_full():
    assert trend_score("LONG", price=101.0, ema200=100.0, ema200_slope=0.5) == 100.0
    assert trend_score("SHORT", price=99.0, ema200=100.0, ema200_slope=-0.5) == 100.0


def test_momentum_sweet_spot():
    # RSI in 55-70 and positive MACD = top momentum for a LONG.
    assert momentum_score("LONG", rsi=62, macd_hist=0.4) == 100.0
    # MACD against the direction loses the 40% MACD slice.
    assert momentum_score("LONG", rsi=62, macd_hist=-0.4) == pytest.approx(60.0)


def test_momentum_short_is_mirrored():
    # For a SHORT the sweet spot reflects to RSI 30-45.
    assert momentum_score("SHORT", rsi=38, macd_hist=-0.4) == 100.0


def test_positioning_is_contrarian_usdcnh_example():
    # USDCNH 100% retail long -> a SELL is the perfect contrarian play.
    assert positioning_score("SHORT", retail_long_pct=1.0) == 100.0
    # ...and a LONG into a 100%-long crowd scores zero.
    assert positioning_score("LONG", retail_long_pct=1.0) == 0.0


def test_positioning_rejects_bad_input():
    with pytest.raises(ValueError):
        positioning_score("LONG", retail_long_pct=1.5)


def test_volatility_compression_then_breakout_is_best():
    assert volatility_score(atr_now=0.7, atr_avg=1.0, breakout=True) == 100.0
    # Already expanded, no breakout = weak.
    assert volatility_score(atr_now=1.4, atr_avg=1.0, breakout=False) == 20.0


def test_score_entry_qualifies_above_threshold():
    s = score_entry(
        bias="LONG",
        price=101.0, ema200=100.0, ema200_slope=0.5,
        rsi=62, macd_hist=0.4,
        retail_long_pct=0.1,     # crowd short -> good for our long
        atr_now=0.7, atr_avg=1.0, breakout=True,
    )
    # All four components near-max -> total well above 80.
    assert s.total > ENTRY_THRESHOLD
    assert s.qualifies is True


def test_score_entry_blocks_against_trend():
    # Wrong side of the 200 EMA zeroes 40% of the score, so it cannot qualify.
    s = score_entry(
        bias="LONG",
        price=99.0, ema200=100.0, ema200_slope=0.5,
        rsi=62, macd_hist=0.4,
        retail_long_pct=0.1,
        atr_now=0.7, atr_avg=1.0, breakout=True,
    )
    assert s.trend == 0.0
    assert s.qualifies is False


def test_buy_sell_aliases_accepted():
    assert score_entry(
        bias="BUY",
        price=101.0, ema200=100.0,
        rsi=62, macd_hist=0.4,
        retail_long_pct=0.1,
        atr_now=0.7, atr_avg=1.0, breakout=True,
    ).bias == "LONG"
