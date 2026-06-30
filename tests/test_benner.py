"""Tests for the Benner cycle macro clock."""

from benner.engine import benner_status, TOP_YEARS, BOTTOM_YEARS, PANIC_YEARS


def test_2026_is_a_top_sell_year():
    s = benner_status(2026)
    assert s["is_top"] is True
    assert s["phase"] == "top"
    assert "SELL" in s["action"]


def test_panic_year():
    s = benner_status(2019)
    assert s["is_panic"] is True and s["phase"] == "panic"


def test_bottom_year_is_buy():
    s = benner_status(2023)
    assert s["is_bottom"] is True and s["phase"] == "bottom"
    assert "BUY" in s["action"]


def test_countdown_fields():
    s = benner_status(2026)
    # next bottom after 2026 is 2032
    assert s["next_bottom"] == 2032
    assert s["years_to_bottom"] == 6
    # next panic after 2026 is 2035
    assert s["next_panic"] == 2035
    assert s["years_to_panic"] == 9


def test_between_marker_year_classifies():
    s = benner_status(2028)               # not in any list
    assert s["phase"] in ("recovery", "distribution")
    assert s["is_top"] is False and s["is_bottom"] is False


def test_sequences_are_sorted_unique():
    for seq in (TOP_YEARS, BOTTOM_YEARS, PANIC_YEARS):
        assert seq == sorted(seq)
        assert len(seq) == len(set(seq))
