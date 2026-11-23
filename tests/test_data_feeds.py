import datetime as dt

from services import data_feeds as df


def test_symbol_maps():
    assert df.stooq_symbol("EUR/USD") == "eurusd" and df.stooq_symbol("XAU/USD") == "xauusd"
    assert df.stooq_symbol("US30") == "^dji" and df.stooq_symbol("NVDA") == "nvda.us"
    assert df.binance_symbol("BTC/USD") == "BTCUSDT" and df.binance_symbol("EUR/USD") is None
    assert df.is_crypto("DOGE/USD") and not df.is_crypto("XAU/USD")


def test_price_parsers():
    stooq = "Date,Open,High,Low,Close,Volume\n2026-11-20,1.10,1.11,1.09,1.105,0\n2026-11-19,1.09,1.10,1.08,1.10,0\n"
    b = df.parse_ohlc_csv(stooq)
    assert b[0][0] == dt.date(2026, 11, 19) and b[-1][4] == 1.105
    vix = "DATE,OPEN,HIGH,LOW,CLOSE\n11/20/2026,18.1,19.0,17.5,18.6\n"
    assert df.parse_ohlc_csv(vix)[0] == (dt.date(2026, 11, 20), 18.1, 19.0, 17.5, 18.6)
    t = [{"date": "2026-11-20T00:00:00.000Z", "open": 1, "high": 2, "low": 0.5, "close": 1.5}]
    assert df.parse_tiingo(t)[0][4] == 1.5 and df.parse_tiingo([{"priceData": t}])[0][4] == 1.5
    k = [[1763596800000, "61000", "62000", "60000", "61629.49", "1"]]
    assert df.parse_binance(k)[0][4] == 61629.49


def test_sentiment_and_macro_parsers():
    av = {"feed": [{"ticker_sentiment": [{"ticker": "FOREX:EUR", "relevance_score": "0.8",
                                          "ticker_sentiment_score": "0.30"},
                                         {"ticker": "CRYPTO:BTC", "relevance_score": "0.5",
                                          "ticker_sentiment_score": "-0.4"}]},
                   {"ticker_sentiment": [{"ticker": "FOREX:EUR", "relevance_score": "0.2",
                                          "ticker_sentiment_score": "-0.20"}]}]}
    assert df.parse_av_sentiment(av, "FOREX:EUR") == {"score": 0.2, "articles": 2}
    mx = {"data": [{"entities": [{"symbol": "NVDA", "sentiment_score": 0.4}, {"symbol": "AMD", "sentiment_score": -1}]}]}
    assert df.parse_marketaux(mx, "NVDA") == {"score": 0.4, "articles": 1}
    ecb = "KEY,TIME_PERIOD,OBS_VALUE\nEXR.D,2026-11-19,1.0985\nEXR.D,2026-11-20,1.0991\n"
    assert df.parse_ecb_csv(ecb) == {"date": "2026-11-20", "value": 1.0991}
    tr = 'Date,"3 Mo","2 Yr","10 Yr","30 Yr"\n11/20/2026,4.10,3.90,4.30,4.60\n11/19/2026,4.1,3.9,4.2,4.5\n'
    y = df.parse_treasury_csv(tr)
    assert y["us10y"] == 4.30 and y["curve_2s10s"] == 0.4
    bls = {"Results": {"series": [{"seriesID": "CUUR0000SA0", "data": [
        {"year": "2026", "period": "M10", "value": "330.0"}, {"year": "2025", "period": "M10", "value": "320.0"}]}]}}
    assert df.parse_bls(bls)["cpi_index"]["yoy_pct"] == 3.12
    cg = {"data": {"market_cap_percentage": {"btc": 58.123, "eth": 9.5}, "total_market_cap": {"usd": 2.1e12},
                   "market_cap_change_percentage_24h_usd": -1.234}}
    assert df.parse_coingecko_global(cg)["btc_dominance"] == 58.12


def test_registry_ranks_by_recent_success():
    for _ in range(5):
        df.record("feed_a", True, 100)
        df.record("feed_b", False, err="HTTP 429")
    assert df.ranked(["feed_b", "feed_a"]) == ["feed_a", "feed_b"]
    st = df.status()["sources"]
    assert st["feed_a"]["score"] > 0.8 and st["feed_b"]["last_err"] == "HTTP 429"


def test_news_agent_votes_and_quote_inversion():
    from services import council as cn
    assert cn.vote_news({"score": 0.3, "sources": {"alphavantage": {}}}, "BUY")["vote"] == 1
    assert cn.vote_news({"score": 0.05}, "BUY")["vote"] == 0
    assert cn.vote_news(None, "SELL")["vote"] == 0


def test_summarise_base_rates():
    from services.yearly_cbdr import summarise
    ys = [{"year": 2000 + i, "up": 2.6, "down": -0.4, "close": 1.4} for i in range(6)]
    s = summarise(ys, source="auto-stooq")
    assert s["years"] == 6 and s["pct_reach_up"]["2.5"] == 100 and s["source"] == "auto-stooq"


def test_access_log_redacts_tokens():
    import logging
    import main
    rec = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                            ("10.0.0.1:0", "GET", "/data/sources?token=2dc3abc&x=1&api_key=zz", "1.1", 200), None)
    main._RedactTokens().filter(rec)
    assert rec.args[2] == "/data/sources?token=***&x=1&api_key=***"


def test_daily_chain_returns_longest_partial(monkeypatch):
    import asyncio
    async def short(s): return [(dt.date(2020, 1, 1) + dt.timedelta(days=i), 1, 1, 1, 1) for i in range(50)]
    async def none(s): return []
    monkeypatch.setattr(df, "DAILY_SOURCES", {"a": none, "b": short})
    loop = asyncio.new_event_loop()
    try:
        bars, src = loop.run_until_complete(df.daily_chain("XAU/USD", min_bars=200))
    finally:
        loop.close()
    assert src == "b" and len(bars) == 50
