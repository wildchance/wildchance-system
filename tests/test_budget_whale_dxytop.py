"""Weekly trade budget, the whale tier, and the DXY 110/117 manipulation top."""

import datetime as _dt
import pytest

from gold import trade_budget as tb
from gold import big5
from gold import dxy as gdxy


# --- whale tier --------------------------------------------------------------

def test_whale_above_elephant():
    assert big5.is_whale(1501) is True
    assert big5.is_whale(1500) is False        # exactly 1500 = elephant
    assert big5.tier_for_pips(2000)["name"] == "whale"
    assert big5.tier_for_pips(1500)["name"] == "elephant"


def test_classify_capture_flags_whale():
    c = big5.classify_capture(4000.0, 4200.0, "long")   # 2000 pips
    assert c["pips"] == 2000.0 and c["whale"] is True
    assert c["tier"]["name"] == "whale"


# --- weekly trade budget -----------------------------------------------------

def test_budget_caps_match_cadence():
    b = tb.WEEKLY_BUDGET
    assert b["swing"] == 1 and b["intraday"] == 5 and b["intrasession"] == 5
    assert b["crt"] == 10 and b["sniper"] == 5 and b["prelondon"] == 5


def test_within_budget_and_gate():
    assert tb.within_budget("intraday", 4) is True
    assert tb.within_budget("intraday", 5) is False        # 6th blocked
    g = tb.budget_gate("crt", 10)
    assert g["ok"] is False and "cap reached" in g["reason"]
    assert tb.within_budget("unknown_tier", 99) is True     # uncapped


def test_count_by_tier_this_week():
    now = _dt.datetime.now(_dt.timezone.utc)
    since = tb.week_start(now)
    positions = [
        {"trade_type": "intraday", "opened_at": now},
        {"trade_type": "intraday", "opened_at": now},
        {"trade_type": "crt", "opened_at": now},
        {"trade_type": "swing", "opened_at": since - _dt.timedelta(days=2)},  # last week
    ]
    counts = tb.count_by_tier(positions, since)
    assert counts["intraday"] == 2 and counts["crt"] == 1
    assert "swing" not in counts                            # older than this week


def test_budget_status_board():
    board = tb.budget_status({"intraday": 5, "crt": 3})
    assert board["by_tier"]["intraday"]["over"] is True
    assert board["by_tier"]["crt"]["room"] == 7
    assert board["total_taken"] == 8


# --- DXY manipulation top 110/117 -------------------------------------------

def test_dxy_extreme_is_110():
    assert gdxy.EXTREME_MIN == 110.0
    assert gdxy.MANIPULATION_TOP == (110.0, 117.0)


def test_flip_locked_below_110_even_at_old_ceiling():
    # 106 used to be "at extreme"; now the top is 110 → still pre-extreme → LOCKED
    s = gdxy.dxy_flip_status(106.0, rbusbis_dir="falling")
    assert s["at_extreme"] is False and s["unlocked"] is False


def test_flip_unlocks_at_110_plus_rollover():
    s = gdxy.dxy_flip_status(111.0, rbusbis_dir="falling")
    assert s["at_extreme"] is True and s["unlocked"] is True
