"""AI sentiment (NVIDIA), Bollinger backtest/sweep, calendar fallback (EA MT5 calendar)."""

import datetime as dt
import math
import random


# --- 1. AI sentiment -------------------------------------------------------------------
def test_parse_verdict_and_locks():
    from services import ai_sentiment as ai
    v = ai.parse_verdict('Sure: {"score": -0.72, "confidence": 0.8, "drivers": ["Fed hawkish"]}')
    assert v["bias"] == "BEARISH" and v["score"] == -0.72 and ai.lock_from(v) == "SELL_ONLY"
    assert ai.lock_from(ai.parse_verdict('{"score": 0.9, "confidence": 0.3}')) is None  # unsure
    assert ai.lock_from(ai.parse_verdict('{"score": 0.3, "confidence": 0.9}')) is None  # weak
    assert ai.parse_verdict("no json here") is None
    assert ai.parse_verdict('{"score": 7, "confidence": 2}')["score"] == 1.0          # clamped
    msgs = ai.build_messages("XAUUSD", ["gold hits record", "fed cuts"])
    assert "XAUUSD" in msgs[1]["content"] and "JSON" in msgs[0]["content"]


def test_auto_locks_opt_in_and_manual_wins(monkeypatch):
    import time
    from services import ai_sentiment as ai
    from services import swing_service as sw
    ai._cache.clear()
    ai._cache["BTCUSD"] = {"lock": "BUY_ONLY", "_t": time.time()}
    ai._cache["XAUUSD"] = {"lock": "SELL_ONLY", "_t": time.time()}
    monkeypatch.setattr(ai, "AUTO_BIAS", False)
    assert ai.cached_locks() == {}
    monkeypatch.setattr(ai, "AUTO_BIAS", True)
    monkeypatch.setattr(sw, "config", lambda k, default=None: '{"BTCUSD":"SELL_ONLY"}'
                        if k == "MACRO_BIAS" else default)
    locks = sw.macro_bias()
    assert locks["BTCUSD"] == "SELL_ONLY" and locks["XAUUSD"] == "SELL_ONLY"
    ai._cache.clear()


def test_score_without_key_is_off(monkeypatch):
    import asyncio
    from services import ai_sentiment as ai
    monkeypatch.setattr(ai, "NVIDIA_API_KEY", None)
    loop = asyncio.new_event_loop()
    try:
        assert loop.run_until_complete(ai.score("XAUUSD", force=True))["status"] == "OFF"
    finally:
        loop.close()


# --- 2. Bollinger backtest + sweep -----------------------------------------------------
def _mean_reverting(n=900, seed=1):
    random.seed(seed)
    x, bars, d0 = 0.0, [], dt.date(2022, 1, 1)
    for i in range(n):
        x = 0.85 * x + random.gauss(0, 1.0)                # strong pull back to 100
        c = 100 + x
        o = c + random.gauss(0, 0.2)
        bars.append((d0 + dt.timedelta(days=i), o, max(o, c) + 0.3, min(o, c) - 0.3, c))
    return bars


def test_simulate_finds_edge_on_mean_reverting_series():
    from backtest.bb_engine import simulate
    out = simulate(_mean_reverting(), period=20, k=2.0, max_hold=10)
    assert out["n"] >= 10 and out["expectancy_r"] > 0 and out["win_rate"] > 0.5
    t = out["trades"][0]
    assert t["side"] in ("BUY", "SELL") and t["why"] in ("stop", "target", "time")


def test_simulate_no_edge_on_trend():
    from backtest.bb_engine import simulate
    d0 = dt.date(2022, 1, 1)
    bars = [(d0 + dt.timedelta(days=i), 100 + i, 101 + i, 99.5 + i, 100.6 + i) for i in range(400)]
    out = simulate(bars)
    assert out["n"] == 0 or (out["expectancy_r"] or 0) <= 0.5


def test_sweep_is_oos_and_ranked():
    from backtest.bb_engine import sweep
    grid = {"period": [20], "k": [1.8, 2.0], "max_hold": [10], "walk_filter": [False]}
    out = sweep(_mean_reverting(1200, seed=4), grid=grid)
    assert out["configs"] == 2 and out["best"]
    assert set(out["best"]) == {"params", "in_sample", "out_of_sample"}
    assert out["verdict"].startswith(("POSITIVE", "NO ROBUST"))


def test_metrics_drawdown():
    from backtest.bb_engine import metrics
    m = metrics([1, -1, -1, 2, -1])
    assert m["n"] == 5 and m["max_dd_r"] == 2 and m["profit_factor"] == 1.0


# --- 3. calendar: no stale data, EA MT5 calendar fallback ------------------------------
def test_calendar_falls_back_to_ea_then_never_stale(monkeypatch):
    from wildchance import wildchance_scraper as wc
    import urllib.request
    def boom(*a, **k):
        raise OSError("429 Too Many Requests")
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    wc._cal_cache.update(events=None, t=0.0, source=None)
    wc._ea_cal.update(events=[], t=0.0)
    assert wc.fetch_calendar() == []                       # was: June 2026 snapshot
    t = int(dt.datetime(2026, 12, 4, 13, 30, tzinfo=dt.timezone.utc).timestamp())
    assert wc.set_ea_calendar([{"t": t, "ccy": "usd", "event": "Nonfarm Payrolls",
                                "impact": "High", "actual": None, "forecast": "150"}]) == 1
    ev = wc.fetch_calendar()
    assert ev[0]["date"] == "2026-12-04" and ev[0]["ccy"] == "USD" and ev[0]["impact"] == "high"
    assert ev[0]["source"] == "mt5" and ev[0]["forecast"] == 150
    assert wc.calendar_status()["ea_events"] == 1


def test_calendar_caches_good_feed(monkeypatch):
    import io, json, urllib.request
    from wildchance import wildchance_scraper as wc
    calls = []

    class R(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def ok(*a, **k):
        calls.append(1)
        return R(json.dumps([{"date": "2026-11-27T13:30:00-05:00", "country": "USD",
                              "title": "Core PCE", "impact": "High"}]).encode())
    monkeypatch.setattr(urllib.request, "urlopen", ok)
    wc._cal_cache.update(events=None, t=0.0, source=None)
    assert wc.fetch_calendar()[0]["event"] == "Core PCE"
    wc.fetch_calendar()
    assert len(calls) == 1                                 # second call served from cache
    wc._cal_cache.update(events=None, t=0.0, source=None)


def test_bridge_calendar_and_export_routes(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    import routes.bridge as rb
    from main import app
    monkeypatch.setattr(rb, "TOKEN", "tk")
    c = TestClient(app)
    t = int(dt.datetime(2026, 12, 4, 13, 30, tzinfo=dt.timezone.utc).timestamp())
    body = {"events": [{"t": t, "ccy": "USD", "event": "CPI m/m", "impact": "high"}]}
    assert c.post("/bridge/calendar", json=body).status_code == 403
    r = c.post("/bridge/calendar", json=body, headers={"X-Bridge-Token": "tk"})
    assert r.json() == {"ok": True, "events": 1}
    assert c.get("/bridge/calendar").json()["high_impact_next"][0]["event"] == "CPI m/m"
    assert c.get("/bridge/export?symbol=XAUUSD").status_code == 403
    paths = {r.path for r in app.routes}
    assert {"/sentiment/scan", "/swing/backtest/{symbol:path}", "/swing/sweep/{symbol:path}",
            "/bridge/export"} <= paths
