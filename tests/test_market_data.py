"""Polymarket, Deribit options, expiry calendar, composite sentiment, Myfxbook fallback."""

import datetime as dt
import json


# --- expiry calendar -------------------------------------------------------------------
def test_expiry_rules():
    from derivs.expiry import equity_monthly, cme_fx, cme_gold, last_weekday, upcoming, flags_for
    assert equity_monthly(2026, 12) == dt.date(2026, 12, 18)        # 3rd Friday
    assert cme_fx(2026, 12) == dt.date(2026, 12, 4)                 # 2 Fridays before 3rd Wed
    assert cme_gold(2027, 1) == dt.date(2026, 12, 28)               # 4th last biz day prior month
    assert cme_gold(2026, 12) == dt.date(2026, 11, 25)
    assert last_weekday(2026, 12, 4) == dt.date(2026, 12, 25)       # Deribit quarterly
    up = upcoming(dt.date(2026, 11, 22), 35)
    assert any(e["type"].startswith("QUARTERLY (triple") and e["date"] == "2026-12-18" for e in up)
    assert [e["date"] for e in up] == sorted(e["date"] for e in up)
    assert flags_for("XAUUSD", dt.date(2026, 11, 24)) and not flags_for("XAUUSD", dt.date(2026, 12, 10))
    assert flags_for("US500", dt.date(2026, 12, 18))


# --- options open interest -------------------------------------------------------------
def test_deribit_parse_and_max_pain():
    from derivs.options_oi import parse_deribit, by_expiry
    raw = [{"instrument_name": "BTC-25DEC26-90000-P", "open_interest": 500},
           {"instrument_name": "BTC-25DEC26-100000-C", "open_interest": 800},
           {"instrument_name": "BTC-25DEC26-95000-C", "open_interest": 100},
           {"instrument_name": "BTC-25DEC26-95000-P", "open_interest": 100},
           {"instrument_name": "bad-name"}]
    rows = parse_deribit(raw)
    assert len(rows) == 4 and rows[0]["expiry"] == "2026-12-25"
    t = by_expiry(rows, spot=93000)[0]
    assert t["call_wall"] == 100000 and t["put_wall"] == 90000
    assert t["max_pain"] == 95000 and t["put_call_ratio"] == round(600 / 900, 2)
    assert t["max_pain_vs_spot_pct"] == round(100 * 2000 / 93000, 2)


# --- Polymarket ------------------------------------------------------------------------
def test_polymarket_rules_parse_and_score():
    from services import polymarket_service as pm
    eff = pm.match_rules("Will the Fed cut rates in December?")
    assert eff["XAUUSD"] > 0 and eff["EURUSD"] > 0 and eff["USDJPY"] < 0 and eff["US500"] > 0
    assert pm.match_rules("Will Taylor Swift win a Grammy?") is None
    m = pm.parse_market({"question": "Fed cut in December?", "outcomes": '["Yes","No"]',
                         "outcomePrices": '["0.8","0.2"]', "volume": "250000",
                         "endDate": "2026-12-10T00:00:00Z"})
    assert m["p_yes"] == 0.8 and m["end"] == "2026-12-10"
    assert pm.parse_market({"outcomes": '["A","B","C"]', "outcomePrices": "[]"}) is None
    m["effects"] = eff
    hike = dict(m, question="Fed hike?", p_yes=0.1, effects=pm.match_rules("Will the Fed hike?"))
    sc = pm.score_assets([m, hike])
    assert sc["XAUUSD"]["score"] > 0.5 and sc["USDJPY"]["score"] < 0 and sc["XAUUSD"]["markets"] == 2


# --- composite -------------------------------------------------------------------------
def test_composite_and_retail_contrarian():
    from services.market_sentiment import combine, retail_contrarian
    assert retail_contrarian(80) == -1.0 and retail_contrarian(35) == 0.5
    r = combine({"news": -0.7, "polymarket": -0.5, "retail": None})
    assert r["lock"] == "SELL_ONLY" and r["agree"] == 2 and r["bias"] == "BEARISH"
    r = combine({"news": -0.9, "polymarket": None, "retail": None})
    assert r["lock"] is None                                       # one source can't lock
    r = combine({"news": 0.8, "polymarket": -0.6, "retail": -0.6})
    assert r["lock"] is None and abs(r["composite"]) < 0.5        # sources disagree
    assert combine({"news": None, "polymarket": None, "retail": None})["composite"] is None


# --- Myfxbook: never a stale June snapshot ---------------------------------------------
def test_myfxbook_failure_is_neutral_not_june(monkeypatch):
    import urllib.request
    from wildchance import wildchance_scraper as wc
    monkeypatch.setattr(wc, "MYFXBOOK_EMAIL", "a@b.c")
    monkeypatch.setattr(wc, "MYFXBOOK_PASSWORD", "x")
    wc._retail_cache.update(data=None, t=0.0)

    def boom(*a, **k):
        raise OSError("blocked")
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    out = wc.fetch_retail()
    assert all(v == {"long_pct": 50, "short_pct": 50, "stale": True} for v in out.values())


def test_myfxbook_live_then_cached(monkeypatch):
    import io, urllib.request
    from wildchance import wildchance_scraper as wc
    monkeypatch.setattr(wc, "MYFXBOOK_EMAIL", "a@b.c")
    monkeypatch.setattr(wc, "MYFXBOOK_PASSWORD", "x")
    wc._retail_cache.update(data=None, t=0.0)
    calls = []

    class R(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake(url, timeout=30):
        calls.append(url)
        if "login" in url:
            return R(b'{"error": false, "session": "S"}')
        if "outlook" in url:
            return R(json.dumps({"error": False, "symbols": [
                {"name": "EURUSD", "longPercentage": 72, "shortPercentage": 28}]}).encode())
        return R(b'{"error": false}')
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    out = wc.fetch_retail()
    assert out["EUR/USD"] == {"long_pct": 72, "short_pct": 28}
    n = len(calls)
    wc.fetch_retail()
    assert len(calls) == n                                         # 6h cache, no re-login
    wc._retail_cache.update(data=None, t=0.0)


def test_new_routes_mounted():
    from main import app
    paths = {r.path for r in app.routes}
    assert {"/options/expiry", "/options/btc", "/sentiment/polymarket",
            "/sentiment/composite/{symbol}"} <= paths
    from fastapi.testclient import TestClient
    r = TestClient(app).get("/options/expiry?days=40").json()
    assert r["expiries"] and "holidays" in r["note"]
