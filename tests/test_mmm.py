from mmm.engine import (
    weekly_levels, previous_frame, level_context, adr, adr_used,
    taylor_day, weekly_cycle_bias, swing_pivots, peak_formation, weekly_setup,
)


def test_weekly_levels_mid_and_range():
    lv = weekly_levels((10, 14, 8, 12))
    assert lv.mid == 11 and lv.range == 6 and lv.high == 14


def test_previous_frame_high_low():
    bars = [(1, 5, 0.5, 4), (4, 6, 3, 5), (5, 5.5, 4.5, 5)]
    pfh, pfl = previous_frame(bars)
    assert pfh == 6 and pfl == 0.5


def test_level_context_zones():
    lv = weekly_levels((10, 14, 8, 12))
    assert "above prior-week high" in level_context(15, lv)
    assert "below prior-week low" in level_context(7, lv)
    assert "upper half" in level_context(12, lv)
    assert "lower half" in level_context(9, lv)


def test_adr_and_used():
    bars = [(0, 2, 0, 1)] * 5          # range 2 each
    assert adr(bars, 5) == 2
    assert adr_used((0, 3, 0, 2), 2) == 1.5


def test_taylor_buy_day():
    prev = (10, 11, 9, 10)
    today = (10, 11, 8.5, 10.8)        # lower low, closes upper half
    assert taylor_day(prev, today) == "BUY DAY"


def test_taylor_short_sell_day():
    prev = (10, 11, 9, 10)
    today = (10.5, 12, 10, 10.2)       # higher high, closes lower half
    assert taylor_day(prev, today) == "SHORT SELL DAY"


def test_weekly_cycle_bias_maps_weekday():
    assert "Wed" in weekly_cycle_bias(2)
    assert "Sun" in weekly_cycle_bias(6)


def test_swing_pivots_finds_peak_and_trough():
    # a clear peak at idx 2, trough at idx 5
    bars = [(0, 1, 0, 1), (0, 2, 1, 2), (0, 5, 2, 3),
            (0, 3, 1, 2), (0, 2, 0.5, 1), (0, 1, 0.2, 0.5), (0, 2, 1, 1.5)]
    kinds = {(p.index, p.kind) for p in swing_pivots(bars, 2, 2)}
    assert (2, "peak") in kinds


def test_peak_formation_bearish_reversal():
    # rally into a peak, then a lower-high down-close bar
    bars = [(0, 2, 0, 1), (0, 3, 1, 2.5), (0, 5, 2, 4.5),
            (0, 4.8, 3, 4), (0, 4.2, 3, 3.2)]
    pf = peak_formation(bars, 1, 1)
    assert pf["reversal"] and "bearish" in pf["reversal"]


def test_weekly_setup_shape():
    daily = [(10, 11, 9, 10), (10, 11, 8.5, 10.8), (10.8, 11.5, 10, 11)]
    out = weekly_setup("EUR/USD", 2, (9, 12, 8, 11), daily, adr_n=3)
    assert out["symbol"] == "EUR/USD"
    assert "weekly_levels" in out and "taylor_day" in out and "peak_formation" in out
