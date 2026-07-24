"""4H b2b bomber — 1-5-9 sweep + back-to-back continuation, and its recon wiring."""

from gold.b2b import b2b_bomber, format_b2b
from gold import recon as gr


def _bar(o, h, l, c, t=None):
    return {"time": t, "open": o, "high": h, "low": l, "close": c}


# --- bullish: sweep the low then two higher closes ---------------------------

def test_bullish_b2b_sweep_low_then_continuation():
    bars = [
        _bar(3980, 3990, 3975, 3985),   # reference
        _bar(3985, 3988, 3960, 3978),   # candle 1 — sweeps below 3975 (grabs sells)
        _bar(3978, 4005, 3977, 4000),   # candle 5 — higher close
        _bar(4000, 4030, 3998, 4025),   # candle 9 — higher close (b2b)
    ]
    r = b2b_bomber(bars)
    assert r["signal"] == "LONG" and r["swept"] == "low"
    assert r["invalidation"] == 3960.0
    assert r["continuation_closes"] == [4000.0, 4025.0]


def test_bearish_b2b_sweep_high_then_continuation():
    bars = [
        _bar(4020, 4025, 4010, 4015),
        _bar(4015, 4040, 4013, 4020),   # sweeps above 4025
        _bar(4020, 4022, 3995, 4000),   # lower close
        _bar(4000, 4004, 3970, 3980),   # lower close (b2b)
    ]
    r = b2b_bomber(bars)
    assert r["signal"] == "SHORT" and r["swept"] == "high"
    assert r["invalidation"] == 4040.0


# --- non-patterns ------------------------------------------------------------

def test_sweep_without_continuation_is_none():
    bars = [
        _bar(3980, 3990, 3975, 3985),
        _bar(3985, 3988, 3960, 3978),   # sweeps low
        _bar(3978, 3980, 3970, 3972),   # but LOWER close — no bullish continuation
        _bar(3972, 3975, 3965, 3968),
    ]
    r = b2b_bomber(bars)
    assert r["signal"] == "NONE"


def test_no_sweep_is_none():
    bars = [
        _bar(3980, 3990, 3975, 3985),
        _bar(3985, 3989, 3980, 3987),   # inside the reference — no sweep
        _bar(3987, 3995, 3985, 3992),
        _bar(3992, 4000, 3990, 3998),
    ]
    assert b2b_bomber(bars)["signal"] == "NONE"


def test_too_few_bars():
    assert b2b_bomber([_bar(1, 2, 0, 1)] * 3)["signal"] == "NONE"


# --- anchor detection (UTC-4 session opens) ----------------------------------

def test_anchor_flag_on_ny_1400():
    # sweep candle at 18:00 UTC → 14:00 UTC-4 = the NY new-CBDR anchor
    bars = [
        _bar(3980, 3990, 3975, 3985, "2026-07-20 14:00:00"),
        _bar(3985, 3988, 3960, 3978, "2026-07-20 18:00:00"),
        _bar(3978, 4005, 3977, 4000, "2026-07-20 22:00:00"),
        _bar(4000, 4030, 3998, 4025, "2026-07-21 02:00:00"),
    ]
    r = b2b_bomber(bars)
    assert r["signal"] == "LONG"
    assert r["anchored"] is True and r["anchor_session"] == "ny_14"


# --- recon confluence wiring -------------------------------------------------

def test_recon_folds_in_b2b_confluence():
    from cbdr.engine import build_cbdr
    box = build_cbdr(3320.0, 3300.0)
    b2b = {"signal": "LONG", "swept": "low", "anchored": True,
           "anchor_session": "ny_14", "invalidation": 3260.0}
    sweep = gr.recon_sweep(3268.0, dxy_price=111.0, box=box,
                           rbusbis_dir="falling", b2b=b2b)
    buy = next(s for s in sweep["setups"] if s["side"] == "LONG")
    assert buy["b2b_confluence"] is True
    assert "b2b" in buy["gate"]
    assert "💣 4H b2b" in gr.format_recon(sweep)


def test_format_b2b_line():
    r = {"signal": "LONG", "swept": "low", "anchored": True, "anchor_session": "asia_00",
         "invalidation": 3960.0, "target_ref": 4030.0}
    txt = format_b2b(r)
    assert "B2B BOMBER" in txt and "LONG" in txt


def test_b2b_returns_swing_card():
    """The 4H b2b fires an actionable ~8h swing card (entry/stop/target/RR)."""
    from gold.b2b import b2b_bomber
    bars = [
        ("2026-07-23T20:00:00Z", 4080, 4090, 4070, 4085),
        ("2026-07-24T04:00:00Z", 4085, 4088, 4050, 4082),   # sweep low (Asian 00:00 UTC-4)
        ("2026-07-24T08:00:00Z", 4082, 4110, 4080, 4105),
        ("2026-07-24T12:00:00Z", 4105, 4130, 4100, 4125),
    ]
    r = b2b_bomber(bars)
    assert r["signal"] == "LONG"
    assert r["entry"] and r["stop"] < r["entry"] < r["target"]
    assert r["rr"] > 0 and r["horizon_hours"] == 8 and r["trade_type"] == "swing"
    assert r["anchor_session"] == "asia_00"      # 04:00 UTC = 00:00 UTC-4
