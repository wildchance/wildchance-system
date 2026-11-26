"""Subscription payments inside Telegram: Telegram Stars (XTR) and Crypto Pay (@CryptoBot).

Both end in services.billing_service.activate(email, tier, months, provider, ref) — the
same activation Paystack / Stripe use — and every payment reference is applied ONCE
(Crypto Pay retries webhooks; a replay must never add a second month).

Telegram's rule: digital goods sold inside a bot are paid in Stars, so /subscribe in the
bot charges Stars. Crypto Pay (USDT / TON / BTC) is offered from the website checkout
(POST /billing/checkout?provider=cryptopay) and, when enabled, as a link in the bot.

Env:
  STARS_PER_USD (77 — Stars charged per USD of the tier price) · STARS_PRICE_<TIER> (override)
  CRYPTOPAY_TOKEN (from @CryptoBot → Crypto Pay → Create App) · CRYPTOPAY_TESTNET (false)
  CRYPTOPAY_ASSETS (USDT,TON,BTC) · CRYPTOPAY_IN_BOT (false — show the crypto link in the bot)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
from typing import Dict, Optional

from decouple import config

STARS_PER_USD = float(config("STARS_PER_USD", default="77"))
CRYPTOPAY_TOKEN = config("CRYPTOPAY_TOKEN", default=None)
CRYPTOPAY_TESTNET = str(config("CRYPTOPAY_TESTNET", default="false")).lower() in ("1", "true", "yes", "on")
CRYPTOPAY_ASSETS = str(config("CRYPTOPAY_ASSETS", default="USDT,TON,BTC"))
CRYPTOPAY_IN_BOT = str(config("CRYPTOPAY_IN_BOT", default="false")).lower() in ("1", "true", "yes", "on")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
SEEN_KEY = "payments_seen"
_lock = None


def tier_usd(tier: str) -> Optional[float]:
    from saas import billing as bl
    return bl.price(tier, "USD")


def stars_for(tier: str, months: int = 1) -> Optional[int]:
    over = config(f"STARS_PRICE_{tier.upper()}", default=None)
    if over:
        return int(float(over)) * max(1, months)
    usd = tier_usd(tier)
    return int(math.ceil(usd * STARS_PER_USD * max(1, months))) if usd else None


def payload(tier: str, months: int, email: str) -> str:
    return f"wc|{tier.lower()}|{int(months)}|{email.lower()}"[:128]


def parse_payload(p: str) -> Optional[Dict]:
    parts = str(p or "").split("|")
    if len(parts) != 4 or parts[0] != "wc":
        return None
    try:
        months = int(parts[2])
    except ValueError:
        return None
    if not (1 <= months <= 12) or not EMAIL.match(parts[3]) or not tier_usd(parts[1]):
        return None
    return {"tier": parts[1], "months": months, "email": parts[3]}


async def apply_once(ref: str, tier: str, months: int, email: str, provider: str) -> Dict:
    """Activate exactly once per payment reference (serialised: retried webhooks may overlap)."""
    import asyncio
    global _lock
    _lock = _lock or asyncio.Lock()
    async with _lock:
        return await _apply(ref, tier, months, email, provider)


async def _apply(ref: str, tier: str, months: int, email: str, provider: str) -> Dict:
    from services import data_store, billing_service as bs
    from database.db import AsyncSessionLocal
    seen, _ = await data_store.get(SEEN_KEY)
    seen = list(seen or [])
    key = f"{provider}:{ref}"
    if key in seen:
        return {"ok": True, "duplicate": True}
    async with AsyncSessionLocal() as db:
        r = await bs.activate(db, email, tier, months, provider, ref)
    if r.get("ok"):
        await data_store.put(SEEN_KEY, (seen + [key])[-5000:])
        try:
            from services.owner_notify import send
            usd = (tier_usd(tier) or 0) * months
            await send(f"💳 New payment ({provider}): {email} → {tier} × {months} mo (≈ ${usd:,.0f}) · "
                       f"paid until {r.get('paid_until', '')[:10]}")
        except Exception:
            pass
    return r


# ── Crypto Pay ───────────────────────────────────────────────────────────────────
def cryptopay_base() -> str:
    return "https://testnet-pay.crypt.bot/api" if CRYPTOPAY_TESTNET else "https://pay.crypt.bot/api"


def cryptopay_verify(body: bytes, signature: Optional[str], token: Optional[str] = None) -> bool:
    """crypto-pay-api-signature = HMAC-SHA256(body) keyed with SHA256(app token)."""
    token = token or CRYPTOPAY_TOKEN
    if not token or not signature:
        return False
    secret = hashlib.sha256(token.encode()).digest()
    return hmac.compare_digest(hmac.new(secret, body, hashlib.sha256).hexdigest(), signature)


def cryptopay_event(update: Dict) -> Optional[Dict]:
    """invoice_paid → {ref, tier, months, email}; anything else → None."""
    if update.get("update_type") != "invoice_paid":
        return None
    inv = update.get("payload") or {}
    if str(inv.get("status")) != "paid":
        return None
    p = parse_payload(inv.get("payload"))
    return {**p, "ref": str(inv.get("invoice_id"))} if p else None


async def cryptopay_invoice(tier: str, months: int, email: str) -> Dict:
    if not CRYPTOPAY_TOKEN:
        return {"ok": False, "reason": "Crypto Pay not configured (CRYPTOPAY_TOKEN)"}
    usd = tier_usd(tier)
    if not usd or not EMAIL.match(email or ""):
        return {"ok": False, "reason": "unknown tier or bad email"}
    import httpx
    body = {"currency_type": "fiat", "fiat": "USD", "amount": f"{usd * months:.2f}",
            "accepted_assets": CRYPTOPAY_ASSETS, "payload": payload(tier, months, email),
            "description": f"WildChance {tier.title()} — {months} month(s) for {email}",
            "expires_in": 3600, "allow_comments": False}
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(f"{cryptopay_base()}/createInvoice", json=body,
                         headers={"Crypto-Pay-API-Token": CRYPTOPAY_TOKEN})
    d = r.json() if r.content else {}
    if not d.get("ok"):
        return {"ok": False, "reason": f"Crypto Pay: {(d.get('error') or {}).get('name') or r.status_code}"}
    res = d["result"]
    return {"ok": True, "invoice_id": res.get("invoice_id"),
            "url": res.get("bot_invoice_url") or res.get("mini_app_invoice_url") or res.get("pay_url")}


def plans_text() -> str:
    from saas import tiers as st
    lines = ["💳 WildChance plans (1 month):"]
    for t in st.TIERS:
        if t.get("price_usd"):
            lines.append(f"• {t['name']} — ${t['price_usd']} · ⭐ {stars_for(t['key'])} Stars")
    lines.append("Subscribe: /subscribe trader you@email.com [months]")
    lines.append("Your account and keys are then on the members page under that email.")
    return "\n".join(lines)
