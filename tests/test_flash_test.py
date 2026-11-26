import asyncio

from services import flash_card as fc


def run(c):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(c)
    finally:
        loop.close()


ROWS = [{"symbol": "EUR/USD", "side": "SELL", "entry": 1.1650, "hits": [{"tp": 1, "price": 1.1610, "ts": 1795600000}],
         "engine": "sniper"},
        {"symbol": "USD/JPY", "side": "BUY", "entry": 155.0, "hits": [{"tp": 1, "price": 155.5, "ts": 1795700000}]},
        {"symbol": "EUR/USD", "side": "BUY", "entry": 1.10, "hits": []}]


def test_latest_win_picks_symbol(monkeypatch):
    from services import signal_tracker

    async def load():
        return ROWS
    monkeypatch.setattr(signal_tracker, "_load", load)
    w = run(fc.latest_win("EURUSD"))
    assert (w["symbol"], w["side"], w["open"], w["close"], w["tp"]) == ("EUR/USD", "SELL", 1.165, 1.161, 1)
    assert run(fc.latest_win())["symbol"] == "USD/JPY"
    assert run(fc.latest_win("GBP/USD")) is None


def test_send_test_goes_to_owner_only(monkeypatch):
    from services import owner_notify, signal_tracker
    import httpx
    sent = []

    async def load():
        return ROWS

    class Resp:
        status_code = 200

    class Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, data=None, files=None):
            sent.append((url.rsplit("/", 1)[-1], data["chat_id"], data.get("caption", "")))
            return Resp()
    monkeypatch.setattr(signal_tracker, "_load", load)
    monkeypatch.setattr(owner_notify, "BOT_TOKEN", "x")
    monkeypatch.setattr(owner_notify, "OWNER_CHAT_ID", "42")
    monkeypatch.setattr(httpx, "AsyncClient", Client)
    r = run(fc.send_test("EUR/USD"))
    assert r["sent"] and r["pips"] == 40.0 and r["source"] == "TP1 hit · sniper"
    assert [s[:2] for s in sent] == [("sendPhoto", "42"), ("sendDocument", "42")]
    assert sent[0][2].startswith("🧪 TEST") and "\n" in sent[0][2]
    assert run(fc.send_test("EUR/USD", "BUY", 1.10, 1.09))["reason"].startswith("not a winning")
