import asyncio

from services import flash_card as fc
from routes.cbdr import px


def test_pips_match_owner_samples():
    assert fc.pips("US30", "BUY", 42512.0, 42818.43) == 3064.3
    assert fc.pips("EUR/JPY", "SELL", 164.52, 163.981) == 53.9
    assert fc.pips("XAU/USD", "BUY", 4000.0, 4025.0) == 250.0          # cheetah = 250 pips
    assert fc.pips("BTC/USD", "SELL", 61000.0, 60500.0) == 500.0
    assert fc.pips("EUR/USD", "BUY", 1.1, 1.09) < 0


def test_render_png_and_losers_not_posted():
    png = fc.render("US30", "BUY", 42512.0, 42818.43, qr_url="https://t.me/x")
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 10_000
    loop = asyncio.new_event_loop()
    try:
        r = loop.run_until_complete(fc.post("EUR/USD", "BUY", 1.10, 1.09))
    finally:
        loop.close()
    assert r == {"sent": False, "reason": "not a winning trade"}
    assert "US30 BUY +3,064.3 pips" in fc.caption("US30", "BUY", 42512.0, 42818.43)


def test_prelondon_card_formats_prices():
    # the grey-zone line used to print raw floats (3501.0123300000005)
    vals = [3501.0123300000005, 3467.075320000001]
    assert "–".join(px("XAU/USD", v) for v in vals) == "3501.01–3467.08"


def test_telegram_float_noise_is_tidied():
    from services.signal_feed import tidy_numbers, _tidy_request
    import httpx, json
    t = "Grey buy 3501.0123300000005, 3467.075320000001 · sell 3704.6343899999997 · EURUSD 1.09902 · JPY 151.81900000001"
    out = tidy_numbers(t)
    assert out == "Grey buy 3501.01, 3467.08 · sell 3704.63 · EURUSD 1.09902 · JPY 151.819"
    assert tidy_numbers("1.10131999999999 and 0.5") == "1.10132 and 0.5"
    req = httpx.Request("POST", "https://api.telegram.org/botX/sendMessage",
                        json={"chat_id": 1, "text": "sell 3704.6343899999997"})
    new = _tidy_request(req)
    assert json.loads(new.content)["text"] == "sell 3704.63" and json.loads(new.content)["chat_id"] == 1


def test_head_requests_on_public_pages():
    from fastapi.testclient import TestClient
    import main
    c = TestClient(main.app)
    for path in ("/", "/pricing", "/members", "/status"):
        assert c.head(path).status_code == 200, path


TODAY = [  # owner's MT5 history, 2026-11-23 (screenshot)
    {"ticket": 1, "symbol": "USDCHFb.Z", "side": "BUY", "volume": 0.10, "open_price": 0.83954, "close_price": 0.84124, "net": 20.21, "close_time": 1795450000},
    {"ticket": 2, "symbol": "USDCHFb.Z", "side": "BUY", "volume": 0.01, "open_price": 0.83889, "close_price": 0.84390, "net": 5.94, "close_time": 1795450100},
    {"ticket": 3, "symbol": "EURUSDb.Z", "side": "SELL", "volume": 0.01, "open_price": 1.09818, "close_price": 1.08887, "net": 9.31, "close_time": 1795451000},
    {"ticket": 4, "symbol": "EURUSDb.Z", "side": "SELL", "volume": 0.01, "open_price": 1.09831, "close_price": 1.08884, "net": 9.47, "close_time": 1795451000},
    {"ticket": 5, "symbol": "EURUSDb.Z", "side": "SELL", "volume": 0.01, "open_price": 1.09838, "close_price": 1.08885, "net": 9.53, "close_time": 1795451000},
    {"ticket": 6, "symbol": "EURUSDb.Z", "side": "BUY", "volume": 0.01, "open_price": 1.10, "close_price": 1.09, "net": -10.0, "close_time": 1795451000},
]


def test_deals_group_into_one_card_per_symbol_side():
    g = {(x["symbol"], x["side"]): x for x in fc.group_deals(TODAY)}
    eu = g[("EURUSD", "SELL")]
    assert eu["lots"] == 0.03 and eu["tickets"] == ["3", "4", "5"] and round(eu["net"], 2) == 28.31
    assert round(fc.pips("EURUSD", "SELL", eu["open"], eu["close"]), 1) == 94.4
    ch = g[("USDCHF", "BUY")]
    assert ch["lots"] == 0.11 and round(fc.pips("USDCHF", "BUY", ch["open"], ch["close"]), 1) == 20.0


def test_post_from_deals_winners_only_and_once(monkeypatch):
    import asyncio
    from services import data_store
    store, sent = {}, []

    async def get(k):
        return store.get(k), None

    async def put(k, v):
        store[k] = v
        return True

    async def post(sym, side, o, c, when=None):
        sent.append((sym, side))
        return {"sent": True}
    monkeypatch.setattr(data_store, "get", get)
    monkeypatch.setattr(data_store, "put", put)
    monkeypatch.setattr(fc, "post", post)
    monkeypatch.setattr(fc, "FROM_DEALS", True)
    loop = asyncio.new_event_loop()
    try:
        r1 = loop.run_until_complete(fc.post_from_deals(TODAY))
        r2 = loop.run_until_complete(fc.post_from_deals(TODAY))          # EA re-sends → nothing new
    finally:
        loop.close()
    assert sorted(sent) == [("EURUSD", "SELL"), ("USDCHF", "BUY")]       # the losing EURUSD buy is skipped
    assert any(s["why"] == "not a winning close" for s in r1["skipped"])
    assert r2["new"] == 0 and r2["posted"] == []
