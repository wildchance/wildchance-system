import asyncio
import datetime as dt

from services import freshness, shfe


def test_blackouts_flags_dark_markets(monkeypatch):
    wed = dt.datetime(2026, 11, 25, 15, 0, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(freshness, "LAST_OK", {"EURUSD": {"t": 1000.0, "source": "pool:yahoo_intraday"},
                                               "XAUUSD": {"t": 1000.0 - 200 * 60, "source": "twelvedata"}})
    r = freshness.blackouts(("EURUSD", "XAUUSD", "GBPUSD"), now_ts=1000.0, max_min=90, now=wed)
    assert r["dark"] == ["XAUUSD", "GBPUSD"] and r["symbols"][0]["status"] == "OK"
    sat = dt.datetime(2026, 11, 28, 12, 0, tzinfo=dt.timezone.utc)
    r = freshness.blackouts(("EURUSD", "BTCUSD"), now_ts=1000.0, max_min=90, now=sat)
    assert r["dark"] == ["BTCUSD"] and r["symbols"][0]["status"] == "CLOSED"     # crypto trades weekends


SAMPLE = {"o_curinstrument": [
    {"PRODUCTID": "au_f    ", "DELIVERYMONTH": "2612", "PRESETTLEMENTPRICE": 940.0, "CLOSEPRICE": 944.0,
     "SETTLEMENTPRICE": 945.5, "VOLUME": 200000, "OPENINTEREST": 180000, "OPENINTERESTCHG": 1200},
    {"PRODUCTID": "au_f    ", "DELIVERYMONTH": "2702", "PRESETTLEMENTPRICE": 941.0, "CLOSEPRICE": 946.0,
     "SETTLEMENTPRICE": 946.0, "VOLUME": 9000, "OPENINTEREST": 20000, "OPENINTERESTCHG": 10},
    {"PRODUCTID": "au_f    ", "DELIVERYMONTH": "小计", "SETTLEMENTPRICE": "", "OPENINTEREST": 200000},
    {"PRODUCTID": "cu_f    ", "DELIVERYMONTH": "2701", "PRESETTLEMENTPRICE": 80000, "SETTLEMENTPRICE": 80800,
     "OPENINTEREST": 150000},
    {"PRODUCTID": "rb_f    ", "DELIVERYMONTH": "2701", "SETTLEMENTPRICE": 3200, "OPENINTEREST": 9e6},
]}


def test_shfe_parse_and_premium():
    rows = shfe.parse(SAMPLE)
    assert set(rows) == {"au", "cu"} and rows["au"]["contract"] == "au2612" and rows["au"]["settle"] == 945.5
    assert shfe.day_change(rows["cu"]) == 1.0
    g = shfe.gold_premium(945.5, 7.10, 4120.0)          # 945.5 CNY/g → ≈ 4142.0 $/oz
    assert g["shfe_usd_oz"] == 4142.02 and g["premium_usd"] == 22.02 and "strong" in g["read"]
    assert "discount" in shfe.gold_premium(900.0, 7.10, 4120.0)["read"]


def test_shfe_unreachable_is_reported(monkeypatch):
    from services import data_feeds
    async def none(*a, **k):
        return None
    monkeypatch.setattr(data_feeds, "_get", none)
    r = asyncio.run(shfe.report())
    assert r["ok"] is False and "not reachable" in r["reason"]
    assert shfe.lines(r)[0].startswith("🇨🇳 SHFE:")


def test_scraper_falls_back_to_pool_not_snapshot(monkeypatch):
    from wildchance import wildchance_scraper as wc
    def fill(symbols, out):
        for s in symbols:
            out[s] = 1.2345
        return len(symbols)
    monkeypatch.setattr(wc, "_pool_fill", fill)
    monkeypatch.setattr(wc, "TWELVEDATA_KEY", "")
    p = wc.fetch_prices()
    assert all(v == 1.2345 for v in p.values()) and wc.PRICE_STATUS["live"] is True
    monkeypatch.setattr(wc, "_pool_fill", lambda s, o: 0)
    p = wc.fetch_prices()
    assert p == wc.PRICE_FALLBACK and wc.PRICE_STATUS["live"] is False
