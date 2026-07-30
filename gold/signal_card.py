"""Signal card — turn a setup (entry/stop/TP) into the shareable WILDCHANCE card.

Computes every field on the branded graphic — direction, potential-profit %, risk %,
reward %, risk:reward, points — and the geometry check that a BUY has TP above entry
above stop (and the mirror for a SELL). Pure + stdlib-only. The route renders it into
the HTML card (static/dashboard/signal_card.html) and/or pushes the text to Telegram.
"""

from __future__ import annotations

import datetime as _dt
import math
from typing import Optional


def _r2(x: float) -> float:
    """Round to 2dp HALF-UP (so 0.625 → 0.63, matching the branded card, not Python's
    banker's rounding which gives 0.62)."""
    return math.floor(abs(x) * 100 + 0.5) / 100 * (1 if x >= 0 else -1)


def _prob_tag(rr: float) -> str:
    if rr >= 3.0:
        return "HIGH PROBABILITY SETUP"
    if rr >= 2.0:
        return "GOOD SETUP"
    if rr >= 1.0:
        return "MODERATE SETUP"
    return "LOW R:R — REVIEW"


def build_signal_card(entry: float, stop: float, tp: float,
                      symbol: str = "XAUUSD", side: Optional[str] = None,
                      author: str = "Wildchance_Conglomerate",
                      note: Optional[str] = None,
                      order_type: str = "market",
                      current_price: Optional[float] = None) -> dict:
    """All card fields from entry/stop/TP. Side inferred from geometry if not given.

    order_type: 'market' or 'limit' (a resting pending order). When 'limit' and the
    current price is known, the side_label reads BUY LIMIT / SELL LIMIT — matching how
    it shows in MT5 (e.g. a BUY LIMIT resting at 4033 waiting for price to come down)."""
    entry, stop, tp = float(entry), float(stop), float(tp)
    inferred = "BUY" if tp >= entry else "SELL"
    side = (side or inferred).upper()
    order_type = (order_type or "market").lower()
    side_label = f"{side} LIMIT" if order_type == "limit" else side

    risk_points = _r2(abs(entry - stop))
    reward_points = _r2(abs(tp - entry))
    risk_pct = _r2(risk_points / entry * 100) if entry else 0.0
    reward_pct = _r2(reward_points / entry * 100) if entry else 0.0
    rr = _r2(reward_points / risk_points) if risk_points else 0.0

    # geometry sanity: BUY needs stop<entry<tp; SELL needs tp<entry<stop
    if side == "BUY":
        valid = stop < entry < tp
    else:
        valid = tp < entry < stop
    warnings = [] if valid else [
        f"geometry off for a {side}: expected "
        + ("stop < entry < TP" if side == "BUY" else "TP < entry < stop")]

    now = _dt.datetime.utcnow()
    return {
        "symbol": symbol.upper(), "instrument": "Gold Spot / U.S. Dollar",
        "side": side, "order_type": order_type, "side_label": side_label,
        "entry": entry, "stop_loss": stop, "take_profit": tp,
        "risk_points": risk_points, "reward_points": reward_points,
        "risk_pct": risk_pct, "reward_pct": reward_pct,
        "potential_profit_pct": reward_pct,
        "risk_reward": rr, "risk_reward_str": f"1 : {rr:.2f}",
        "probability": _prob_tag(rr),
        "valid_geometry": valid, "warnings": warnings,
        "author": author,
        "timestamp_utc": now.strftime("%Y-%m-%d %H:%M UTC"),
        "date_label": now.strftime("%B %d, %Y | %H:%M UTC"),
        "note": note,
        # a ready-to-open render URL (the branded HTML card) with params pre-filled
        "render_url": (f"/static/dashboard/signal_card.html?symbol={symbol.upper()}"
                       f"&side={side}&type={order_type}&entry={entry}&stop={stop}&tp={tp}"),
    }


def format_card_telegram(card: dict) -> str:
    """The card as a Telegram message (what you broadcast to the channel)."""
    icon = "🟢" if card["side"] == "BUY" else "🔴"
    warn = ("\n⚠️ " + "; ".join(card["warnings"])) if card.get("warnings") else ""
    return (
        f"{icon} *{card['symbol']} {card.get('side_label', card['side'])}*  ·  {card['instrument']}\n"
        f"*+{card['potential_profit_pct']:.2f}%* potential profit\n\n"
        f"🎯 Take Profit : `{card['take_profit']:.2f}`\n"
        f"📈 Entry       : `{card['entry']:.2f}`\n"
        f"🛡️ Stop Loss   : `{card['stop_loss']:.2f}`\n\n"
        f"Risk {card['risk_pct']:.2f}% ({card['risk_points']:.0f} pts)  ·  "
        f"Reward {card['reward_pct']:.2f}% ({card['reward_points']:.0f} pts)  ·  "
        f"R:R {card['risk_reward_str']}\n"
        f"_{card['probability']}_{warn}\n"
        f"— {card['author']}  ·  {card['timestamp_utc']}")
