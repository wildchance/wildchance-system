import asyncio


def test_live_read_survives_missing_gold_quote(monkeypatch):
    from services import retracement_service as rsvc
    import services.ohlc_service as osvc
    import utils.price_fetcher as pf
    bars = [{"datetime": f"2026-11-2{i // 6} {i % 6 * 4:02d}:00:00", "open": 3600 + i, "high": 3610 + i,
             "low": 3590 + i, "close": 3605 + i} for i in range(40)]

    async def raw(*a, **k):
        return bars

    async def none(*a, **k):
        return None

    async def bias():
        return None

    async def box(w):
        return None
    monkeypatch.setattr(osvc, "fetch_ohlc_raw", raw)
    monkeypatch.setattr(pf, "get_forex_price", none)                 # TwelveData out of credits
    monkeypatch.setattr(rsvc, "_htf_bias", bias)
    monkeypatch.setattr(rsvc, "_box", box)
    loop = asyncio.new_event_loop()
    try:
        r = loop.run_until_complete(rsvc.live_read())
    finally:
        loop.close()
    assert r["state"] and r["price"] > 0
