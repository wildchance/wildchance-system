"""SaaS endpoints — signup, account, tiers, and the billing bridge (Stripe/M-Pesa).

Billing is a SAFE STUB: /billing/checkout returns the tier + amount to charge (wire
your Stripe/M-Pesa client to it), and /billing/webhook flips the user's tier on a
verified 'paid' event. No secrets are handled here — only the tier state machine.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Header
from sqlalchemy.ext.asyncio import AsyncSession

from database.db import get_db
from services import saas_service as svc
from saas import tiers as st
from saas.auth import current_user

router = APIRouter(prefix="/saas", tags=["saas"])


@router.get("/tiers")
async def tiers():
    """The public pricing catalogue (retail cap $499; institutional custom)."""
    return {"retail_cap_usd": st.RETAIL_CAP_USD, "tiers": st.catalog()}


@router.post("/signup")
async def signup(email: str = Query(..., description="account email"),
                 tier: str = Query("scout"), db: AsyncSession = Depends(get_db)):
    """Create an account + mint an API key (idempotent on email). Start on Scout (free)."""
    return await svc.signup(db, email, tier)


@router.get("/me")
async def me(user=Depends(current_user)):
    """Your account — tier, API key, account limit, usage."""
    return svc._to_dict(user)


@router.post("/rotate-key")
async def rotate_key(x_api_key: str = Header(None), api_key: str = Query(None),
                     db: AsyncSession = Depends(get_db)):
    out = await svc.rotate_key(db, x_api_key or api_key)
    if not out:
        raise HTTPException(status_code=401, detail="invalid API key")
    return out


@router.get("/features")
async def features(user=Depends(current_user)):
    """Which features your tier unlocks (cumulative)."""
    unlocked = [t for t in st.TIERS if t["rank"] <= st.rank_of(user.tier)]
    return {"tier": user.tier,
            "features": sorted({f for t in unlocked for f in t["features"]}),
            "account_limit": st.account_limit(user.tier)}


# --- billing bridge (Stripe / M-Pesa stub) -----------------------------------

@router.post("/billing/checkout")
async def checkout(tier: str = Query(..., description="target tier key"),
                   currency: str = Query("USD", description="USD | KES | KWD | NGN ..."),
                   user=Depends(current_user)):
    """Return what to charge for a tier (wire your Stripe/M-Pesa client to this).
    Cross-border: the amount is localised via the account-network FX table."""
    t = st.get_tier(tier)
    if not t:
        raise HTTPException(status_code=400, detail=f"unknown tier {tier}")
    if t["price_usd"] is None:
        return {"tier": tier, "checkout": "contact_sales",
                "note": "Institutional is custom-priced — contact sales"}
    amount = {"currency": "USD", "amount": t["price_usd"]}
    try:
        from gold.account_network import currency_deposits
        local = currency_deposits(t["price_usd"], [currency])["deposits"]
        if local:
            amount = {"currency": currency, "amount": local[0]["deposit"],
                      "usd": t["price_usd"]}
    except Exception:
        pass
    return {"email": user.email, "tier": tier, "charge": amount,
            "provider_hint": ("mpesa" if currency in ("KES", "TZS", "UGX") else "stripe"),
            "note": "STUB — create the provider session client-side, then POST the "
                    "verified event to /saas/billing/webhook"}


@router.post("/billing/webhook")
async def webhook(email: str = Query(...), tier: str = Query(...),
                  event: str = Query("paid", description="paid | canceled"),
                  secret: str = Query(None, description="shared webhook secret"),
                  db: AsyncSession = Depends(get_db)):
    """Flip a user's tier on a verified billing event. Guard with BILLING_WEBHOOK_SECRET."""
    from decouple import config
    expected = config("BILLING_WEBHOOK_SECRET", default=None)
    if expected and secret != expected:
        raise HTTPException(status_code=403, detail="bad webhook secret")
    if event == "canceled":
        return await svc.set_tier(db, email, "scout")
    return await svc.set_tier(db, email, tier)
