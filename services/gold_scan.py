"""Real-time gold scan — the 'hands' that fire XAU/USD signals to Telegram.

Chains the pieces built for gold:
  1. daily OHLC → ICT weekly profile (direction + justification)
  2. live price → entry; fixed-pip SL money-sized to $ risk; RR take-profits
  3. wildchance gold confluence (retail + COT verdict) as extra justification
  4. prop-firm gate
  5. Telegram card

Boot-safe: no signal (not an error) when the profile is neutral / Seek & Destroy,
or when data is unavailable.
"""

from __future__ import annotations

import httpx
from decouple import config

from services.ohlc_service import fetch_ohlc
from services.wildchance_service import get_latest_feed
from utils.price_fetcher import get_forex_price
from gold.ict import classify_week, confluence as ict_confluence
from gold.signal import assemble, format_card
from gold import macro as gmacro
from gold import macro_cycle as gcycle
from gold.location import location_gate

BOT_TOKEN = config("TELEGRAM_BOT_TOKEN", default=None) or config("BOT_TOKEN", default=None)
CHAT_ID = config("TELEGRAM_CHAT_ID", default=None)


async def _tg(text: str) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(url, json={"chat_id": CHAT_ID, "text": text,
                                    "parse_mode": "Markdown"})
        return True
    except Exception:
        return False


async def _wildchance_gold_side() -> str:
    """XAU/USD verdict from the stored wildchance feed → 'long'/'short'/'neutral'."""
    try:
        feed = await get_latest_feed()
        for s in (feed or {}).get("signals", []):
            if (s.get("pair") or "").upper().replace("-", "/") == "XAU/USD":
                v = s.get("verdict")
                return "long" if v == "LONG" else "short" if v == "SHORT" else "neutral"
    except Exception:
        pass
    return "neutral"


async def scan(balance: float = 5000.0, tier: str = "6", risk_usd: float = 20.0,
               sl_pips: float = 200.0, require_confluence: bool = False,
               require_macro: bool = True, require_location: bool = True,
               require_regime: bool = True,
               loc_deep: float = 0.5, notify: bool = False) -> dict:
    """Build (and optionally send) the current gold signal."""
    daily = await fetch_ohlc("XAU/USD", "1day", 25)
    if len(daily) < 3:
        return {"signal": "NO TRADE", "reason": "no XAU/USD daily bars"}

    profile = classify_week(daily)
    entry = None
    try:
        entry = await get_forex_price("XAU/USD")
    except Exception:
        entry = None
    if entry is None:
        entry = daily[-1][4]                       # fall back to last close

    sig = assemble(profile, entry, balance, tier=tier, risk_usd=risk_usd, sl_pips=sl_pips)

    bias = (profile or {}).get("bias")

    # --- MACRO ANCHOR — align with the standing macro bias, respect invalidation.
    if sig.get("signal") in ("LONG", "SHORT"):
        macro = gmacro.macro_confluence(bias, entry)
        sig["macro"] = macro
        if require_macro and macro["invalidated"]:
            sig["signal"] = "NO TRADE"
            sig["suppressed"] = macro["note"]
            return sig
        if require_macro and macro["status"] == "diverges":
            sig["signal"] = "NO TRADE"
            sig["suppressed"] = (f"weekly {bias} diverges from macro {macro['bias']} "
                                 "anchor — stand aside (no mid-week flip)")
            return sig

    # --- LOCATION — buy discount / sell premium (today's range), never chase.
    if sig.get("signal") in ("LONG", "SHORT") and require_location:
        d_high, d_low = daily[-1][2], daily[-1][3]
        loc = location_gate(bias, entry, d_high, d_low, deep=loc_deep)
        sig["location"] = loc
        if not loc["ok"]:
            sig["signal"] = "NO TRADE"
            sig["suppressed"] = loc["reason"]
            return sig

    # --- REGIME — dollar (DXY inverse) + COT positioning confluence.
    if sig.get("signal") in ("LONG", "SHORT"):
        reg = gcycle.regime_gate(bias)     # DXY structure, not the gold price
        sig["regime"] = reg
        if require_regime and not reg["ok"]:
            sig["signal"] = "NO TRADE"
            sig["suppressed"] = reg["reason"]
            return sig

    # Wildchance gold confluence (retail + COT) — justification, and optional gate.
    wc_side = await _wildchance_gold_side()
    sig["wildchance_side"] = wc_side
    sig["wildchance_confluence"] = ict_confluence({"bias": wc_side}, sig.get("signal", "")) \
        if sig.get("signal") in ("LONG", "SHORT") else "neutral"

    if sig.get("signal") == "NO TRADE":
        return sig
    if require_confluence and sig["wildchance_confluence"] == "diverges":
        sig["suppressed"] = "wildchance retail+COT opposes the profile"
        return sig

    if notify:
        sig["sent"] = await _tg(format_card(sig))
    return sig
