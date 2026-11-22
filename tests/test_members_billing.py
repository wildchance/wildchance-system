"""Members website: payment signatures, activation/expiry, webhooks, feed capture, pages."""

import asyncio
import datetime as dt
import hashlib
import hmac
import json
import time

import pytest

from saas import billing as bl


def _paystack_sig(body: bytes, secret="sk_test"):
    return hmac.new(secret.encode(), body, hashlib.sha512).hexdigest()


def _stripe_header(body: bytes, secret="whsec_x", ts=None):
    ts = int(ts or time.time())
    return f"t={ts},v1=" + hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()


def test_signatures():
    b = b'{"event":"charge.success"}'
    assert bl.verify_paystack(b, _paystack_sig(b), "sk_test")
    assert not bl.verify_paystack(b + b" ", _paystack_sig(b), "sk_test")     # tampered
    assert not bl.verify_paystack(b, _paystack_sig(b), None)                  # unconfigured
    h = _stripe_header(b)
    assert bl.verify_stripe(b, h, "whsec_x")
    assert not bl.verify_stripe(b, h, "whsec_other")
    assert not bl.verify_stripe(b, _stripe_header(b, ts=time.time() - 3600), "whsec_x")  # replay


def test_event_parsing_and_price():
    ev = {"event": "charge.success", "data": {"customer": {"email": "A@B.co"}, "reference": "r1",
                                              "metadata": {"tier": "trader", "months": 2}}}
    assert bl.paystack_event(ev) == {"action": "activate", "email": "a@b.co", "tier": "trader",
                                     "months": 2, "ref": "r1"}
    s = {"type": "checkout.session.completed", "data": {"object": {
        "id": "cs_1", "customer": "cus_1", "customer_details": {"email": "x@y.z"},
        "metadata": {"tier": "pro", "months": "1"}}}}
    assert bl.stripe_event(s)["tier"] == "pro" and bl.stripe_event(s)["customer"] == "cus_1"
    assert bl.stripe_event({"type": "ping"}) is None
    assert bl.price("trader", "KES", 130) == 49 * 130 and bl.price("trader", "USD") == 49
    assert bl.price("scout", "USD") is None and bl.price("trader", "KES", 130, "5000") == 5000.0


@pytest.fixture
def client(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker
    import database.db as dbm
    import models.user_model, models.signal_feed_model  # noqa: F401
    from main import app
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'm.db'}")
    Session = sessionmaker(bind=eng, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(dbm, "AsyncSessionLocal", Session)

    async def _db():
        async with Session() as s:
            yield s
    app.dependency_overrides[dbm.get_db] = _db

    async def mk():
        async with eng.begin() as c:
            await c.run_sync(dbm.Base.metadata.create_all)
    loop = asyncio.new_event_loop(); loop.run_until_complete(mk()); loop.close()
    import routes.billing as rb
    env = {"PAYSTACK_SECRET_KEY": "sk_test", "STRIPE_WEBHOOK_SECRET": "whsec_x"}
    monkeypatch.setattr(rb, "config", lambda k, default=None: env.get(k, default))
    yield TestClient(app), Session
    app.dependency_overrides.clear()


def test_paystack_webhook_activates_and_stacks(client):
    c, Session = client
    key = c.post("/saas/signup?email=m@x.co").json()["api_key"]
    body = json.dumps({"event": "charge.success", "data": {"customer": {"email": "m@x.co"},
                       "reference": "r1", "metadata": {"tier": "trader", "months": 1}}}).encode()
    assert c.post("/billing/paystack/webhook", content=body,
                  headers={"x-paystack-signature": "forged"}).status_code == 401
    r = c.post("/billing/paystack/webhook", content=body,
               headers={"x-paystack-signature": _paystack_sig(body)}).json()
    assert r["ok"] and r["tier"] == "trader"
    first = dt.datetime.fromisoformat(r["paid_until"])
    r2 = c.post("/billing/paystack/webhook", content=body,
                headers={"x-paystack-signature": _paystack_sig(body)}).json()
    assert (dt.datetime.fromisoformat(r2["paid_until"]) - first).days == 31   # stacks
    me = c.get("/members/me", headers={"X-API-Key": key}).json()
    assert me["paid"] and me["tier"] == "trader"


def test_stripe_cancel_and_expiry(client):
    c, Session = client
    body = json.dumps({"type": "checkout.session.completed", "data": {"object": {
        "id": "cs_1", "customer": "cus_9", "customer_details": {"email": "s@x.co"},
        "metadata": {"tier": "pro"}}}}).encode()
    assert c.post("/billing/stripe/webhook", content=body,
                  headers={"stripe-signature": _stripe_header(body)}).json()["tier"] == "pro"
    cancel = json.dumps({"type": "customer.subscription.deleted",
                         "data": {"object": {"id": "sub_1", "customer": "cus_9"}}}).encode()
    assert c.post("/billing/stripe/webhook", content=cancel,
                  headers={"stripe-signature": _stripe_header(cancel)}).json()["tier"] == "scout"
    from services import billing_service as bs
    from models.user_model import User

    async def past_due():
        async with Session() as db:
            await bs.activate(db, "e@x.co", "trader")
            u = (await db.execute(__import__("sqlalchemy").select(User).where(User.email == "e@x.co"))).scalar_one()
            u.paid_until = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)
            await db.commit()
    loop = asyncio.new_event_loop(); loop.run_until_complete(past_due()); loop.close()
    assert c.get("/billing/expire").json()["expired"] == 1


def test_free_member_sees_teaser_only(client):
    c, Session = client
    key = c.post("/saas/signup?email=f@x.co").json()["api_key"]
    from services import signal_feed as sf
    loop = asyncio.new_event_loop()
    loop.run_until_complete(sf.record("⚡ Z-setup — EUR/USD SELL\nEntry 1.0991 Stop 1.1001 TP1 1.0975"))
    loop.run_until_complete(sf.record("⚠️ WildChance outage: 9/10 failed"))      # system: skipped
    loop.close()
    f = c.get("/members/feed", headers={"X-API-Key": key}).json()
    assert len(f["signals"]) == 1 and f["signals"][0]["kind"] == "z-setup"
    assert "upgrade" in f["signals"][0]["text"]
    assert c.get("/members/me", headers={"X-API-Key": key}).json()["telegram_invite"] is None


def test_feed_capture_helpers():
    from services import signal_feed as sf
    assert sf.extract_text(b'{"chat_id": -100123, "text": "hi"}', channel="-100123") == "hi"
    assert sf.extract_text(b'{"chat_id": 555, "text": "your /history"}', channel="-100123") == ""
    assert sf.extract_text(b'{"chat_id": 1, "text": "x"}', channel="") == ""
    assert sf.extract_text(b'[1,2]', channel="1") == ""
    assert sf.kind_of("🎯 Swing fade — XAUUSD") == "swing" and sf.kind_of("📦 CBDR — box") == "cbdr"


def test_pages_served():
    from fastapi.testclient import TestClient
    from main import app
    c = TestClient(app)
    for p, needle in (("/", "Discipline today"), ("/pricing", "Choose your"),
                      ("/members", "Member login")):
        r = c.get(p)
        assert r.status_code == 200 and needle in r.text and "Risk warning" in r.text
    assert c.get("/status").json()["status"] == "Server running"
    assert c.post("/billing/checkout?provider=paystack&tier=trader").status_code == 401


def test_signup_rejects_html_emails():
    from fastapi.testclient import TestClient
    from main import app
    c = TestClient(app)
    assert c.post('/saas/signup?email=<img src=x onerror=alert(1)>@x.co').status_code == 422
    assert c.post('/saas/signup?email=notanemail').status_code == 422
