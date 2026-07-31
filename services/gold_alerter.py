"""Gold setup alerter — the real-time 'catch the setup and send the card' engine.

Runs the whole stack on a schedule (or on demand) and, when a setup ARMS, pushes the
branded WILDCHANCE signal card to Telegram — so you stop polling:

  1. live price + HTF bars
  2. Optimus precision_entry across every live zone — the REJECT gate (ARMED vs WAIT),
     so it only fires on a swept-and-rejected level, never an early fill
  3. VAULTUM gold bias (free-feed macro) — the setup must ALIGN with the bias
  4. build the signal card + broadcast to Telegram, DEDUPED per zone/side so it never
     spams the same level twice within the cooldown

Boot-safe: every external call degrades; a dead feed just yields "no armed setups".
"""

from __future__ import annotations

import time
from typing import Optional, List

# in-process dedup: key -> expiry epoch (resets on restart, which is fine)
_SENT: dict = {}
_DEDUP_TTL = 3600.0        # don't re-alert the same zone/side within an hour


def _recent(key: str, ttl: float) -> bool:
    exp = _SENT.get(key)
    return bool(exp and exp > time.time())


def _mark(key: str, ttl: float):
    _SENT[key] = time.time() + ttl


async def _live_price() -> Optional[float]:
    try:
        from utils.price_fetcher import get_forex_price
        p = await get_forex_price("XAU/USD")
        if p:
            return float(p)
    except Exception:
        pass
    try:
        from services.ohlc_service import fetch_ohlc
        bars = await fetch_ohlc("XAU/USD", "1h", 2)
        if bars:
            return float(bars[-1][4])
    except Exception:
        pass
    return None


async def _vaultum_direction():
    """(direction, conviction_pct) from the live VAULTUM board — or (None, None)."""
    try:
        from routes.vaultum import _gather_scores
        from gold import vaultum_scores as vs
        sc, _ = await _gather_scores()
        b = vs.gold_bias_board(sc)
        return b.get("direction"), b.get("conviction_pct")
    except Exception:
        return None, None


def scan_setups(bars, sides=("sell", "buy")) -> List[dict]:
    """Every ARMED Optimus precision entry across the live zones (reject-gated)."""
    from gold import optimus as gop
    armed = []
    for side in sides:
        for z in gop.LIVE_ZONES.get(side, []):
            try:
                pe = gop.precision_entry(bars, z, side)
            except Exception:
                continue
            if pe.get("armed") and pe.get("target") is not None:
                armed.append(pe)
    return armed


async def scan_and_alert(notify: bool = False, min_conviction: float = 0.0,
                         require_bias_align: bool = True,
                         dedup_ttl: float = _DEDUP_TTL) -> dict:
    """Detect armed setups, align with the VAULTUM bias, and broadcast the card(s)."""
    from services.ohlc_service import fetch_ohlc
    from gold.signal_card import build_signal_card, format_card_telegram

    price = await _live_price()
    try:
        bars = await fetch_ohlc("XAU/USD", "4h", 60)
    except Exception:
        bars = None
    if not bars or len(bars) < 8:
        return {"armed": 0, "fired": [], "reason": "no HTF bars", "price": price}

    armed = scan_setups(bars)
    direction, conviction = await _vaultum_direction()

    fired = []
    for pe in armed:
        want = "long" if pe["side"] == "BUY" else "short"
        aligned = direction in (want, None, "neutral") or not direction
        if require_bias_align and not aligned:
            continue
        if conviction is not None and conviction < min_conviction:
            continue
        key = f"{pe['zone']}:{pe['side']}:{round(pe['entry'])}"
        if _recent(key, dedup_ttl):
            continue
        card = build_signal_card(
            entry=pe["entry"], stop=pe["stop"], tp=pe["target"], side=pe["side"],
            order_type="market",
            note=f"Optimus ARMED @ {pe['zone']} · VAULTUM {direction or 'n/a'}")
        card["optimus"] = {"zone": pe["zone"], "rr": pe.get("rr"),
                           "capture_pips": pe.get("capture_pips"),
                           "capture_tier": pe.get("capture_tier")}
        card["vaultum"] = {"direction": direction, "conviction_pct": conviction,
                           "aligned": aligned}
        if notify:
            try:
                from services.gold_scan import _tg
                msg = (format_card_telegram(card)
                       + f"\n🏛️ VAULTUM bias: {direction or 'n/a'}"
                       + (f" ({conviction:.0f}%)" if conviction is not None else "")
                       + ("  ✅ aligned" if aligned else "  ⚠️ counter-bias"))
                card["sent"] = await _tg(msg)
            except Exception:
                card["sent"] = False
        _mark(key, dedup_ttl)
        fired.append(card)

    return {
        "price": price, "armed": len(armed), "fired": fired,
        "vaultum": {"direction": direction, "conviction_pct": conviction},
        "note": (f"{len(fired)} card(s) broadcast" if fired
                 else f"{len(armed)} armed, none newly alerted (dedup/bias/conviction)"
                 if armed else "no armed setups — WAIT (reject gate not met)"),
    }
