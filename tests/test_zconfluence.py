"""Z-confluence + the owner's trade gate (fixtures from the live Telegram screenshots)."""

import datetime as dt

from services import trade_gate as tg
from services import zconfluence as zc

U = dt.timezone.utc
LONDON_TUE = dt.datetime(2026, 11, 24, 8, 0, tzinfo=U)       # 03:00 NY → allowed
CBDR_SUN = dt.datetime(2026, 11, 22, 22, 54, tzinfo=U)       # 17:54 NY Sunday → CBDR forming


def _breach(sym, z, last, mean, sd):
    return {"ok": True, "fired": True, "fresh": True, "symbol": sym, "z": z,
            "lean": "SELL" if z > 0 else "BUY", "last_close": last, "mean": mean, "sd": sd}


def _row(pair, verdict, conf, agrees, missing):
    return {"pair": pair, "verdict": verdict, "confidence": conf, "cot_agrees": agrees,
            "cot_missing": missing, "cot_net": None if missing else -5000, "retail_long": 80}


W_SHORT = {"bias": "short", "profile": "continuation_down", "reason": "x"}
W_LONG = {"bias": "long", "profile": "continuation_up", "reason": "x"}


# --- the gate -------------------------------------------------------------------------
def test_session_block_cbdr_forming_and_sunday_open():
    assert "CBDR" in tg.session_block(CBDR_SUN)
    assert tg.session_block(dt.datetime(2026, 11, 22, 22, 30, tzinfo=U))          # 17:30 NY Sun
    assert tg.session_block(LONDON_TUE) is None


def test_min_stop_widens_noise_stops():
    # GBP/USD screenshot: 1.7-pip stop → widened to the owner's 10 pips
    assert tg.widen_stop("GBP/USD", "SELL", 1.30883, 1.30900) == 1.30983
    assert tg.widen_stop("XAU/USD", "BUY", 3532.88, 3531.75) == 3529.88          # $1.13 → $3
    assert tg.widen_stop("EUR/USD", "SELL", 1.1, 1.102) == 1.102                  # already wide


def test_rr_floor_blocks_the_confirm_card():
    # CONFIRMED SHORT XAU screenshot: TPs 0.48R / 0.3R → held
    g = tg.evaluate("XAU/USD", "SHORT", 3532.85, 3541.7, [3537.06 - 9, 3535.55 - 6], now=LONDON_TUE)
    assert not g["allow"] and "below 1.3R" in g["reasons"][0]
    assert tg.evaluate("XAU/USD", "SHORT", 3532.85, 3541.7, [3510.0], now=LONDON_TUE)["allow"]


def test_weekly_profile_divergence_blocks():
    g = tg.evaluate("XAU/USD", "BUY", 3532.0, 3529.0, [3540.0], weekly=W_SHORT, now=LONDON_TUE)
    assert not g["allow"] and "weekly profile is short" in g["reasons"][0]


# --- grading --------------------------------------------------------------------------
def test_grade_requires_everything_for_A():
    full = _row("EUR/USD", "SHORT", 85, True, False)
    assert zc.grade("SELL", full, "confirms") == "A"
    assert zc.grade("SELL", full, "neutral") == "B"                       # weekly not confirming
    assert zc.grade("SELL", _row("EUR/USD", "SHORT", 80, False, True), "confirms") == "B"  # COT missing
    assert zc.grade("SELL", _row("EUR/USD", "SHORT", 85, False, False), "confirms") == "X"  # COT opposes
    assert zc.grade("BUY", full, "neutral") == "X"                        # confluence opposite
    assert zc.grade("SELL", full, "diverges") == "X"                      # weekly opposite


def test_screenshot_cases_are_no_longer_sent():
    breaches = [_breach("GBP/USD", 2.20, 1.30883, 1.30846, 0.00017),
                _breach("XAU/USD", -3.93, 3532.87867, 3537.31893, 1.13)]
    signals = [_row("GBP/USD", "SHORT", 80, False, True), _row("XAU/USD", "SHORT", 69, False, True)]
    out = {r["symbol"]: r for r in zc.build(breaches, signals, None,
                                           {"XAU/USD": W_SHORT}, now=CBDR_SUN)}
    gbp, xau = out["GBP/USD"], out["XAU/USD"]
    assert gbp["grade"] == "B" and not zc.sendable(gbp)                  # COT missing → not A
    assert gbp["levels"]["stop"] == 1.30983                              # 10-pip floor
    assert xau["grade"] == "X" and not zc.sendable(xau)                  # BUY vs weekly short
    assert any("CBDR" in w for w in gbp["gate"]["reasons"])


def test_a_grade_outside_cbdr_is_sent():
    b = _breach("EUR/USD", 3.2, 1.1040, 1.0960, 0.0025)
    r = zc.build([b], [_row("EUR/USD", "SHORT", 88, True, False)], "2026-11-17",
                 {"EUR/USD": W_SHORT}, now=LONDON_TUE)[0]
    assert r["grade"] == "A" and r["gate"]["allow"] and zc.sendable(r)
    txt = zc.card(r)
    assert "ALIGNED" in txt and "Weekly profile: short" in txt and "COT agrees (as of 2026-11-17)" in txt
    assert "*Lots*" in txt


def test_levels_math_and_route():
    lv = zc.levels("SELL", last=158.608, mean=158.238, sd=0.1, z=3.7)
    assert lv["r_tp1"] == 2.7 and lv["r_tp2"] == 3.7
    from main import app
    assert "/zconfluence/scan" in {r.path for r in app.routes}


def test_cot_staleness_and_no_cot_cap():
    from services.wildchance_service import cot_stale, cot_latest
    feed = {"cot": {"EUR": [{"d": "2026-11-17"}]}}
    assert cot_latest(feed) == "2026-11-17" and not cot_stale(feed, dt.date(2026, 11, 22))
    from wildchance.wildchance_scraper import evaluate
    sig = evaluate("EUR/USD", 1.1, {"long_pct": 85, "short_pct": 15}, {})
    assert sig["confidence"] == 80 and sig["cot_missing"]


def test_signal_card_has_no_old_lot_line():
    from services.signal_engine import build_manual, format_card
    card = format_card(build_manual("EUR/USD", "short", 1.09701, 1.09801, [1.09246], balance=2500))
    assert "Lot ≤" not in card and "*Lots*" in card


def test_weekend_blocks_all_new_trades():
    sat = dt.datetime(2026, 11, 28, 12, 0, tzinfo=U)
    sun_before_open = dt.datetime(2026, 11, 22, 19, 54, tzinfo=U)   # the screenshot time
    for t in (sat, sun_before_open):
        assert "market closed" in tg.session_block(t)
    g = tg.evaluate("EUR/USD", "SELL", 1.104, 1.105, [1.096], now=sat)
    assert not g["allow"] and "market closed" in g["reasons"][0]
    assert "CBDR" in tg.session_block(dt.datetime(2026, 11, 23, 20, 0, tzinfo=U))  # Mon 15:00 NY


def test_intraday_alert_held_when_closed(monkeypatch):
    from fastapi.testclient import TestClient
    import services.freshness as fr
    import services.intraday_service as isvc
    from main import app

    async def fake_scan(*a, **k):
        return [{"ok": True, "fired": True, "fresh": True, "symbol": "USD/JPY", "z": 3.7,
                 "lean": "SELL", "direction": "overbought", "last_close": 158.6}]
    monkeypatch.setattr(isvc, "scan", fake_scan)
    monkeypatch.setattr(fr, "market_open", lambda now=None: False)
    r = TestClient(app).post("/intraday/scan").json()
    assert r["sent"] is False and "market closed" in r["held"]


def test_held_reasons_explain_grade_b():
    # live 2026-11-23 EUR/USD: COT agrees, conf 94, but weekly inside Monday range
    r = {"lean": "SELL", "confluence": {"verdict": "SHORT", "confidence": 94, "cot_agrees": True,
                                        "cot_missing": False},
         "weekly": {"verdict": "neutral", "reason": "still inside Monday range — no sweep yet"}}
    assert zc.grade_reasons(r) == ["weekly profile neutral (still inside Monday range — no sweep yet)"]
    r2 = {"lean": "SELL", "confluence": None, "weekly": {"verdict": "neutral"}}
    assert zc.grade_reasons(r2)[0] == "no COT/retail read for this pair"
    r3 = {"lean": "SELL", "confluence": {"verdict": "WATCH", "confidence": 61, "cot_agrees": False,
                                         "cot_missing": False}, "weekly": {"verdict": "confirms"}}
    assert zc.grade_reasons(r3) == ["confluence WATCH conf 61 (needs LONG/SHORT ≥ 70)", "COT disagrees"]
