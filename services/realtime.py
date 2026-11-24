"""Real-time evaluation — runs when the EA pushes a closed bar (no cron latency).

  H4 / D1 bar  → HTF Bollinger swing scan for that symbol → Telegram on ENTRY/LIMIT
  M15 / H1 bar → CBDR extension check: price ≥ 2.5 SD beyond the box AND the HTF
                 Bollinger is stretched the same way → "exhaustion" Telegram card

Every alert is de-duplicated (same key → one message per ALERT_TTL_HOURS), so a
bar re-sent by the EA never spams. Disable with REALTIME_ALERTS_ENABLED=false.
"""

from __future__ import annotations

import time
from typing import Dict, Optional

from decouple import config

REALTIME_ENABLED = str(config("REALTIME_ALERTS_ENABLED", default="true")).strip().lower() in (
    "1", "true", "yes", "on")
ALERT_TTL_HOURS = float(config("ALERT_TTL_HOURS", default="12"))

_sent: Dict[str, float] = {}
_htf_cache: Dict[str, Dict] = {}          # last swing read per symbol (for the CBDR check)


async def alert_once(key: str, text: str, ttl_hours: Optional[float] = None) -> bool:
    """Send ``text`` to Telegram unless ``key`` was sent within the TTL. Returns sent?"""
    now = time.time()
    ttl = (ttl_hours if ttl_hours is not None else ALERT_TTL_HOURS) * 3600
    for k in [k for k, t in _sent.items() if now - t > ttl]:
        del _sent[k]
    if key in _sent:
        return False
    _sent[key] = now
    ok = None
    try:
        from services.telegram_service import send_telegram_message
        ok = await send_telegram_message(text)
    except Exception:
        ok = False
    if ok is False:                       # not delivered → forget it so the next tick retries
        _sent.pop(key, None)
        return False
    return True


def td_symbol(canon_symbol: str) -> str:
    from services.swing_service import DISPLAY
    rev = {v: k for k, v in DISPLAY.items()}
    if canon_symbol in rev:
        return rev[canon_symbol]
    if len(canon_symbol) == 6 and canon_symbol.isalpha():
        return f"{canon_symbol[:3]}/{canon_symbol[3:]}"
    return canon_symbol


async def on_bars(symbol: str, timeframe: str) -> Dict:
    """Hook called after /bridge/bars ingests. Never raises."""
    if not REALTIME_ENABLED:
        return {"skipped": "REALTIME_ALERTS_ENABLED=false"}
    from services import swing_service as sw
    sym, tf = symbol.upper(), timeframe.upper()
    out: Dict = {"symbol": sym, "timeframe": tf}
    from services.trade_gate import session_block
    held = session_block()
    if held:                                  # owner's rules: no entries now
        out["held"] = held
        return out
    try:
        if tf in ("H4", "D1", "W1"):
            r = await sw.analyze(td_symbol(sym))
            _htf_cache[sym] = r
            out["swing"] = r.get("status")
            if r.get("status") in ("ENTRY", "LIMIT"):
                day = time.strftime("%Y-%m-%d", time.gmtime())
                out["alerted"] = await alert_once(f"swing:{sym}:{r['side']}:{day}",
                                                  sw.format_alert(r))
        elif tf in ("M15", "H1"):
            out.update(await cbdr_exhaustion(sym))
    except Exception as e:                       # the ingest must never fail on analysis
        out["error"] = f"{type(e).__name__}: {e}"[:200]
    return out


async def cbdr_exhaustion(sym: str) -> Dict:
    from cbdr.engine import build_cbdr, extension_read
    from services.broker_feed import td_values
    from services.cbdr_service import fetch_cbdr_window
    box = await fetch_cbdr_window(td_symbol(sym), "cbdr")
    last = await td_values(td_symbol(sym), "15min", 1) or await td_values(td_symbol(sym), "1h", 1)
    if not box or not last:
        return {"cbdr": "no box/price"}
    price = float(last["values"][0]["close"])
    c = build_cbdr(box["high"], box["low"])
    ext = extension_read(price, c)
    res = {"cbdr": ext["note"], "price": price}
    if not ext["extreme"]:
        return res
    htf = _htf_cache.get(sym)
    if htf is None:
        from services import swing_service as sw
        htf = await sw.analyze(td_symbol(sym), with_cbdr=False)
        _htf_cache[sym] = htf
    want = "SELL" if ext["side"] == "up" else "BUY"
    zones = htf.get("zones") or {}
    stretched = [tf for tf, z in zones.items()
                 if z.endswith("HIGH" if want == "SELL" else "LOW") and z != "CORE"]
    res["htf_stretched"] = stretched
    if stretched:
        text = (f"⚡ *CBDR exhaustion — {sym}* {'🔻 SELL' if want == 'SELL' else '🔺 BUY'} watch\n"
                f"Price {price} reached {ext['reached_sd']:g}SD beyond the CBDR box "
                f"({box['low']}–{box['high']}, {box['session']})\n"
                f"HTF Bollinger stretched: {', '.join(f'{t} {zones[t]}' for t in stretched)}\n"
                f"Wait for the M15/H1 close back inside, stop beyond the extreme.")
        res["alerted"] = await alert_once(f"cbdrx:{sym}:{box['session']}:{want}", text)
    return res
