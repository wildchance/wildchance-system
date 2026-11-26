import asyncio
import hashlib
import hmac
import json

import pytest

from services import payments, treasury


def run(c):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(c)
    finally:
        loop.close()


@pytest.fixture
def store(monkeypatch):
    from services import data_store
    s = {}

    async def get(k):
        return s.get(k), None

    async def put(k, v):
        s[k] = v
        return True
    monkeypatch.setattr(data_store, "get", get)
    monkeypatch.setattr(data_store, "put", put)
    return s


# ── treasury ─────────────────────────────────────────────────────────────────────
def test_secrets_refused_addresses_validated():
    seed = "abandon ability able about above absent absorb abstract absurd abuse access accident"
    assert treasury.looks_secret(seed) and treasury.looks_secret("0x" + "ab" * 32) and treasury.looks_secret("xprv9s21Z")
    assert not treasury.looks_secret("TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t")
    assert treasury.chain_of("trc20", "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t") == "tron"
    assert treasury.chain_of("btc", "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq") == "btc"
    assert treasury.chain_of("eth", "0xdAC17F958D2ee523a2206206994597C13D831ec7") == "eth"
    assert treasury.chain_of("ton", "UQ" + "A" * 46) == "ton"
    assert treasury.chain_of("eth", "0x123") is None and treasury.chain_of("doge", "D8x") is None


def test_parsers():
    assert treasury.parse_mempool({"chain_stats": {"funded_txo_sum": 150_000_000, "spent_txo_sum": 50_000_000},
                                   "mempool_stats": {"funded_txo_sum": 1_000_000, "spent_txo_sum": 0}}) == {"BTC": 1.01}
    tr = treasury.parse_tron({"data": [{"balance": 2_500_000, "trc20": [{treasury.TRC20_USDT: "125000000"}]}]})
    assert tr == {"TRX": 2.5, "USDT": 125.0}
    assert treasury.parse_tron({"data": []}) == {"TRX": 0.0, "USDT": 0.0}
    ton = treasury.parse_ton({"balance": 3_000_000_000},
                             {"balances": [{"balance": "50000000", "jetton": {"symbol": "USD₮", "decimals": 6}}]})
    assert ton == {"TON": 3.0, "USDT": 50.0}
    call = treasury._erc20_call("0x" + "1" * 40, "0xtoken")
    assert call["data"].startswith("0x70a08231") and len(call["data"]) == 10 + 64


def test_moves_and_snapshot_alerts(store, monkeypatch):
    px = {"USDT": 1.0, "TRX": 0.2}
    assert treasury.moves({"USDT": 100}, {"USDT": 150, "TRX": 1}, px) == [("USDT", 50, 50.0)]   # TRX $0.2 < $1
    run(treasury.add("tron", "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t", "Tangem USDT"))
    bals = iter([{"TRX": 0.0, "USDT": 100.0}, {"TRX": 0.0, "USDT": 40.0}])

    async def balances(chain, address):
        return next(bals)

    async def prices():
        return px
    sent = []

    async def send(text):
        sent.append(text)
        return True
    monkeypatch.setattr(treasury, "balances", balances)
    monkeypatch.setattr(treasury, "prices", prices)
    monkeypatch.setattr(treasury, "kes_per_usd", lambda: 129.0)
    from services import owner_notify
    monkeypatch.setattr(owner_notify, "send", send)
    r1 = run(treasury.snapshot(alert=True))
    assert r1["total_usd"] == 100.0 and r1["total_kes"] == 12900 and not r1["alerts"]     # first look: baseline
    r2 = run(treasury.snapshot(alert=True))
    assert r2["alerts"] and "📤" in r2["alerts"][0] and "-60 USDT" in r2["alerts"][0] and sent
    assert "Treasury (watch-only) — $40.00" in treasury.text(r2)
    assert run(treasury.add("tron", "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"))["reason"] == "already watched"
    assert run(treasury.remove(1))["ok"] and run(treasury.remove(1))["ok"] is False


# ── payments ─────────────────────────────────────────────────────────────────────
def test_stars_price_and_payload(monkeypatch):
    assert payments.stars_for("trader") == 3773 and payments.stars_for("trader", 3) == 11319   # $49 × 77
    assert payments.stars_for("scout") is None
    p = payments.payload("Trader", 2, "Me@Mail.com")
    assert p == "wc|trader|2|me@mail.com" and payments.parse_payload(p) == {"tier": "trader", "months": 2,
                                                                           "email": "me@mail.com"}
    for bad in ("wc|trader|0|a@b.co", "wc|nope|1|a@b.co", "wc|trader|1|notanemail", "x|trader|1|a@b.co"):
        assert payments.parse_payload(bad) is None


def test_cryptopay_signature_and_event():
    token = "1234:AAA"
    body = json.dumps({"update_type": "invoice_paid",
                       "payload": {"invoice_id": 77, "status": "paid", "payload": "wc|pro|1|x@y.io"}}).encode()
    sig = hmac.new(hashlib.sha256(token.encode()).digest(), body, hashlib.sha256).hexdigest()
    assert payments.cryptopay_verify(body, sig, token) and not payments.cryptopay_verify(body, "00" * 32, token)
    assert payments.cryptopay_event(json.loads(body)) == {"tier": "pro", "months": 1, "email": "x@y.io", "ref": "77"}
    assert payments.cryptopay_event({"update_type": "invoice_created"}) is None


def test_apply_once_never_double_credits(store, monkeypatch):
    from services import billing_service as bs, owner_notify
    calls = []

    async def activate(db, email, tier, months, provider, ref):
        calls.append((email, tier, months, provider, ref))
        return {"ok": True, "paid_until": "2026-12-27T00:00:00"}

    async def send(t):
        return True

    class S:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *a):
            return False
    monkeypatch.setattr(bs, "activate", activate)
    monkeypatch.setattr(owner_notify, "send", send)
    import database.db as dbm
    monkeypatch.setattr(dbm, "AsyncSessionLocal", lambda: S())
    payments._lock = None
    assert run(payments.apply_once("77", "pro", 1, "x@y.io", "cryptopay"))["ok"]
    assert run(payments.apply_once("77", "pro", 1, "x@y.io", "cryptopay")) == {"ok": True, "duplicate": True}
    assert len(calls) == 1 and store["payments_seen"] == ["cryptopay:77"]


def test_cryptopay_webhook_rejects_bad_signature(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import billing
    monkeypatch.setattr(payments, "CRYPTOPAY_TOKEN", "1234:AAA")
    app = FastAPI()
    app.include_router(billing.router)
    c = TestClient(app)
    assert c.post("/billing/cryptopay/webhook", content=b"{}",
                  headers={"crypto-pay-api-signature": "bad"}).status_code == 401
    body = json.dumps({"update_type": "invoice_created"}).encode()
    sig = hmac.new(hashlib.sha256(b"1234:AAA").digest(), body, hashlib.sha256).hexdigest()
    assert c.post("/billing/cryptopay/webhook", content=body,
                  headers={"crypto-pay-api-signature": sig}).json() == {"ok": True, "ignored": True}
