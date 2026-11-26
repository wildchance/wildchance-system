import asyncio

from services import recon_service as rs


def run(c):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(c)
    finally:
        loop.close()


def test_drop_longs():
    orders = [{"side": "long", "level": "-1SD"}, {"side": "short", "level": "+1SD"},
              {"side": "long", "level": "+1SD_stop"}, {"side": "short", "level": "+3SD"}]
    keep, n = rs.drop_longs(orders)
    assert n == 2 and [o["level"] for o in keep] == ["+1SD", "+3SD"]


def test_lock_only_for_gold_and_unknown_counts_locked(monkeypatch):
    async def unlocked():
        return {"gold_longs": "unlocked"}

    async def boom():
        raise RuntimeError("no dxy")
    assert run(rs.gold_longs_locked("EUR/USD")) is False
    monkeypatch.setattr(rs, "gold_longs_lock", unlocked)
    assert run(rs.gold_longs_locked("XAU/USD")) is False
    monkeypatch.setattr(rs, "gold_longs_lock", boom)
    assert run(rs.gold_longs_locked("XAUUSD")) is True


def test_prelondon_card_sells_only_while_locked(monkeypatch):
    from routes import cbdr
    sent = []

    async def build(symbol, window, price):
        return {"box": {"high": 3600.0, "low": 3550.0}, "window_label": "pre-London", "session": "2026-11-26"}

    async def locked(symbol="XAU/USD"):
        return True

    async def send(text):
        sent.append(text)
        return True
    monkeypatch.setattr(cbdr, "_build", build)
    monkeypatch.setattr(rs, "gold_longs_locked", locked)
    monkeypatch.setattr(cbdr, "_send", send)
    r = run(cbdr.cbdr_limits("XAU/USD", notify=True))
    assert r["longs_locked"] and r["hidden_buys"] >= 1
    assert all(o["side"] != "long" for o in r["orders"])
    assert "🟢 BUY" not in sent[0] and "🔴 SELL" in sent[0] and "DXY locks gold longs" in sent[0]
