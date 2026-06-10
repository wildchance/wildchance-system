"""Tests for the portfolio framework data — verifies the tier allocations,
candidate biases and confidences against the framework as written."""

import pytest

from portfolio import framework as fw


def test_allocations_sum_to_100pct():
    assert fw.total_allocation() == 1.0


def test_tier_allocations():
    assert fw.tier(1).capital_allocation == 0.40
    assert fw.tier(2).capital_allocation == 0.35
    assert fw.tier(3).capital_allocation == 0.15
    assert fw.tier(4).capital_allocation == 0.10


def test_tier1_weights_sum_to_allocation():
    # The six Tier-1 position weights (8/8/6/6/6/6%) should equal the 40% tier.
    assert round(sum(fw.tier(1).weights.values()), 6) == 0.40


def test_tier1_top_conviction():
    by_pair = {c.pair: c for c in fw.tier(1).candidates}
    assert by_pair["EURJPY"].confidence == 9.0
    assert by_pair["GBPJPY"].confidence == 9.0
    assert by_pair["USDJPY"].confidence == 8.5
    assert all(c.bias == "LONG" for c in fw.tier(1).candidates)


def test_tier3_position_cap():
    assert fw.tier(3).max_position == 0.03
    assert len(fw.tier(3).candidates) == 5


def test_ranking_is_ten_deep_and_starts_eurjpy():
    assert fw.HEDGE_FUND_RANKING[0] == "EURJPY Long"
    assert len(fw.HEDGE_FUND_RANKING) == 10


def test_all_candidates_tagged_with_tier():
    cands = fw.all_candidates()
    assert {c["tier"] for c in cands} == {1, 2, 3, 4}
    assert any(c["pair"] == "XAUUSD" and c["tier"] == 4 for c in cands)


def test_unknown_tier_raises():
    with pytest.raises(ValueError):
        fw.tier(9)
