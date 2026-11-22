"""FRED macro dashboard logic, owner's regime presets, and the billing-webhook fix."""

from services import macro_dashboard as md


def test_curve_state_after_inversion():
    s = md.curve_state(0.5, [-0.4, -0.2, 0.1, 0.5])
    assert s["state"] == "positive" and "re-steepened" in s["note"]
    assert md.curve_state(-0.3, [-0.3])["state"] == "inverted"
    assert md.curve_state(0.1, [0.1])["note"] is None and md.curve_state(None, []) is None


def test_regime_tags():
    d = {"VIXCLS": {"latest": 28}, "BAMLH0A0HYM2": {"latest": 5.0, "change": 0.8},
         "CPIAUCSL": {"yoy_pct": 3.1}, "DTWEXBGS": {"direction": "rising"},
         "ICSA": {"direction": "rising"}}
    r = md.regime(d)
    assert r["posture"] == "RISK-OFF" and r["risk_off_score"] == 3
    assert "CPI 3.1% YoY" in r["tags"] and "broad USD rising" in r["tags"]
    assert md.regime({})["posture"] == "NORMAL"


def test_regime_presets_and_precedence(monkeypatch):
    from services import swing_service as sw
    import services.ai_sentiment as ai
    monkeypatch.setattr(ai, "AUTO_BIAS", False)
    env = {"MACRO_REGIMES": "political_uncertainty,usd_bull_trump1_analog,bogus",
           "MACRO_BIAS": '{"US500": "BUY_ONLY"}'}
    monkeypatch.setattr(sw, "config", lambda k, default=None: env.get(k, default))
    assert sw.active_regimes() == ["political_uncertainty", "usd_bull_trump1_analog"]
    locks = sw.macro_bias()
    assert locks["XAUUSD"] == "BUY_ONLY" and locks["EURUSD"] == "SELL_ONLY"
    assert locks["USDJPY"] == "BUY_ONLY" and locks["US500"] == "BUY_ONLY"   # manual wins


def test_zsetup_against_macro_lock_is_conflict():
    from services import zconfluence as zc
    b = {"ok": True, "fired": True, "fresh": True, "symbol": "EUR/USD", "z": -3.0, "lean": "BUY",
         "last_close": 1.09, "mean": 1.0975, "sd": 0.0025}
    row = {"pair": "EUR/USD", "verdict": "LONG", "confidence": 88, "cot_agrees": True,
           "cot_net": 5, "retail_long": 20}
    w = {"EUR/USD": {"bias": "long", "profile": "continuation_up", "reason": "x"}}
    assert zc.build([b], [row], "2026-11-17", w)[0]["grade"] == "A"
    assert zc.build([b], [row], "2026-11-17", w, locks={"EURUSD": "SELL_ONLY"})[0]["grade"] == "X"


def test_billing_webhook_requires_secret(monkeypatch):
    from fastapi.testclient import TestClient
    import decouple
    from main import app
    c = TestClient(app)
    url = "/saas/billing/webhook?email=a@b.c&tier=elite"
    monkeypatch.setattr(decouple, "config", lambda k, default=None: None
                        if k == "BILLING_WEBHOOK_SECRET" else default)
    assert c.post(url).status_code == 503                         # was: open to anyone
    monkeypatch.setattr(decouple, "config", lambda k, default=None: "s3cret"
                        if k == "BILLING_WEBHOOK_SECRET" else default)
    assert c.post(url + "&secret=nope").status_code == 403


def test_macro_routes_mounted():
    from main import app
    paths = {r.path for r in app.routes}
    assert {"/macro/dashboard", "/macro/brief", "/macro/locks"} <= paths
