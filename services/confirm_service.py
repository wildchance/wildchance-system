"""Confirmation scan — take the trade on the sweep-and-reject, not the touch.

Watches the live pre-London CBDR deviation levels (+1/+1.5SD sell, −1/−1.5SD buy),
pulls the lower-timeframe (M15) closes, and fires ONLY when price has swept a level
and CLOSED BACK INSIDE (gold.rejection). On a confirmed rejection it money-sizes the
entry (stop beyond the swept wick, targets the mean → opposite rail), alerts it as a
TAKE, and — in deploy mode — opens it as a tracked position (respecting the weekly
budget). Best-effort marker dedup so a cron stays quiet until a NEW rejection prints.
"""

from __future__ import annotations

import os
from typing import Optional

from gold import rejection as rej
from gold.limit_orders import size_limit
from services import gold_scan
from services import gold_positions as gp

_STATE_DIR = os.environ.get("STATE_DIR", "state")
_MARKER = os.path.join(_STATE_DIR, "confirm.state")


def _read_last() -> Optional[str]:
    try:
        with open(_MARKER) as f:
            return f.read().strip() or None
    except Exception:
        return None


def _write_last(sig: str) -> None:
    try:
        os.makedirs(_STATE_DIR, exist_ok=True)
        with open(_MARKER, "w") as f:
            f.write(sig)
    except Exception:
        pass


def _card_text(sized: dict, rj: dict) -> str:
    tps = "  ".join(f"{t['price']}({t['rr']}R)" for t in sized.get("targets", []))
    return (f"🎯 *CONFIRMED — {rj['signal']} XAU/USD*  (sweep+reject)\n"
            f"{rj['note']}\n"
            f"entry `{sized['entry']}`  SL `{sized['stop']}`  lot {sized.get('lot')}\n"
            f"TP: {tps}")


async def confirm(balance: float = 5000.0, risk_usd: float = 20.0,
                  window: str = "prelondon", lookback: int = 3,
                  notify: bool = True, deploy: bool = False,
                  db=None) -> dict:
    """Scan the CBDR deviation levels for a sweep-and-reject on the M15, alert +
    (optionally) deploy the confirmed entry."""
    # live price + the CBDR box levels
    try:
        from utils.price_fetcher import get_forex_price
        price = await get_forex_price("XAU/USD")
    except Exception:
        price = None
    box = None
    try:
        from services.cbdr_service import fetch_cbdr_window
        from cbdr.engine import build_cbdr
        w = await fetch_cbdr_window("XAU/USD", window=window)
        if w and w.get("high") is not None:
            box = build_cbdr(w["high"], w["low"])
    except Exception:
        box = None
    if box is None:
        return {"confirmed": False, "reason": "no CBDR box available"}
    lv, mid = box.levels, round(box.mid, 2)

    # M15 closes for the rejection read
    try:
        from services.ohlc_service import fetch_ohlc_raw
        m15 = await fetch_ohlc_raw("XAU/USD", interval="15min", outputsize=max(4, lookback + 2))
    except Exception:
        m15 = []
    if len(m15) < 2:
        return {"confirmed": False, "reason": "no M15 bars"}

    # candidate levels: sell at +1.5/+1SD, buy at −1.5/−1SD, deepest first.
    checks = [
        ("short", lv.get("+1.5SD"), [mid, lv.get("-1SD")]),
        ("short", lv.get("+1SD"), [mid, lv.get("-1SD")]),
        ("long", lv.get("-1.5SD"), [mid, lv.get("+1SD")]),
        ("long", lv.get("-1SD"), [mid, lv.get("+1SD")]),
    ]
    hit = None
    for side, level, targets in checks:
        if level is None:
            continue
        r = rej.sweep_reject(m15, level, side, lookback=lookback)
        if r:
            hit = (r, targets)
            break
    if hit is None:
        return {"confirmed": False, "price": price,
                "reason": "no sweep-and-reject at a CBDR level yet"}

    r, targets = hit
    order = {"side": r["signal"].lower(), "entry": r["entry"], "stop": r["stop"],
             "targets": [t for t in targets if t is not None], "trade_type": "sniper",
             "reason": r["note"], "label": "sweep_reject"}
    sized = size_limit(order, balance, risk_usd)
    if sized.get("signal") not in ("LONG", "SHORT"):
        return {"confirmed": True, "sized": sized, "reason": sized.get("reason")}

    # dedup on the rejection signature (level + close)
    sig = f"{r['signal']}:{r['level']}:{r['close']}"
    changed = sig != _read_last()
    sent = False
    if notify and changed:
        sent = await gold_scan._tg(_card_text(sized, r))
    _write_last(sig)

    deployed = None
    if deploy and db is not None and changed:
        # respect the weekly budget for this tier before opening
        from gold import trade_budget as tb
        counts = tb.count_by_tier(await gp.list_positions(db, limit=300), tb.week_start())
        if tb.within_budget(sized.get("trade_type", "sniper"), counts.get("sniper", 0)):
            deployed = await gp.open_from_signal(db, sized, source="confirm_reject")
        else:
            deployed = {"skipped": "weekly sniper budget reached"}

    return {"confirmed": True, "changed": changed, "sent": sent,
            "rejection": r, "sized": sized, "deployed": deployed}
