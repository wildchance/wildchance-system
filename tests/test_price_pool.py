import time

import pytest

from services import price_pool as pp


def test_kind_and_plan():
    assert pp.kind("BTC/USD") == "crypto" and pp.plan("BTC/USD")[0] == ("binance", "BTCUSDT")
    assert pp.kind("XAU/USD") == "proxy" and pp.plan("XAU/USD")[0] == ("binance", "PAXGUSDT")
    assert pp.kind("NAS100") == "index" and pp.plan("NAS100") == [("yahoo_intraday", "^NDX")]
    assert pp.plan("EUR/USD") == [("yahoo_intraday", "EURUSD=X"), ("kraken", "EURUSD"), ("tiingo_fx", "EURUSD")]
    assert pp.plan("GBP/CAD") == [("yahoo_intraday", "GBPCAD=X"), ("tiingo_fx", "GBPCAD")]
    assert pp.plan("USD/CNH")[0] == ("yahoo_intraday", "CNH=X") and pp.plan("USD/ZAR")[0] == ("yahoo_intraday", "ZAR=X")
    assert ("kraken", "PAXGUSD") in pp.plan("XAU/USD") and ("kraken", "XBTUSD") in pp.plan("BTC/USD")
    assert pp.kind("NVDA") == "stock"


def test_parsers():
    y = {"chart": {"result": [{"timestamp": [100, 200], "indicators": {"quote": [
        {"open": [1, None], "high": [2, 3], "low": [0.5, 1], "close": [1.5, 2]}]}}]}}
    assert pp.parse_yahoo(y) == [{"t": 100, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5}]
    assert pp.parse_binance([[100000, "1", "2", "0.5", "1.5"]])[0]["t"] == 100
    assert pp.parse_tiingo_fx([{"date": "2026-11-24T10:00:00.000Z", "open": 1, "high": 2, "low": 0.5,
                                "close": 1.2}])[0]["close"] == 1.2


def test_aggregate_4h():
    base = 1795500000 - 1795500000 % 14400          # a 4h boundary in UTC
    bars = [{"t": base + i * 3600, "open": i, "high": 10 + i, "low": -i, "close": i + 0.5} for i in range(6)]
    agg = pp.aggregate(bars, 240, "UTC")
    assert len(agg) == 2 and agg[0]["open"] == 0 and agg[0]["high"] == 13 and agg[0]["low"] == -3
    assert agg[0]["close"] == 3.5 and agg[1]["open"] == 4


def test_basis_correct_needs_fresh_reference():
    bars = [{"t": 1000, "open": 3650, "high": 3660, "low": 3640, "close": 3650}]
    assert pp.basis_correct(bars, None) is None
    assert pp.basis_correct(bars, (1000, 3624.0), now=1000 + 73 * 3600) is None      # older than 72 h
    assert pp.basis_correct(bars, (1000, 3624.0), now=1000 + 7 * 3600) is not None  # 7 h is fine now
    out = pp.basis_correct(bars, (1000, 3624.0), now=1100)
    assert round(out[0]["close"], 2) == 3624.0 and out[0]["high"] > out[0]["close"]


def test_to_td_shape_newest_first():
    bars = [{"t": 1795500000 + i * 3600, "open": 1, "high": 2, "low": 0, "close": i} for i in range(5)]
    d = pp.to_td(bars, "1h", "America/New_York", 3, "pool:yahoo_intraday")
    assert [v["close"] for v in d["values"]] == [4, 3, 2] and len(d["values"][0]["datetime"]) == 19


@pytest.mark.parametrize("enabled", [True])
def test_alt_values_uses_cache_and_corrects_gold(monkeypatch, enabled):
    import asyncio
    monkeypatch.setattr(pp, "POOL_ENABLED", enabled)
    now = int(time.time())
    pp._cache.clear()
    pp._cache["binance:PAXGUSDT:1h"] = (time.time(), [{"t": now - 3600, "open": 3640, "high": 3660,
                                                        "low": 3630, "close": 3650}], 5)
    pp.note_reference("XAU/USD", 3624.0)
    d = asyncio.new_event_loop().run_until_complete(pp.alt_values("XAU/USD", "1h", 5))
    assert d["source"] == "pool:binance" and round(d["values"][0]["close"], 2) == 3624.0


def test_time_series_falls_back_to_pool_when_td_queue_full(monkeypatch):
    import asyncio
    from services import ohlc_service as os_
    calls = []

    async def td(params, wait):
        calls.append(wait)
        return {"status": "error", "code": 429}

    async def alt(*a, **k):
        return {"values": [{"datetime": "2026-11-24 09:00:00", "open": 1, "high": 1, "low": 1, "close": 1}],
                "source": "pool:yahoo_intraday"}
    monkeypatch.setattr(os_, "TWELVEDATA_KEY", "k")
    monkeypatch.setattr(os_, "_td_call", td)
    monkeypatch.setattr(pp, "POOL_ENABLED", True)
    monkeypatch.setattr(pp, "alt_values", alt)
    d = asyncio.new_event_loop().run_until_complete(
        os_.time_series({"symbol": "EUR/USD", "interval": "1h", "outputsize": 5}, broker=False))
    assert d["source"] == "pool:yahoo_intraday" and calls == [pp.POOL_TD_WAIT_S]


def test_kraken_parser():
    d = {"error": [], "result": {"ZEURZUSD": [[1700000000, "1.0850", "1.0860", "1.0840", "1.0855", "1.085", "12.5", 9]],
                                 "last": 1700000000}}
    assert pp.parse_kraken(d) == [{"t": 1700000000, "open": 1.085, "high": 1.086, "low": 1.084, "close": 1.0855}]
    assert pp.parse_kraken({"error": ["EQuery:Unknown asset pair"]}) == []


def test_proxy_reference_survives_restart(monkeypatch):
    import asyncio
    from services import data_store
    store = {"proxy_refs": {"XAUUSD": [1795600000.0, 3571.5]}}

    async def get(k):
        return store.get(k), None

    async def put(k, v):
        store[k] = v
        return True
    monkeypatch.setattr(data_store, "get", get)
    monkeypatch.setattr(data_store, "put", put)
    monkeypatch.setattr(pp, "_ref", {})
    monkeypatch.setattr(pp, "_ref_saved", {"t": 0.0, "loaded": False})
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(pp._ref_sync())
        assert pp._ref["XAUUSD"] == (1795600000.0, 3571.5)          # restored after a restart
        pp.note_reference("XAU/USD", 3580.0)
        loop.run_until_complete(pp._ref_sync())
        assert store["proxy_refs"]["XAUUSD"][1] == 3580.0             # newer one saved
    finally:
        loop.close()



def test_short_cached_history_does_not_answer_a_longer_request(monkeypatch):
    import asyncio
    monkeypatch.setattr(pp, "POOL_ENABLED", True)
    now = int(time.time())
    pp._cache.clear()
    short = [{"t": now - 86400 * i, "open": 1, "high": 2, "low": 0.5, "close": 1.1} for i in range(80)]
    pp._cache["yahoo_intraday:EURUSD=X:1day"] = (time.time(), short, 80)
    calls = []

    async def yahoo(tick, interval, n):
        calls.append(n)
        return [{"t": now - 86400 * i, "open": 1, "high": 2, "low": 0.5, "close": 1.2} for i in range(300)]
    monkeypatch.setattr(pp, "_yahoo", yahoo)
    loop = asyncio.new_event_loop()
    try:
        d80 = loop.run_until_complete(pp.alt_values("EUR/USD", "1day", 80))
        d300 = loop.run_until_complete(pp.alt_values("EUR/USD", "1day", 300))
    finally:
        loop.close()
    assert not calls[:0] and len(d80["values"]) == 80                     # cache answers the same size
    assert calls == [300] and len(d300["values"]) == 300                   # a longer ask refetches
