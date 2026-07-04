import pytest

from gold.entry import (
    break_of_structure, ote_zone, order_block, fair_value_gap, refined_entry,
)
from gold.signal import assemble_structured


# Bullish BMS series: swing low ~8 (idx2), swing high 12 (idx5), close 12.9 breaks it.
_BULL = [
    (11, 11.5, 10.5, 11),
    (11, 11.2, 10.5, 10.8),
    (10.8, 11, 8, 8.5),       # swing low 8
    (8.5, 9, 8.2, 8.8),
    (8.8, 9.5, 8.5, 9.2),
    (9.2, 12, 9, 11.8),       # swing high 12
    (11.8, 11.9, 10, 10.3),   # down candle → order block
    (10.3, 10.8, 9.8, 10.5),
    (10.5, 13, 10.2, 12.9),   # close 12.9 > 12 → bullish BMS
]


def test_break_of_structure_bullish():
    b = break_of_structure(_BULL)
    assert b["bms"] == "bullish" and b["leg_low"] == 8.0 and b["leg_high"] == 13.0


def test_ote_zone_long_and_short():
    z = ote_zone(8, 13, "long")
    assert z["entry"] == 9.475                      # 13 − 0.705×5
    assert z["zone"] == [9.05, 9.9]                 # 0.79 … 0.62
    s = ote_zone(8, 13, "short")
    assert s["entry"] == 11.525                     # 8 + 0.705×5


def test_order_block_long_is_a_down_candle():
    ob = order_block(_BULL, "long")
    assert ob and ob["ob_low"] < ob["ob_high"]


def test_fair_value_gap_runs():
    # just exercises the detector without asserting a specific gap
    assert fair_value_gap(_BULL, "long") in (None,) or "fvg_low" in fair_value_gap(_BULL, "long")


def test_refined_entry_long_ok():
    r = refined_entry(_BULL, "long", buffer=0.0)
    assert r["ok"] and r["bms"] == "bullish" and r["entry"] == 9.475
    assert r["stop"] <= 8.0                          # below the leg low


def test_refined_entry_wrong_side_falls_back():
    r = refined_entry(_BULL, "short")
    assert r["ok"] is False                          # no bearish BMS present


def test_assemble_structured_sizes_from_entry_stop():
    prof = {"bias": "long", "profile": "Classic Tuesday Low", "reason": "x"}
    sig = assemble_structured(prof, entry=9.475, stop=8.0, balance=5000, risk_usd=20)
    assert sig["signal"] == "LONG" and sig["entry_mode"] == "structure"
    assert sig["stop"] == 8.0 and sig["lot"] >= 0.01
    assert [t["price"] for t in sig["targets"]][0] > 9.475   # TP above entry for a long


def test_assemble_structured_rejects_bad_stop():
    prof = {"bias": "long", "profile": "x", "reason": "y"}
    assert assemble_structured(prof, 100.0, 100.0, 5000)["signal"] == "NO TRADE"
