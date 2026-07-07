"""SD ladder + 8-hour session range + protraction detection."""

from cbdr.engine import sd_ladder
from gold.session_levels import eight_hour_range, detect_protraction, protraction_gate


# --- SD ladder (validated against the on-chart numbers) ---------------------

def test_sd_ladder_matches_chart():
    # Chart box: high 4156.122, low 4145.096 → unit ~11.026, mean ~4150.6.
    lad = sd_ladder(4156.122, 4145.096)
    lv = lad["levels"]
    assert lv["0"] == 4156.122 and lv["-1"] == 4145.096          # 0 = high, -1 = low
    assert abs(lad["mean"] - 4150.609) < 0.01                    # mean = box mid (k=-0.5)
    assert abs(lv["1"] - 4167.148) < 0.01                        # +1 unit above high
    assert abs(lv["4"] - 4200.226) < 0.01 and abs(lv["-4"] - 4112.018) < 0.01
    assert "-0.5" in lv and "2.5" in lv                          # full 0.5-step ladder


def test_sd_ladder_flat_box():
    assert sd_ladder(100.0, 100.0)["levels"] == {}


# --- 8-hour range -----------------------------------------------------------

def _bars(seq):
    # seq of (o,h,l,c) → (date,o,h,l,c) bars
    return [(i, o, h, l, c) for i, (o, h, l, c) in enumerate(seq)]


def test_eight_hour_range():
    bars = _bars([(10, 12, 9, 11), (11, 15, 10, 14), (14, 14, 8, 9)])
    r = eight_hour_range(bars, hours=8)
    assert r["high"] == 15 and r["low"] == 8 and r["mid"] == 11.5


# --- protraction ------------------------------------------------------------

def test_protraction_sweep_low_reversal_is_long():
    # last bars dip below the low (95) then close back above it
    bars = _bars([(100, 101, 99, 100), (99, 100, 94, 98), (98, 102, 97, 101)])
    p = detect_protraction(bars, high=105, low=95, lookback=3)
    assert p["swept"] == "low" and p["direction"] == "long" and p["target"] == 105


def test_protraction_sweep_high_reversal_is_short():
    bars = _bars([(100, 104, 99, 101), (101, 107, 100, 102), (102, 103, 98, 99)])
    p = detect_protraction(bars, high=105, low=95, lookback=3)
    assert p["swept"] == "high" and p["direction"] == "short" and p["target"] == 95


def test_protraction_both_sides_is_seek_destroy():
    bars = _bars([(100, 107, 99, 101), (101, 102, 93, 100), (100, 101, 98, 100)])
    p = detect_protraction(bars, high=105, low=95, lookback=3)
    assert p["swept"] == "both" and p["direction"] is None


def test_protraction_gate_confirms_and_blocks():
    p = {"direction": "long", "swept": "low", "target": 105}
    assert protraction_gate("long", p)["ok"] is True
    assert protraction_gate("long", p)["target"] == 105
    assert protraction_gate("short", p)["ok"] is False
    assert protraction_gate("long", {"direction": None})["ok"] is False
