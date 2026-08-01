"""Volume-profile anatomy — HVN/LVN nodes, bullish/bearish scenario, Asian breakout."""

from gold import volume_profile as vp


def _b(t, o, h, l, c):
    return (t, o, h, l, c)


def _two_peak_series():
    """A distribution with two acceptance shelves (HVN) around 4000 and 4100 and a
    thin void (LVN) between — so nodes() has clear peaks and a valley."""
    bars = []
    for _ in range(12):                       # shelf 1 ~4000 (heavy time-at-price)
        bars.append(_b("d", 3998, 4002, 3997, 4000))
    bars.append(_b("d", 4001, 4052, 4000, 4050))   # fast leg THROUGH the void
    for _ in range(12):                       # shelf 2 ~4100
        bars.append(_b("d", 4098, 4102, 4097, 4100))
    return bars


def test_nodes_surface_hvn_and_lvn():
    n = vp.nodes(_two_peak_series(), bins=30)
    assert n["hvn"], "expected at least one high-volume node"
    # the two acceptance shelves show up as HVN magnets
    assert any(3990 <= h <= 4010 for h in n["hvn"])
    assert any(4090 <= h <= 4110 for h in n["hvn"])
    # the thin span between the two shelves is a low-volume void
    assert any(4004 <= l <= 4096 for l in n["lvn"])


def test_scenario_bullish_above_value_poc_below():
    bars = _two_peak_series()
    s = vp.scenario(bars, price=4140.0)       # price accepted above the whole value
    assert s["scenario"] == "bullish" and s["vs_poc"] == "above"
    assert "nodes" in s and s["poc"] < 4140.0


def test_scenario_bearish_below_value_poc_above():
    bars = _two_peak_series()
    s = vp.scenario(bars, price=3960.0)
    assert s["scenario"] == "bearish" and s["vs_poc"] == "below"


def test_scenario_neutral_inside_value():
    bars = _two_peak_series()
    s = vp.scenario(bars, price=4050.0)       # mid-range, inside value
    assert s["scenario"] == "neutral"


def _asian_series():
    # bars carry an explicit session 'hour'; Asian window 14..19 builds value ~4000,
    # then London (hour 22) prints price above it → a BUY breakout of Asian value.
    bars = []
    for h in range(14, 20):                   # Asian accumulation shelf
        bars.append({"hour": h, "open": 3999, "high": 4003, "low": 3997, "close": 4001})
        bars.append({"hour": h, "open": 4001, "high": 4004, "low": 3998, "close": 4000})
    bars.append({"hour": 22, "open": 4010, "high": 4060, "low": 4008, "close": 4055})
    return bars


def test_asian_profile_filters_to_session():
    ap = vp.asian_profile(_asian_series(), tz_offset=0)
    assert ap["ok"] is True and ap["asian_bars"] == 12
    assert ap["val"] <= ap["poc"] <= ap["vah"]


def test_asian_breakout_buy_above_vah():
    bo = vp.asian_breakout(_asian_series(), price=4055.0, tz_offset=0)
    assert bo["armed"] is True and bo["side"] == "BUY"
    assert bo["entry"] == bo["profile"]["vah"]     # retest the Asian VAH
    assert bo["target"] > bo["entry"] and bo["stop"] == bo["profile"]["poc"]


def test_asian_breakout_waits_inside_value():
    bo = vp.asian_breakout(_asian_series(), price=4000.0, tz_offset=0)
    assert bo["armed"] is False and "WAIT" in bo["status"]
