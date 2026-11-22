"""TwelveData limiter + cache (free plan 8/min, 1 credit per symbol in a batch)."""

import asyncio

import httpx
import pytest

from services import td_limiter as tl


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    tl._slots.clear(); tl._cache.clear()
    tl._stats.update(day=tl._day(), requests=0, cache_hits=0, rejected=0)
    monkeypatch.setattr(tl, "RATE", 7)
    monkeypatch.setattr(tl, "DAILY_LIMIT", 790)
    yield


def test_eighth_request_waits_a_minute():
    waits = [tl.reserve(max_wait=120, now=1000.0) for _ in range(8)]
    assert waits[:7] == [0.0] * 7 and waits[7] == pytest.approx(60.5)


def test_batch_credits_and_rollback():
    assert tl.credits_for("https://api.twelvedata.com/price?symbol=EUR/USD,GBP/USD,XAU/USD&apikey=x") == 3
    assert tl.reserve(max_wait=75, now=1000.0, credits=7) == 0.0
    assert tl.reserve(max_wait=10, now=1001.0, credits=3) is None     # would wait ~60s → rejected
    assert len(tl._slots) == 7 and tl._stats["rejected"] == 1           # rolled back, nothing held


def test_daily_budget(monkeypatch):
    monkeypatch.setattr(tl, "DAILY_LIMIT", 5)
    assert tl.reserve(max_wait=999, now=0.0, credits=5) == 0.0
    assert tl.reserve(max_wait=999, now=0.0) is None


def test_cache_key_ignores_apikey_and_ttl_by_interval():
    k1, ttl = tl.cache_key("https://api.twelvedata.com/time_series?symbol=XAU/USD&interval=1day&apikey=A")
    k2, _ = tl.cache_key("https://api.twelvedata.com/time_series?apikey=B&interval=1day&symbol=XAU/USD")
    assert k1 == k2 and "apikey" not in k1 and ttl == 43200
    assert tl.cache_key("https://api.twelvedata.com/time_series?interval=1h&symbol=X")[1] == 600
    assert tl.cache_key("https://api.twelvedata.com/price?symbol=X")[1] == 60


def test_installed_send_caches_and_never_caches_errors():
    calls = []

    def handler(req):
        calls.append(str(req.url))
        if "BAD" in str(req.url):
            return httpx.Response(200, json={"status": "error", "code": 429, "message": "x"})
        return httpx.Response(200, json={"values": [{"close": "1"}], "status": "ok"})

    tl._installed["done"] = False
    import httpx as hx
    orig = hx.AsyncClient.send
    try:
        tl.install()

        async def go():
            async with hx.AsyncClient(transport=hx.MockTransport(handler)) as c:
                u = "https://api.twelvedata.com/time_series?symbol=XAU/USD&interval=1day&apikey=k"
                a = (await c.get(u)).json()
                b = await c.get(u)
                bad = "https://api.twelvedata.com/time_series?symbol=BAD&interval=1day"
                await c.get(bad); await c.get(bad)
                other = await c.get("https://example.com/x")
                return a, b, other
        loop = asyncio.new_event_loop()
        try:
            a, b, other = loop.run_until_complete(go())
        finally:
            loop.close()
        assert a["values"] and b.headers.get("x-td-cache") == "hit"
        assert sum("XAU" in c for c in calls) == 1                 # second served from cache
        assert sum("BAD" in c for c in calls) == 2                 # errors not cached
        assert other.status_code == 200 and tl.status()["cache_hits_today"] == 1
    finally:
        hx.AsyncClient.send = orig
        tl._installed["done"] = False


def test_health_reports_limiter():
    from fastapi.testclient import TestClient
    from main import app
    h = TestClient(app).get("/health").json()
    assert h["twelvedata"]["rate_per_min"] >= 1 and "used_today" in h["twelvedata"]


def test_any_twelvedata_path_marks_gold_fresh():
    from services import freshness
    freshness.LAST_OK.clear()
    tl.record_fresh("https://api.twelvedata.com/price?symbol=XAU/USD,EUR/USD&apikey=k",
                    b'{"XAU/USD": {"price": "3540.1"}, "EUR/USD": {"status": "error"}}')
    assert "XAUUSD" in freshness.LAST_OK and "EURUSD" not in freshness.LAST_OK
    freshness.LAST_OK.clear()
    tl.record_fresh("https://api.twelvedata.com/price?symbol=XAU/USD", b'{"price": "3540"}')
    assert freshness.LAST_OK["XAUUSD"]["source"] == "twelvedata"
    freshness.LAST_OK.clear()
