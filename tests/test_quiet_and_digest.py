from services import council_gate as cg


def test_quiet_market_is_blocked():
    assert "too quiet" in cg.quiet_reason("NZD/USD", {"atr_pct": 60, "adr_pips": 41.0})
    assert "bottom" in cg.quiet_reason("EUR/USD", {"atr_pct": 8, "adr_pips": 80.0})
    assert cg.quiet_reason("EUR/USD", {"atr_pct": 55, "adr_pips": 72.0}) is None
    assert cg.quiet_reason("XAU/USD", {"atr_pct": 40, "adr_pips": 30.0}) is None   # ADR floor is FX-only
    assert cg.quiet_reason("NZD/USD", None) is None


def test_digest_labels_positioning_leans_with_the_council():
    src = open("routes/alerts.py").read()
    assert "Positioning leans" in src and "watch only — council" in src and "council_gate.check" in src


def test_ea_fails_closed_by_default():
    src = open("mt5_bridge/WildChance_PropEA_v4.mq5").read()
    assert "InpGateFailOpen      = false" in src and "Sleep(1500); g = AppGate" in src


def test_candlerange_card_fails_closed_and_shows_council(monkeypatch):
    import asyncio
    from services import candlerange_service as crs, council_gate, trade_gate

    async def weekly(sym):
        return None
    monkeypatch.setattr(trade_gate, "weekly_profile", weekly)
    monkeypatch.setattr(trade_gate, "evaluate", lambda *a, **k: {"allow": True, "reasons": []})
    plan = {"side": "SELL", "entry": 1.09778, "stop": 1.09878, "tp1": 1.0971, "tp2": 1.09642, "rr_tp2": 1.36}
    monkeypatch.setattr(crs, "plan_from_anchor", lambda sym, a: dict(plan))
    res = lambda: {"symbol": "EUR/USD", "price": 1.0972, "anchors": {"1300": {"state": "reversal", "candle_dir": "bullish",   # noqa: E731
                   "break_dir": "down", "body_low": 1.09778, "body_high": 1.09846}}}

    async def boom(*a, **k):
        raise RuntimeError("feeds down")

    async def go(*a, **k):
        return {"pass": True, "verdict": "GO", "line": "🏛 Council *GO* (5 for / 0 against)"}
    loop = asyncio.new_event_loop()
    try:
        fn = crs.add_plans
        monkeypatch.setattr(council_gate, "check", boom)
        r = loop.run_until_complete(fn(res()))
        assert r["anchors"]["1300"]["plan"]["gate"]["allow"] is False
        assert "SELL limit" not in (crs.format_alert(r) or "")
        monkeypatch.setattr(council_gate, "check", go)
        r = loop.run_until_complete(fn(res()))
        txt = crs.format_alert(r)
        assert "SELL limit" in txt and "Council *GO*" in txt
    finally:
        loop.close()
