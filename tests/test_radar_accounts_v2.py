"""OB radar detector + acc4 stepped compounding + acc5 layering cap."""

import pytest

from gold import radar as rd
from gold import accounts as ga


# --- OB radar ----------------------------------------------------------------

def _bar(o, h, l, c, t="d"):
    return (t, o, h, l, c)


def test_bearish_ob_from_upclose_before_drop():
    bars = [
        _bar(4100, 4110, 4095, 4105, "d1"),
        _bar(4105, 4140, 4104, 4135, "d2"),   # up-close (bearish OB candidate), high 4140
        _bar(4135, 4136, 4060, 4065, "d3"),   # closes 4065 < 4104 low → down displacement
        _bar(4065, 4070, 4030, 4040, "d4"),
    ]
    obs = rd.order_blocks(bars)
    bear = [o for o in obs if o["type"] == "bearish"]
    assert any(o["zone"][1] == 4140.0 for o in bear)     # OB zone top = wick high
    assert bear[0]["kind"] == "supply"


def test_bullish_ob_from_downclose_before_rally():
    bars = [
        _bar(4000, 4005, 3990, 3995, "d1"),
        _bar(3995, 3998, 3960, 3965, "d2"),   # down-close (bullish OB), low 3960
        _bar(3965, 4010, 3964, 4005, "d3"),   # closes 4005 > 3998 high → up displacement
        _bar(4005, 4030, 4000, 4025, "d4"),
    ]
    obs = rd.order_blocks(bars)
    bull = [o for o in obs if o["type"] == "bullish"]
    assert any(o["zone"][0] == 3960.0 for o in bull)     # OB zone bottom = wick low


def test_active_retest_and_bias():
    bars = [
        _bar(4100, 4110, 4095, 4105, "d1"),
        _bar(4105, 4140, 4104, 4135, "d2"),
        _bar(4135, 4136, 4060, 4065, "d3"),
    ]
    scan = rd.radar_scan(bars, price=4135.0)             # back at the bearish OB
    assert scan["active_retest"] is not None
    assert scan["bias"] == "short"
    assert "OB" in scan["note"]


def test_continuity_ladder_splits_around_price():
    scan = rd.radar_scan([_bar(4100, 4110, 4090, 4100)] * 6, price=4135.0)
    # default risk book: sells below 4135, buys above
    assert scan["continuity"]["sell_targets"] == [4075.0, 4000.0, 3885.0]
    assert scan["continuity"]["buy_targets"] == [4195.0, 4275.0, 4380.0]


def test_set_continuity():
    rd.set_continuity(sell=[4130, 4050], buy=[4200, 4300])
    assert rd.CONTINUITY["sell"] == [4130.0, 4050.0]
    rd.set_continuity(sell=[4135, 4075, 4000, 3885], buy=[4195, 4275, 4380])   # restore


# --- acc4 stepped compounding (0.05 start, 2× then 10×) ----------------------

def test_acc4_stepped_first_two_double():
    out = ga.compound_stepped(750)
    rows = out["ladder"]
    assert rows[0]["lot"] == 0.05                       # 0.05 start
    # run1 gain at 0.05 lot / 1500 pips = 750 → doubles 750 to 1500
    assert rows[0]["balance"] == pytest.approx(1500.0)
    assert rows[1]["balance"] == pytest.approx(3000.0)  # 2nd run doubles again
    # last two runs 10x the account
    assert rows[2]["target_pct"] == 1000 and rows[3]["target_pct"] == 1000
    assert out["final_balance"] > 300000


def test_acc4_registered():
    assert ga.FLEET["acc4"]["strategy"] == "compound_stepped"
    assert ga.account_plan("acc4")["start_lot"] == 0.05


# --- acc5 layering cap -------------------------------------------------------

def test_acc5_layering_caps_then_rides():
    p = ga.trend_layer_plan(4000.0, "long", range_pips=2500, cap_pips=1450)
    assert p["target"] == pytest.approx(4250.0)         # full 2500-pip target
    # layers fill only within the cap, not the whole span
    assert 3800.0 < p["cap_price"] < 4000.0             # layers cap below the long anchor
    assert p["leverage"] == "1:2000/3000"
    assert p["orders"][0]["lot"] == 0.02                # 0.02 start
