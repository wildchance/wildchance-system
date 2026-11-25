import asyncio

from services import cron_schedule, options_levels


def test_reconcile_is_called_with_get(monkeypatch):
    monkeypatch.setattr(cron_schedule, "config", lambda k, default=None: "tok" if k == "EXECUTION_TOKEN" else default)
    calls = [c for j in cron_schedule.jobs() for c in j.calls if "/execution/reconcile" in c[1]]
    assert calls and all(m == cron_schedule.G for m, _ in calls)


def test_polygon_options_pause_on_not_entitled(monkeypatch):
    from services import data_feeds
    async def fake_get(src, url, params=None, **k):
        data_feeds.record(src, False, err='HTTP 403: {"status":"NOT_AUTHORIZED","message":"You are not entitled to this data."')
        return None
    monkeypatch.setattr(data_feeds, "_get", fake_get)
    options_levels._polygon_off["until"] = 0
    assert asyncio.run(options_levels._polygon_chain("SPY")) == {}
    assert options_levels._polygon_off["until"] > 0
    called = []
    async def boom(*a, **k):
        called.append(1)
    monkeypatch.setattr(data_feeds, "_get", boom)
    asyncio.run(options_levels._polygon_chain("SPY"))
    assert not called
    options_levels._polygon_off["until"] = 0


def test_gold_price_falls_back_to_pool(monkeypatch):
    from utils import price_fetcher as pf
    async def none(*a, **k):
        return None
    async def pool(symbol, interval, n, timezone="UTC"):
        return {"values": [{"close": 4012.5}]}
    monkeypatch.setattr(pf, "_twelvedata_price", none)
    monkeypatch.setattr(pf, "_metals_price", none)
    import services.price_pool as pp
    monkeypatch.setattr(pp, "alt_values", pool)
    assert asyncio.run(pf.get_fallback_price("XAU/USD")) == 4012.5
