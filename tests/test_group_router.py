import asyncio
import json

from services import group_router as gr

TABLE = {"gold": ("-100", 12), "fx": ("-100", 15), "crypto": ("-200", None), "indices": ("-100", 20),
         "general": ("-100", 3), "results": ("-300", None)}


def test_families():
    assert gr.families("🔴 XAU/USD SELL limit 3616") == {"gold"}
    assert gr.families("Wildchance Signal — EUR/USD SELL") == {"fx"}
    assert gr.families("BTC/USD BUY 60046") == {"crypto"}
    assert gr.families("NAS100 walls 28,000") == {"indices"}
    assert gr.families("Currency strength USD > JPY > CAD") == set()
    assert gr.families("EUR/USD and XAU/USD") == {"fx", "gold"}


def test_destinations():
    assert gr.destinations("XAU/USD SELL", False, TABLE) == [("-100", 12)]
    assert gr.destinations("EUR/USD and XAU/USD digest", False, TABLE) == [("-100", 3)]          # mixed → general
    assert gr.destinations("✅ EUR/USD SELL +24.4 pips", True, TABLE) == [("-100", 15), ("-300", None)]
    assert gr.destinations("anything", False, {}) == []


def test_routes_env(monkeypatch):
    monkeypatch.setattr(gr, "config", lambda k, default=None: json.dumps({"gold": "-1001:12", "fx": "-1002", "x": ""}))
    assert gr.routes() == {"gold": ("-1001", 12), "fx": ("-1002", None)}


def test_mirror_copies_main_channel_posts(monkeypatch):
    import httpx
    from services import telegram_service
    monkeypatch.setattr(telegram_service, "CHAT_ID", "-999")
    monkeypatch.setattr(gr, "routes", lambda: TABLE)
    copies = []

    async def fake_copy(method, body, dests, base):
        copies.append((method, body.get("text") or body.get("photo"), dests))
    monkeypatch.setattr(gr, "_copy", fake_copy)

    def handler(req):
        body = json.loads(req.content)
        return httpx.Response(200, json={"ok": True, "result": {"chat": {"id": int(body["chat_id"])},
                                                                 "text": body["text"]}})
    orig = httpx.AsyncClient.send
    we_installed = not gr._installed["done"]
    try:
        gr.install()                                  # no-op when the app already installed it

        async def go():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
                await c.post("https://api.telegram.org/botX/sendMessage", json={"chat_id": "-999", "text": "XAU/USD SELL"})
                await c.post("https://api.telegram.org/botX/sendMessage", json={"chat_id": "123", "text": "XAU/USD owner"})
            await asyncio.sleep(0.05)
        asyncio.new_event_loop().run_until_complete(go())
    finally:
        if we_installed:
            httpx.AsyncClient.send = orig
            gr._installed["done"] = False
    assert copies == [("sendMessage", "XAU/USD SELL", [("-100", 12)])]       # only the main channel is mirrored
