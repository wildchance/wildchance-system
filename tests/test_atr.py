from indicators.atr import (
    true_range, atr, atr_stop, atr_targets, spike_ratio, is_spike,
    adr, percent_of_adr,
)


def test_true_range_first_bar_is_high_low():
    assert true_range(10, 8, None) == 2


def test_true_range_uses_prev_close_gap():
    # gap up: prev_close 5, bar 10..9 -> max(1, |10-5|, |9-5|) = 5
    assert true_range(10, 9, 5) == 5


def test_atr_needs_period_bars():
    bars = [(10, 9, 9.5)] * 3
    assert atr(bars, period=14) is None


def test_atr_constant_range_equals_range():
    bars = [(11, 10, 10.5)] * 20        # TR ~1 each
    assert round(atr(bars, 14), 6) == 1.0


def test_atr_stop_directions():
    assert atr_stop(100, 2, "BUY", 1.5) == 97.0
    assert atr_stop(100, 2, "SELL", 1.5) == 103.0


def test_atr_targets_scale_out():
    assert atr_targets(100, 2, "BUY", (1, 2, 3)) == [102, 104, 106]
    assert atr_targets(100, 2, "SELL", (1, 2, 3)) == [98, 96, 94]


def test_is_spike_flags_expansion():
    assert is_spike((110, 100, 105), 3.0, k=2.0) is True     # range 10 vs 3
    assert is_spike((103, 100, 102), 3.0, k=2.0) is False    # range 3 vs 3


def test_spike_ratio_none_without_atr():
    assert spike_ratio(5, None) is None


def test_adr_and_percent():
    assert adr([1, 2, 3, 4, 5], n=5) == 3
    assert percent_of_adr(6, 3) == 2.0
    assert percent_of_adr(1, None) is None
