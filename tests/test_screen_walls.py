import datetime as dt

from services import options_levels as ol


def _csv(tmp_path):
    p = tmp_path / "s.csv"
    p.write_text("symbol,expiry,put25,call25,put10,call10,source\n"
                 "GBPJPY,2026-12-08,206.21,210.22,203.30,211.87,owner screenshot 2026-11-24 (2W ATM 208.45 IV 7.09%)\n"
                 "GBPJPY,2026-12-24,206.35,212.5,203.0,,owner screenshot 2026-11-24 (1M ATM 209.86)\n")
    return str(p)


def test_screen_levels_nearest_expiry(tmp_path):
    sc = ol.screen_levels("GBP/JPY", dt.date(2026, 11, 25), _csv(tmp_path))
    assert sc["ok"] and sc["underlying"] == "vol screens" and sc["expiry"] == "2026-12-08"
    prices = [x["price"] for x in sc["levels"]]
    assert prices == sorted(prices, reverse=True) and 208.45 in prices and 211.87 in prices
    assert any("ceiling" in x["label"] for x in sc["levels"]) and any("floor" in x["label"] for x in sc["levels"])
    assert ol.screen_levels("EUR/GBP", dt.date(2026, 11, 25), _csv(tmp_path)) == {}


def test_liquidity_guard():
    assert not ol._liquid({"strikes": {1: {}}, "call_oi": 0, "put_oi": 620})          # NZD-like
    assert not ol._liquid({"strikes": {1: {}}, "call_oi": 53, "put_oi": 46})           # Dow-like
    assert ol._liquid({"strikes": {1: {}}, "call_oi": 47356, "put_oi": 103079})       # EUR
    assert not ol._liquid(None)


def test_run_uses_screens_for_crosses(monkeypatch, tmp_path):
    import asyncio
    path = _csv(tmp_path)
    real = ol.screen_levels
    monkeypatch.setattr(ol, "screen_levels", lambda s, today=None, path_=None: real(s, dt.date(2026, 11, 25), path))
    r = asyncio.new_event_loop().run_until_complete(ol.run("GBP/JPY"))
    assert r["ok"] and r["underlying"] == "vol screens"
    r2 = asyncio.new_event_loop().run_until_complete(ol.run("EUR/GBP"))
    assert not r2["ok"] and "send this pair's options screens" in r2["reason"]
