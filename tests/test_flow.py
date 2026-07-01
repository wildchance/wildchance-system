"""Tests for the order-flow engine (imbalance + footprint)."""

import pytest

from flow.engine import (
    book_imbalance, depth_imbalance, classify, footprint, cumulative_delta,
)


def test_book_imbalance_basic():
    assert book_imbalance(70, 30) == pytest.approx(0.4)
    assert book_imbalance(30, 70) == pytest.approx(-0.4)
    assert book_imbalance(0, 0) == 0.0
    assert book_imbalance(50, 50) == 0.0


def test_depth_imbalance_raw_sizes_and_levels():
    bids = [10, 8, 6, 4]
    asks = [2, 2, 2, 2]
    # all levels: bid 28 vs ask 8 -> +0.5555
    assert depth_imbalance(bids, asks) == pytest.approx((28 - 8) / 36, abs=1e-3)
    # top 2 only: 18 vs 4 -> +0.6363
    assert depth_imbalance(bids, asks, levels=2) == pytest.approx((18 - 4) / 22, abs=1e-3)


def test_depth_imbalance_price_size_pairs():
    bids = [[100.0, 10], [99.9, 5]]
    asks = [[100.1, 3], [100.2, 2]]
    assert depth_imbalance(bids, asks) == pytest.approx((15 - 5) / 20)


def test_classify_thresholds():
    assert classify(0.5)["state"] == "bid_heavy" and classify(0.5)["pressure"] == "long"
    assert classify(-0.5)["state"] == "ask_heavy" and classify(-0.5)["pressure"] == "short"
    assert classify(0.1)["state"] == "balanced" and classify(0.1)["pressure"] == "neutral"


def test_footprint_delta_and_poc():
    rows = [
        {"price": 100.0, "buy": 5, "sell": 3},   # delta +2, vol 8
        {"price": 100.1, "buy": 1, "sell": 9},   # delta -8, vol 10  <- POC
        {"price": 100.2, "buy": 4, "sell": 4},   # delta 0, vol 8
    ]
    fp = footprint(rows)
    assert fp["total_delta"] == pytest.approx(-6.0)
    assert fp["poc"] == 100.1 and fp["poc_volume"] == pytest.approx(10.0)
    assert fp["bias"] == "bearish"
    assert fp["levels"][0]["delta"] == pytest.approx(2.0)


def test_footprint_empty():
    fp = footprint([])
    assert fp["total_delta"] == 0.0 and fp["poc"] is None and fp["bias"] == "flat"


def test_cumulative_delta():
    assert cumulative_delta([2, -1, 3, -4]) == [2.0, 1.0, 4.0, 0.0]
