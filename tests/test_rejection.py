"""Sweep-and-reject confirmation — take the trade on the close-back-inside."""

from gold.rejection import sweep_reject, build_reject_card


def _bar(o, h, l, c):
    return {"open": o, "high": h, "low": l, "close": c}


# --- SHORT: sweep above a sell level, close back below ------------------------

def test_short_reject_confirms():
    level = 4059.0                       # +1.5SD sell limit
    bars = [
        _bar(4050, 4055, 4048, 4053),
        _bar(4053, 4058, 4051, 4056),
        _bar(4056, 4065, 4054, 4052),    # swept 4059 to 4065, closed back at 4052 (bearish)
    ]
    r = sweep_reject(bars, level, "short")
    assert r is not None and r["signal"] == "SHORT"
    assert r["sweep_high"] == 4065.0 and r["close"] == 4052.0
    assert r["stop"] > 4065.0            # stop above the swept wick


def test_short_no_reject_when_closes_above():
    level = 4059.0
    bars = [_bar(4056, 4066, 4054, 4063)]   # swept but CLOSED above → accepted, not rejected
    assert sweep_reject(bars, level, "short") is None


def test_short_needs_a_sweep():
    level = 4059.0
    bars = [_bar(4050, 4057, 4048, 4052)]   # never reached 4059 → no sweep
    assert sweep_reject(bars, level, "short") is None


# --- LONG: sweep below a buy level, close back above -------------------------

def test_long_reject_confirms():
    level = 3941.0
    bars = [_bar(3940, 3948, 3935, 3944)]   # swept 3941 to 3935, closed back up at 3944 (bullish)
    r = sweep_reject(bars, level, "long")
    assert r is not None and r["signal"] == "LONG"
    assert r["sweep_low"] == 3935.0 and r["stop"] < 3935.0


def test_body_filter_blocks_wrong_close():
    level = 4059.0
    # swept above and closed just below the level but bullish body → require_body blocks
    bars = [_bar(4058, 4065, 4057, 4058.5)]
    assert sweep_reject(bars, level, "short", require_body=True) is None


# --- card build --------------------------------------------------------------

def test_build_reject_card_has_rr_targets():
    r = {"signal": "SHORT", "entry": 4052.0, "stop": 4067.0, "level": 4059.0,
         "note": "sweep+reject"}
    card = build_reject_card(r, targets=[4030.0, 4010.0])
    assert card["signal"] == "SHORT" and card["side"] == "short"
    assert card["targets"][0]["rr"] > 0 and card["stop"] == 4067.0


def test_lookback_finds_recent_reject():
    level = 4059.0
    bars = [
        _bar(4056, 4065, 4054, 4052),    # a reject 3 bars ago
        _bar(4052, 4054, 4048, 4050),
        _bar(4050, 4053, 4047, 4049),    # latest, no sweep
    ]
    # with lookback 1 only the latest bar is checked → no reject
    assert sweep_reject(bars, level, "short", lookback=1) is None
    # with lookback 3 the earlier reject is found
    assert sweep_reject(bars, level, "short", lookback=3) is not None
