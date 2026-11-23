"""Candle-range → actionable CRT plans (fixtures from the live 02:05 UTC card)."""

import datetime as dt

from services import candlerange_service as crs


def _a(lo, hi, state="continuation", direction="up", cdir="bullish"):
    return {"candle_dir": cdir, "body_low": lo, "body_high": hi, "state": state, "break_dir": direction}


def test_stale_and_doji_anchors_are_skipped():
    now = dt.datetime(2026, 11, 23, 2, 5, tzinfo=dt.timezone.utc)
    assert crs.anchor_fresh("2026-11-23 01:00:00", now)
    assert not crs.anchor_fresh("2026-11-22 01:00:00", now)          # >24h
    assert not crs.anchor_fresh("2026-11-21 13:00:00", now)          # Friday's 13:00 on Monday
    assert crs.min_body("EUR/USD") == 0.0003 and 1.09679 - 1.09677 < crs.min_body("EUR/USD")
    assert 3537.31728 - 3537.28618 < crs.min_body("XAU/USD")         # the 3-cent gold "candle"


def test_plan_up_and_down():
    up = crs.plan_from_anchor("XAU/USD", _a(3571.69, 3601.38))       # the live 01:00 gold candle
    assert up["side"] == "BUY" and up["entry"] == 3601.38 and up["stop"] == 3571.69
    assert round(up["tp2"], 2) == round(3601.38 + 2 * (3601.38 - 3571.69), 2) and up["rr_tp2"] == 2.0
    dn = crs.plan_from_anchor("EUR/USD", _a(1.0950, 1.0980, "reversal", "down", "bullish"))
    assert dn["side"] == "SELL" and dn["entry"] == 1.0950 and dn["stop"] == 1.0980
    small = crs.plan_from_anchor("EUR/USD", _a(1.0950, 1.0952))      # 2-pip body → 10-pip stop
    assert round(small["stop"] - small["entry"], 5) == -0.001 and small["rr_tp2"] < 1.3
    assert crs.plan_from_anchor("EUR/USD", _a(1.0, 1.1, "inside", "none")) is None


def test_card_shows_plan_or_reason():
    r = {"symbol": "XAU/USD", "price": 3606.34706,
         "anchors": {"0100": _a(3571.69366, 3601.37886), "1300": None}}
    p = crs.plan_from_anchor("XAU/USD", r["anchors"]["0100"])
    p["gate"] = {"allow": True, "reasons": []}
    r["anchors"]["0100"]["plan"] = p
    txt = crs.format_alert(r)
    assert "@ 3606.35" in txt and "body 3571.69–3601.38" in txt
    assert "🟢 BUY limit `3601.38`" in txt and "CRT confirmation at 05:00 UTC" in txt and "*Lots*" in txt
    p["gate"] = {"allow": False, "reasons": ["CBDR box forming (14:00–20:00 NY) — wait for the box"]}
    assert "⏸ no trade: CBDR box forming" in crs.format_alert(r)
