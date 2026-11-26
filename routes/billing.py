"""Member billing — Paystack (KES: M-Pesa + cards) and Stripe (USD cards).

  POST /billing/checkout?provider=paystack|stripe&tier=trader&months=1   (member API key)
       → {"url": <hosted checkout page>} — the page redirects there
  POST /billing/paystack/webhook     verified with x-paystack-signature (HMAC-SHA512)
  POST /billing/stripe/webhook       verified with Stripe-Signature (HMAC-SHA256 + 5-min window)
  GET  /billing/expire               daily: expired members → free tier (idempotent)

Env: PUBLIC_BASE_URL · PAYSTACK_SECRET_KEY · PAYSTACK_CURRENCY (KES) · PAYSTACK_FX (KES per
USD, 130) · PAYSTACK_PRICE_<TIER> · PAYSTACK_PLAN_<TIER> (recurring) · STRIPE_SECRET_KEY ·
STRIPE_WEBHOOK_SECRET · STRIPE_PRICE_<TIER> (recurring price id)
"""

from __future__ import annotations

import json

import httpx
from decouple import config
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from database.db import get_db
from saas import billing as bl
from saas.auth import current_user
from services import billing_service as bs

router = APIRouter(prefix="/billing", tags=["billing"])


def _env(k, default=None):
    return config(k, default=default)


def _base() -> str:
    return str(_env("PUBLIC_BASE_URL", "https://wildchance-system.onrender.com")).rstrip("/")


@router.post("/checkout")
async def checkout(provider: str = Query(..., pattern="^(paystack|stripe|cryptopay)$"),
                   tier: str = Query(...), months: int = Query(1, ge=1, le=12),
                   user=Depends(current_user)):
    T = tier.upper()
    if provider == "cryptopay":                       # USDT / TON / BTC via @CryptoBot
        from services import payments
        inv = await payments.cryptopay_invoice(tier, months, user.email)
        if not inv.get("ok"):
            raise HTTPException(503 if "configured" in inv["reason"] else 400, inv["reason"])
        return {"provider": "cryptopay", "url": inv["url"]}
    if provider == "paystack":
        key = _env("PAYSTACK_SECRET_KEY")
        if not key:
            raise HTTPException(503, "Paystack not configured (PAYSTACK_SECRET_KEY)")
        cur = str(_env("PAYSTACK_CURRENCY", "KES")).upper()
        amt = bl.price(tier, cur, float(_env("PAYSTACK_FX", "130")), _env(f"PAYSTACK_PRICE_{T}"))
        if not amt:
            raise HTTPException(400, f"tier {tier!r} isn't purchasable")
        body = {"email": user.email, "amount": int(round(amt * 100 * months)), "currency": cur,
                "callback_url": f"{_base()}/members?paid=1",
                "metadata": {"tier": tier, "months": months}}
        if _env(f"PAYSTACK_PLAN_{T}"):
            body["plan"] = _env(f"PAYSTACK_PLAN_{T}")
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post("https://api.paystack.co/transaction/initialize", json=body,
                             headers={"Authorization": f"Bearer {key}"})
        d = r.json()
        if not d.get("status"):
            raise HTTPException(502, f"Paystack: {d.get('message')}")
        return {"provider": "paystack", "url": d["data"]["authorization_url"]}
    key = _env("STRIPE_SECRET_KEY")
    if not key:
        raise HTTPException(503, "Stripe not configured (STRIPE_SECRET_KEY)")
    form = {"customer_email": user.email, "success_url": f"{_base()}/members?paid=1",
            "cancel_url": f"{_base()}/pricing", "metadata[tier]": tier,
            "metadata[months]": str(months), "metadata[email]": user.email}
    price_id = _env(f"STRIPE_PRICE_{T}")
    if price_id:                                       # recurring subscription
        form.update({"mode": "subscription", "line_items[0][price]": price_id,
                     "line_items[0][quantity]": "1", "subscription_data[metadata][tier]": tier,
                     "subscription_data[metadata][email]": user.email})
    else:                                              # one-off payment for N months
        amt = bl.price(tier, "USD")
        if not amt:
            raise HTTPException(400, f"tier {tier!r} isn't purchasable")
        form.update({"mode": "payment", "line_items[0][quantity]": "1",
                     "line_items[0][price_data][currency]": "usd",
                     "line_items[0][price_data][unit_amount]": str(int(amt * 100 * months)),
                     "line_items[0][price_data][product_data][name]":
                         f"WildChance {tier.title()} — {months} month(s)"})
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post("https://api.stripe.com/v1/checkout/sessions", data=form, auth=(key, ""))
    d = r.json()
    if r.status_code != 200:
        raise HTTPException(502, f"Stripe: {(d.get('error') or {}).get('message')}")
    return {"provider": "stripe", "url": d["url"]}


async def _apply(ev, provider: str, db: AsyncSession):
    if not ev:
        return {"ok": True, "ignored": True}
    if ev["action"] == "activate":
        return await bs.activate(db, ev["email"], ev.get("tier") or "", ev.get("months", 1),
                                 provider, ev.get("ref"), ev.get("customer"))
    if ev["action"] == "cancel_now":
        return await bs.cancel_now(db, ev.get("email") or None, ev.get("customer"))
    return {"ok": True, "noted": ev["action"]}          # stop_renewal: runs to paid_until


@router.post("/paystack/webhook")
async def paystack_webhook(request: Request, x_paystack_signature: str = Header(None),
                           db: AsyncSession = Depends(get_db)):
    body = await request.body()
    if not bl.verify_paystack(body, x_paystack_signature, _env("PAYSTACK_SECRET_KEY")):
        raise HTTPException(401, "bad signature")
    return await _apply(bl.paystack_event(json.loads(body)), "paystack", db)


@router.post("/stripe/webhook")
async def stripe_webhook(request: Request, stripe_signature: str = Header(None),
                         db: AsyncSession = Depends(get_db)):
    body = await request.body()
    if not bl.verify_stripe(body, stripe_signature, _env("STRIPE_WEBHOOK_SECRET")):
        raise HTTPException(401, "bad signature")
    return await _apply(bl.stripe_event(json.loads(body)), "stripe", db)


@router.post("/cryptopay/webhook")
async def cryptopay_webhook(request: Request, crypto_pay_api_signature: str = Header(None)):
    """@CryptoBot invoice_paid → activation (signature verified; each invoice applied once)."""
    from services import payments
    body = await request.body()
    if not payments.cryptopay_verify(body, crypto_pay_api_signature):
        raise HTTPException(401, "bad signature")
    ev = payments.cryptopay_event(json.loads(body or b"{}"))
    if not ev:
        return {"ok": True, "ignored": True}
    return await payments.apply_once(ev["ref"], ev["tier"], ev["months"], ev["email"], "cryptopay")


@router.get("/expire")
async def expire(db: AsyncSession = Depends(get_db)):
    return await bs.expire_due(db)
