"""Tests for the reference-candle range model (01:00 / 13:00 UTC)."""

import pytest

from candlerange.engine import (
    candle_body, is_bullish, classify, combined_range, analyze,
)


def test_candle_body_and_direction():
    assert candle_body(100, 105) == (100, 105)
    assert candle_body(105, 100) == (100, 105)
    assert is_bullish(100, 105) is True
    assert is_bullish(105, 100) is False
    assert is_bullish(100, 100) is True       # doji counts as non-bearish


def test_inside_range():
    r = classify(100, 105, 103)
    assert r["state"] == "inside"
    assert r["break_dir"] == "none"


def test_bullish_continuation_up():
    r = classify(100, 105, 106)               # bullish body, break above
    assert r["candle_dir"] == "bullish"
    assert r["state"] == "continuation"
    assert r["break_dir"] == "up"


def test_bullish_reversal_down():
    r = classify(100, 105, 99)                # bullish body, break below
    assert r["state"] == "reversal"
    assert r["break_dir"] == "down"


def test_bearish_continuation_down():
    r = classify(105, 100, 99)                # bearish body, break below
    assert r["candle_dir"] == "bearish"
    assert r["state"] == "continuation"
    assert r["break_dir"] == "down"


def test_bearish_reversal_up():
    r = classify(105, 100, 106)               # bearish body, break above
    assert r["state"] == "reversal"
    assert r["break_dir"] == "up"


def test_combined_range_spans_all_open_close():
    assert combined_range([(100, 105), (102, 108)]) == (100, 108)
    assert combined_range([]) is None


def test_analyze_both_anchors():
    a = analyze({"open": 100, "close": 105}, {"open": 102, "close": 108}, price=110)
    assert a["anchors"]["0100"]["state"] == "continuation"   # bullish, broke up
    assert a["anchors"]["1300"]["state"] == "continuation"
    assert a["combined_range"]["low"] == 100
    assert a["combined_range"]["high"] == 108
    assert a["combined_range"]["state"] == "break_up"


def test_analyze_handles_missing_anchor():
    a = analyze(None, {"open": 102, "close": 108}, price=104)
    assert a["anchors"]["0100"] is None
    assert a["anchors"]["1300"]["state"] == "inside"
    assert a["combined_range"]["low"] == 102      # only the one body
