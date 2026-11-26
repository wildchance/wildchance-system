import asyncio
import time

from services import flash_card as fc


def _store(monkeypatch):
    from services import data_store
    store = {}

    async def get(k):
        return store.get(k), None

    async def put(k, v):
        store[k] = v
        return True
    monkeypatch.setattr(data_store, "get", get)
    monkeypatch.setattr(data_store, "put", put)
    return store


def test_same_trade_matches_within_tolerance():
    now = time.time()
    rec = [{"sym": "EURUSD", "side": "SELL", "open": 1.1650, "ts": now - 60, "src": "signal"}]
    assert fc.same_trade(rec, "EUR/USD", "SELL", 1.1655, now)           # 5 pips slippage = same trade
    assert not fc.same_trade(rec, "EURUSD", "BUY", 1.1650, now)         # other side
    assert not fc.same_trade(rec, "EURUSD", "SELL", 1.1700, now)        # 50 pips away = another trade
    assert not fc.same_trade(rec, "EURUSD", "SELL", 1.1650, now + fc.DEDUPE_H * 3600 + 1)
    assert not fc.same_trade(rec, "EURUSD", "SELL", 1.1650, now, ("deal",))


def test_broker_close_after_tracker_card_is_not_posted_twice(monkeypatch):
    _store(monkeypatch)
    loop = asyncio.new_event_loop()
    try:
        a = loop.run_until_complete(fc._same_trade_posted("EURUSD", "SELL", 1.1650, "signal"))
        b = loop.run_until_complete(fc._same_trade_posted("EURUSD", "SELL", 1.1660, "signal"))  # tracker TP2
        c = loop.run_until_complete(fc._same_trade_posted("EURUSD", "SELL", 1.1653, "deal"))    # EA close
        d = loop.run_until_complete(fc._same_trade_posted("GBPUSD", "BUY", 1.3300, "deal"))     # manual trade
        e = loop.run_until_complete(fc._same_trade_posted("GBPUSD", "BUY", 1.3302, "deal"))     # its partial
        f = loop.run_until_complete(fc._same_trade_posted("GBPUSD", "BUY", 1.3301, "signal"))   # tracker late
    finally:
        loop.close()
    assert (a, b, c, d, e, f) == (False, False, True, False, True, True)


def test_gate_verdict_combines_council_and_calendar(monkeypatch):
    from routes import bridge
    from services import council_gate, econ_calendar
    calls = []

    async def check(sym, side, *a, **k):
        calls.append((sym, side))
        return {"pass": True, "verdict": "GO", "risk_pct": 0.35, "regime": "trend", "for": 4, "against": 0}

    async def loaded():
        return None
    monkeypatch.setattr(council_gate, "check", check)
    monkeypatch.setattr(econ_calendar, "ensure_loaded", loaded)
    monkeypatch.setattr(econ_calendar, "blackout", lambda s: None)
    bridge._gate_cache.clear()
    loop = asyncio.new_event_loop()
    try:
        ok = loop.run_until_complete(bridge.gate_verdict("EURUSD.r", "buy"))
        again = loop.run_until_complete(bridge.gate_verdict("EURUSD", "BUY"))      # cached 60 s
        monkeypatch.setattr(econ_calendar, "blackout", lambda s: "event blackout: USD CPI 12:30 UTC (calendar)")
        bo = loop.run_until_complete(bridge.gate_verdict("XAUUSD", "SELL"))
    finally:
        loop.close()
    assert ok["allow"] is True and ok["risk_pct"] == 0.35 and ok["reason"] == "GO"
    assert calls[0] == ("EUR/USD", "BUY") and len(calls) == 2 and again == ok
    assert bo["allow"] is False and bo["verdict"] == "EVENT" and "CPI" in bo["reason"]


def test_gate_requires_bridge_token(monkeypatch):
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from routes import bridge
    monkeypatch.setattr(bridge, "TOKEN", "t0k")
    app = FastAPI()
    app.include_router(bridge.router)
    c = TestClient(app)
    assert c.get("/bridge/gate?symbol=EURUSD&side=BUY").status_code == 403
    assert c.get("/bridge/gate?symbol=EURUSD&side=BUY&token=t0k").status_code == 403   # header only


def test_ea_has_gate_and_parses_compact_json():
    src = open("mt5_bridge/WildChance_PropEA_v4.mq5").read()
    assert '#property version   "4.55"' in src
    assert "/bridge/gate?symbol=" in src and "X-Bridge-Token" in src
    assert 'riskPct = MathMin(riskPct, appRisk)' in src
    from fastapi.responses import JSONResponse
    body = JSONResponse({"allow": True, "risk_pct": 0.35}).body.decode()
    assert '"allow":true' in body and '"risk_pct":0.35' in body          # what the EA string-matches
