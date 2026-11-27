import asyncio

from services import ea_setups as es


def run(c):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(c)
    finally:
        loop.close()


def test_reason_parsing():
    assert es.reason_of("BLOCKED: [CHOP-TRAP] ADX 19.0") == "CHOP-TRAP"
    assert es.reason_of("BLOCKED: SPREAD 25.0p") == "SPREAD"
    assert es.reason_of("COUNCIL: council HOLD") == "COUNCIL"
    assert es.reason_of("ENTERED") == "ENTERED"


def test_record_dedupes_pings_and_summarises(monkeypatch):
    from services import data_store, owner_notify
    store, sent = {}, []

    async def get(k):
        return store.get(k), None

    async def put(k, v):
        store[k] = v
        return True

    async def send(t):
        sent.append(t)
        return True
    monkeypatch.setattr(data_store, "get", get)
    monkeypatch.setattr(data_store, "put", put)
    monkeypatch.setattr(owner_notify, "send", send)
    monkeypatch.setattr(es, "_pinged", {})
    base = {"symbol": "EURUSD.m", "side": "SELL", "setup": "M", "entry": 1.0975, "stop": 1.0985, "t": 1000}
    run(es.record({**base, "quality": 6, "outcome": "BLOCKED: [CHOP-TRAP] ADX 19.7"}))
    assert run(es.record({**base, "quality": 6, "outcome": "BLOCKED: [CHOP-TRAP] ADX 19.7"}))["duplicate"]
    run(es.record({**base, "symbol": "BTCUSD.m", "quality": 8, "outcome": "COUNCIL: council HOLD"}))
    run(es.record({**base, "symbol": "US30.m", "side": "BUY", "setup": "W", "quality": 7, "outcome": "ENTERED"}))
    assert len(store[es.KEY]) == 3
    assert len(sent) == 2 and "US30.m BUY W q7/10" in sent[1]           # q6 block silent; q8 block + entry pinged
    s = es.summary(store[es.KEY])
    assert s == {"setups": 3, "entered": 1, "blocked": 2, "top_blocks": [("CHOP-TRAP", 1), ("COUNCIL", 1)],
                 "markets": 3}
    assert "3 setups on 3 markets — 1 entered" in es.summary_line(s)


def test_ea_logs_setups_and_relative_spread():
    src = open("mt5_bridge/WildChance_PropEA_v4.mq5").read()
    assert "bool IsFxSymbol()" in src and "InpMaxSpreadATR * atrN" in src
    assert src.count("LogSetup(") >= 4 and '"/bridge/setup"' in src and "SendNotification(" in src


def test_ea_auto_opens_the_market_list():
    src = open("mt5_bridge/WildChance_PropEA_v4.mq5").read()
    assert "void AutoOpenCharts()" in src and 'ChartSaveTemplate(0, "wildchance_auto")' in src
    assert 'ChartApplyTemplate(id, "wildchance_auto.tpl")' in src and "BTCUSD" in src and "GER40" in src
    assert '"USTEC"' in src and '"XTIUSD"' in src and "ChartIsOpen(sym)" in src


def test_pool_fill_runs_on_the_app_loop_from_a_worker_thread():
    """A worker-thread price fill must not spin up its own loop (shared pools break)."""
    import asyncio
    from wildchance import wildchance_scraper as wc
    seen = {}

    async def fake_alt(sym, iv, n):
        seen["loop"] = asyncio.get_running_loop()
        return {"values": [{"close": "1.5"}], "source": "t"}

    async def none(*a, **k):
        return None

    async def main():
        import services.price_pool as pp, services.broker_feed as bf
        old = (pp.alt_values, bf.td_values, wc.MAIN_LOOP)
        pp.alt_values, bf.td_values, wc.MAIN_LOOP = fake_alt, none, asyncio.get_running_loop()
        try:
            out = {}
            n = await asyncio.to_thread(wc._pool_fill, ["EURUSD"], out)
            return n, out, asyncio.get_running_loop()
        finally:
            pp.alt_values, bf.td_values, wc.MAIN_LOOP = old
    n, out, loop = asyncio.new_event_loop().run_until_complete(main())
    assert n == 1 and out["EURUSD"] == 1.5 and seen["loop"] is loop
