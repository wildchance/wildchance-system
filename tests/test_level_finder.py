"""Auto level finder — swing / OB detection + the assembled reaction map."""

from gold import level_finder as lf


def _b(o, h, l, c):
    return ("t", o, h, l, c)


def _series():
    # rally, an up-close OB candle, a SHARP bearish displacement bar (the OB trigger),
    # drift down to a down-close OB candle, a SHARP bullish displacement bar, bounce.
    bars = []
    px = 4000.0
    for i in range(20):                 # rally up (calm bars, ATR ~ small)
        bars.append(_b(px, px + 6, px - 2, px + 4)); px += 4
    bars.append(_b(px, px + 3, px - 1, px + 2))     # last up-close → bearish OB origin
    top = px
    bars.append(_b(px, px + 2, px - 40, px - 38)); px -= 38   # DISPLACEMENT down (big red)
    for i in range(10):
        bars.append(_b(px, px + 2, px - 6, px - 4)); px -= 4
    bars.append(_b(px, px + 2, px - 3, px - 1))     # last down-close → bullish OB origin
    low = px
    bars.append(_b(px, px + 40, px - 2, px + 38)); px += 38   # DISPLACEMENT up (big green)
    for i in range(10):
        bars.append(_b(px, px + 6, px - 1, px + 4)); px += 4
    return bars, top, low


def test_swings_and_obs_detected():
    bars, top, low = _series()
    sw = lf.swing_points(bars)
    obs = lf.order_blocks(bars)
    assert sw["highs"] and sw["lows"]
    assert any(z["side"] == "sell" for z in obs)
    assert any(z["side"] == "buy" for z in obs)


def test_build_levels_map():
    bars, top, low = _series()
    price = 4060.0
    m = lf.build_levels(bars, price=price)
    assert m["ok"] is True
    # sell levels are above price, floors below
    assert all(l > price for l in m["sell_retest_levels"])
    assert all(f < price for f in m["floors"])
    # zones carry lo<hi bands for the reject-gate
    for z in m["sell_zones"] + m["buy_zones"]:
        assert z["lo"] < z["hi"] and z["name"] and "note" in z
    assert m["atr"] > 0 and m["pivots"]["recent_high"] >= m["pivots"]["recent_low"]


def test_too_few_bars_safe():
    assert lf.build_levels([_b(1, 2, 0, 1)] * 5).get("ok") is False


def test_dedupe_collapses_near_levels():
    assert lf._dedupe([4000.0, 4001.0, 4050.0], tol=3.0) == [4000.0, 4050.0]
