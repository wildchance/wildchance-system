import asyncio

from services import council_gate, dxy_service, gold_positions as gp, recon_service


def run(c):
    return asyncio.run(c)


def test_dxy_rejects_non_index_tickers(monkeypatch):
    async def series(sym, interval="1h", outputsize=60):
        return [25.6, 25.69]                                    # a $25 ETF/stock, not the index
    async def pool(sym, interval, n, timezone="UTC"):
        return {"values": [{"close": 103.63}, {"close": 103.5}], "source": "pool:yahoo_intraday"}
    monkeypatch.setattr(dxy_service, "_series", series)
    import services.price_pool as pp
    monkeypatch.setattr(pp, "alt_values", pool)
    closes, src = run(dxy_service.fetch_dxy())
    assert closes[-1] == 103.63 and src == "pool:yahoo_intraday"
    assert not dxy_service.is_index_level([25.69]) and dxy_service.is_index_level([103.6])


def test_optimus_buy_blocked_when_longs_locked_and_deduped(monkeypatch):
    calls = []

    async def check(sym, side, *a, **k):
        calls.append(side)
        return {"pass": side == "SELL", "verdict": "GO" if side == "SELL" else "WAIT"}
    monkeypatch.setattr(council_gate, "check", check)
    armed = [{"side": "BUY", "zone": "buy3534"}, {"side": "BUY", "zone": "buy3502"},
             {"side": "SELL", "zone": "sell4035"}]
    ok, blocked = run(recon_service.gate_optimus(armed, {"dxy": {"gold_longs": "locked"}}))
    assert [r["zone"] for r in ok] == ["sell4035"]
    assert blocked == [{"side": "BUY", "zone": "buy3534", "why": "gold longs LOCKED by DXY"}]
    assert calls == ["SELL"]                                    # a locked side never reaches the council
    ok, blocked = run(recon_service.gate_optimus(armed[:1], {"dxy": {"gold_longs": "unlocked"}}))
    assert ok == [] and blocked[0]["why"] == "council WAIT"


def test_gold_position_needs_council(monkeypatch):
    async def check(sym, side, *a, **k):
        return {"pass": False, "verdict": "WAIT"}
    monkeypatch.setattr(council_gate, "check", check)
    monkeypatch.setattr(gp, "COUNCIL_GATE", True)
    sig = {"signal": "LONG", "gate": {"allow": True}, "entry": 3547.5, "stop": 3442.8, "trade_type": "crt"}
    assert run(gp.open_from_signal(None, dict(sig), source="stratops_paper")) == {"skipped": "council WAIT"}
    assert run(gp.open_limit(None, dict(sig), "gold_limit")) == {"skipped": "council WAIT"}
    assert run(gp.council_block({**sig, "council_pass": True})) is None   # gated upstream (CRT route)
