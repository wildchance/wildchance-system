from routes import member_terminal as mt

FULL = {"as_of": "x", "macro": {"vix": {"close": 20}}, "strength": {"JPY": {"score": 1, "parts": {}},
        "CHF": {"score": .5, "parts": {}}, "USD": {"score": 0, "parts": {}}, "EUR": {"score": -.5, "parts": {}},
        "AUD": {"score": -1, "parts": {}}}, "pairings": [{"symbol": "AUD/JPY", "side": "SELL"}],
        "rotation": {"blocked": {}, "note": "n"}, "exposure": {"USD": 2}, "open_signals": [{"symbol": "EUR/USD"}],
        "walls": {"EUR/USD": {"source": "CME:EURUSD", "asof": "d", "pc": 2.2, "live": 1.09, "levels": []}},
        "calendar": [], "regimes": {"EUR/USD": {"regime": "RANGING", "trend": None, "er": .1, "atr_pct": 40}},
        "positioning": [], "backtests": {"z": []}, "audit": {"summary": "s"}, "signal_stats": {}}


def test_subscriber_view_hides_private_tiles():
    tr = mt.track_record([{"symbol": "USDJPY", "side": "SELL", "result": "win", "pips": 23.4, "source": "manual",
                           "closed_at": 1}, {"symbol": "EURUSD", "side": "BUY", "result": "open", "source": "z"}])
    v = mt.subscriber_view(FULL, tr)
    for k in ("open_signals", "exposure", "backtests", "audit", "signal_stats"):
        assert k not in v
    assert "source" not in v["walls"]["EUR/USD"] and "er" not in v["regimes"]["EUR/USD"]
    assert v["track"]["wins"] == 1 and [r["result"] for r in v["track"]["recent"]] == ["win"]   # open one hidden


def test_teaser_for_free_tier():
    v = mt.teaser_view(mt.subscriber_view(FULL, mt.track_record([])))
    assert v["teaser"] and list(v["strength"]) == ["JPY", "CHF", "EUR", "AUD"]
    assert "walls" not in v and "pairings" not in v and "upgrade" in v


def test_routes(monkeypatch):
    from fastapi.testclient import TestClient
    import main
    from saas.auth import current_user

    class U:
        tier = "free"
    c = TestClient(main.app)
    assert c.get("/members/terminal").status_code == 200                      # public page shell
    assert c.get("/members/terminal/data").status_code == 401                 # data needs the member key

    async def fake():
        return {**mt.subscriber_view(FULL, mt.track_record([]))}
    monkeypatch.setattr(mt, "_subscriber_data", fake)
    main.app.dependency_overrides[current_user] = lambda: U()
    try:
        d = c.get("/members/terminal/data").json()
        assert d["paid"] is False and d["teaser"]
    finally:
        main.app.dependency_overrides.clear()
