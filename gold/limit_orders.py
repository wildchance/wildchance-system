"""Size a limit-order spec into a full tracked/executable card (pure).

The pre-London CBDR, 1-5-9 CRT and Seek & Destroy planners emit limit specs of the
shape {side, entry, stop, targets:[prices], trade_type, reason}. This turns one
into a money-sized card — lot from the $ risk and the entry→stop distance, RR-
tagged targets, and the prop-firm gate — in the same shape gold_positions and
trade_executor already consume. No network.
"""

from __future__ import annotations

from typing import Optional, Sequence

from gold.risk_engine import GOLD_USD_PER_POINT, size_for_risk, targets as rr_targets
from propfirm.engine import evaluate_trade, max_lot, DEFAULT_TIER


def size_limit(order: dict, balance: float, risk_usd: float = 20.0,
               tier: str = DEFAULT_TIER, symbol: str = "XAU/USD",
               profile: Optional[str] = None) -> dict:
    """Money-size a limit spec. Returns a card (signal/entry/stop/targets/lot/gate)
    or {"signal": "NO TRADE", ...} if the geometry is invalid."""
    side = (order.get("side") or "").lower()
    if side not in ("long", "buy", "short", "sell"):
        return {"signal": "NO TRADE", "reason": "limit order missing side"}
    entry, stop = order.get("entry"), order.get("stop")
    if entry is None or stop is None:
        return {"signal": "NO TRADE", "reason": "limit order missing entry/stop"}
    sig_side = "LONG" if side in ("long", "buy") else "SHORT"
    dist = abs(entry - stop)
    if dist <= 0:
        return {"signal": "NO TRADE", "reason": "invalid limit stop distance"}

    lot = max(0.01, min(size_for_risk(entry, stop, risk_usd), max_lot(balance)))
    risk_actual = round(lot * dist * GOLD_USD_PER_POINT, 2)

    # Targets: use the plan's explicit prices, RR-tagged; else derive RR ladder.
    prices = order.get("targets") or []
    if prices:
        tps = []
        for p in prices:
            if p is None:
                continue
            rr = round(abs(p - entry) / dist, 2) if dist else 0
            tps.append({"price": round(p, 2), "rr": rr,
                        "usd": round(lot * abs(p - entry) * GOLD_USD_PER_POINT, 2)})
    else:
        tps = rr_targets(entry, stop, side, (2, 3))
        for t in tps:
            t["usd"] = round(lot * abs(t["price"] - entry) * GOLD_USD_PER_POINT, 2)

    return {
        "signal": sig_side, "instrument": symbol, "kind": "limit",
        "trade_type": order.get("trade_type", "limit"),
        "entry": round(entry, 2), "stop": round(stop, 2),
        "stop_distance": round(dist, 2), "lot": lot, "risk_usd": risk_actual,
        "targets": tps,
        "gate": evaluate_trade(balance, risk_actual, tier=tier),
        "profile": order.get("label") or profile or order.get("trade_type", "limit"),
        "justification": order.get("reason", ""),
        "balance": balance, "tier": tier,
    }
