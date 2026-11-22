"""Payment-provider maths and signature checks (pure — no I/O).

Paystack  (KES: M-Pesa + cards)   webhook header x-paystack-signature = HMAC-SHA512(body, secret key)
Stripe    (USD cards, global)     webhook header Stripe-Signature: t=…,v1=HMAC-SHA256("t.body", whsec)
Never trust a webhook without these checks — the body alone can be forged.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Dict, Optional

from saas import tiers as st


def verify_paystack(body: bytes, signature: Optional[str], secret: Optional[str]) -> bool:
    if not (signature and secret):
        return False
    mac = hmac.new(secret.encode(), body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(mac, signature)


def verify_stripe(body: bytes, header: Optional[str], secret: Optional[str],
                  tolerance: int = 300, now: Optional[float] = None) -> bool:
    if not (header and secret):
        return False
    parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
    ts, sig = parts.get("t"), parts.get("v1")
    if not (ts and sig) or not ts.isdigit():
        return False
    if abs((now or time.time()) - int(ts)) > tolerance:      # replay protection
        return False
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(mac, sig)


def price(tier: str, currency: str, fx: float = 1.0, override: Optional[float] = None) -> Optional[float]:
    """Price in MAJOR units for the currency (override wins, else USD price × fx)."""
    t = st.get_tier(tier)
    if not t or t.get("price_usd") in (None, 0):
        return None
    if override:
        return float(override)
    return round(float(t["price_usd"]) * (fx if currency.upper() != "USD" else 1.0), 2)


def paystack_event(evt: Dict) -> Optional[Dict]:
    """charge.success → activation; subscription.disable → end at period end."""
    data = evt.get("data") or {}
    email = ((data.get("customer") or {}).get("email") or "").lower()
    meta = data.get("metadata") or {}
    if evt.get("event") == "charge.success" and email:
        return {"action": "activate", "email": email, "tier": meta.get("tier"),
                "months": int(meta.get("months") or 1), "ref": data.get("reference")}
    if evt.get("event") in ("subscription.disable", "subscription.not_renew") and email:
        return {"action": "stop_renewal", "email": email, "ref": data.get("subscription_code")}
    return None


def stripe_event(evt: Dict) -> Optional[Dict]:
    obj = ((evt.get("data") or {}).get("object")) or {}
    meta = obj.get("metadata") or {}
    typ = evt.get("type")
    if typ == "checkout.session.completed":
        email = ((obj.get("customer_details") or {}).get("email") or obj.get("customer_email") or "").lower()
        if email:
            return {"action": "activate", "email": email, "tier": meta.get("tier"),
                    "months": int(meta.get("months") or 1), "ref": obj.get("id"),
                    "customer": obj.get("customer")}
    if typ == "invoice.paid" and obj.get("billing_reason") == "subscription_cycle":
        email = (obj.get("customer_email") or "").lower()
        lines = ((obj.get("lines") or {}).get("data") or [{}])
        tier = ((lines[0].get("metadata") or {}).get("tier")) or meta.get("tier")
        if email:
            return {"action": "activate", "email": email, "tier": tier, "months": 1,
                    "ref": obj.get("id")}
    if typ == "customer.subscription.deleted":
        return {"action": "cancel_now", "customer": obj.get("customer"),
                "email": (meta.get("email") or "").lower(), "ref": obj.get("id")}
    return None
