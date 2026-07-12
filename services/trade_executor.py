"""Trade executor — translate a computed signal into a broker order + queue it.

App-side only (broker-agnostic). `build_order` is pure and testable; enqueue /
pending / ack manage the queue the MT5 bridge consumes. Nothing here talks to
MT5 — that's the standalone connector on the VPS (mt5_bridge/connector.py).
"""

from __future__ import annotations

from typing import List, Optional, Sequence

from decouple import config
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.execution_model import ExecutionOrder

MAGIC = 770001                          # identifies this system's trades in MT5

# Scale-out: split the position across the trend-extension TP ladder. Front-loaded
# by default — bank the most at the nearest target, leave a runner for the furthest
# extension. All legs share ONE stop (structure invalidation).
SCALE_OUT = config("EXECUTION_SCALE_OUT", default="true").lower() == "true"
_WEIGHTS_RAW = config("EXECUTION_SCALE_WEIGHTS", default="0.4,0.3,0.2,0.1")
DEFAULT_WEIGHTS = tuple(float(x) for x in _WEIGHTS_RAW.split(",") if x.strip()) or (0.4, 0.3, 0.2, 0.1)
MIN_LOT = config("EXECUTION_MIN_LOT", default=0.01, cast=float)
LOT_STEP = config("EXECUTION_LOT_STEP", default=0.01, cast=float)


def plan_scale_out(total_volume: float, tp_prices: Sequence[float],
                   weights: Sequence[float] = DEFAULT_WEIGHTS,
                   min_lot: float = MIN_LOT, step: float = LOT_STEP) -> List[dict]:
    """Split ``total_volume`` across the TP ladder → list of {volume, tp} legs.

    Volume is quantised to the broker lot ``step`` and every leg is guaranteed
    ≥ ``min_lot`` (each rung starts with one step, the rest is distributed by
    ``weights`` with largest-remainder rounding so the legs sum EXACTLY to the
    total). If the lot is too small to split (e.g. 0.01 over 4 rungs), it returns
    as many rungs as it can afford — nearest TPs first. Empty if total < min_lot.
    """
    tps = [p for p in tp_prices if p is not None]
    if step <= 0:
        return []
    units_total = int(round(total_volume / step))
    # A leg must be ≥ min_lot, i.e. ≥ this many whole lot-steps.
    min_units = max(1, int(round(min_lot / step)))
    if units_total < min_units or not tps:
        return []
    n = min(len(tps), units_total // min_units)     # rungs we can afford at ≥ min_lot
    if n < 1:
        return []
    w = list(weights[:n])
    if len(w) < n:                                  # pad if fewer weights than rungs
        w += [w[-1] if w else 1.0] * (n - len(w))
    sw = sum(w) or 1.0

    alloc = [min_units] * n                         # floor: min_lot per rung
    remaining = units_total - min_units * n
    if remaining > 0:
        raw = [remaining * wi / sw for wi in w]
        floors = [int(x) for x in raw]
        for i in range(n):
            alloc[i] += floors[i]
        leftover = remaining - sum(floors)
        # hand leftover steps to the largest fractional remainders (front-loaded ties)
        order = sorted(range(n), key=lambda i: (raw[i] - floors[i], w[i]), reverse=True)
        for i in order[:leftover]:
            alloc[i] += 1
    return [{"volume": round(alloc[i] * step, 2), "tp": tps[i]} for i in range(n)]


def build_order(sig: dict, symbol: str = "XAUUSD", source: str = "gold") -> Optional[dict]:
    """Normalize a gold signal into an MT5-ready order, or None if not tradeable.

    Structure entries (Wade OTE) become LIMIT orders at the OTE price; otherwise
    a MARKET order at the signal entry. SL/TP come straight from the sized card.
    """
    if sig.get("signal") not in ("LONG", "SHORT", "BUY", "SELL"):
        return None
    if not sig.get("gate", {}).get("allow", True):
        return None
    side = "buy" if sig["signal"] in ("LONG", "BUY") else "sell"
    # Structure/OTE entries AND pre-London/CRT/S&D limit cards become LIMIT orders
    # at their entry price; everything else is a market order.
    otype = "limit" if (sig.get("entry_mode") == "structure"
                        or sig.get("kind") == "limit") else "market"
    tps = [t.get("price") for t in sig.get("targets", []) if t.get("price") is not None]
    return {
        "symbol": symbol,
        "side": side,
        "order_type": otype,
        "volume": float(sig.get("lot") or 0.0),
        "price": float(sig["entry"]) if otype == "limit" else None,
        "sl": float(sig["stop"]) if sig.get("stop") is not None else None,
        "tp": float(tps[0]) if tps else None,          # TP1; bridge can scale out to the rest
        "tp_levels": tps,
        "magic": MAGIC,
        "comment": (sig.get("profile") or source)[:31],
        "source": source,
    }


def build_orders(sig: dict, symbol: str = "XAUUSD", source: str = "gold",
                 scale_out: Optional[bool] = None,
                 weights: Sequence[float] = DEFAULT_WEIGHTS) -> List[dict]:
    """One or MANY orders for a signal — scaled out across the trend-TP ladder.

    When the signal carries a ``trend_targets`` ladder and ``scale_out`` is on,
    the sized position is split into partial legs, each a fraction of the volume
    with its OWN trend-extension TP and the SAME structure stop, so the trade
    ladders out (bank early, run a tail). Otherwise a single order (the base TP1).
    Backward-compatible: callers that want one order still have ``build_order``.
    """
    base = build_order(sig, symbol=symbol, source=source)
    if base is None:
        return []
    do_scale = SCALE_OUT if scale_out is None else scale_out
    tl = sig.get("trend_targets") or {}
    tps = [t.get("price") for t in tl.get("targets", []) if t.get("price") is not None]
    if not do_scale or len(tps) < 2:
        return [base]

    legs = plan_scale_out(base.get("volume") or 0.0, tps, weights)
    if len(legs) < 2:                               # lot too small to split → single
        return [base]

    n = len(legs)
    tag = (sig.get("profile") or source)[:22]
    orders: List[dict] = []
    for i, leg in enumerate(legs, start=1):
        o = dict(base)
        o["volume"] = leg["volume"]
        o["tp"] = leg["tp"]                          # this leg's trend rung
        o["tp_levels"] = tps
        o["scale_leg"] = f"{i}/{n}"
        o["comment"] = f"{tag} {i}/{n}"[:31]         # MT5 comment cap
        orders.append(o)
    return orders


async def enqueue(db: AsyncSession, order: dict) -> dict:
    """Persist a pending order for the bridge to pull."""
    row = ExecutionOrder(
        symbol=order["symbol"], side=order["side"], order_type=order["order_type"],
        volume=order["volume"], price=order.get("price"), sl=order.get("sl"),
        tp=order.get("tp"), magic=order.get("magic", MAGIC),
        comment=order.get("comment"), source=order.get("source"), status="pending",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return {"id": row.id, "status": row.status, **order}


async def enqueue_all(db: AsyncSession, orders: List[dict]) -> List[dict]:
    """Persist a list of orders (the scale-out legs) as pending rows."""
    return [await enqueue(db, o) for o in orders]


async def pending(db: AsyncSession, limit: int = 20) -> List[dict]:
    res = await db.execute(
        select(ExecutionOrder).where(ExecutionOrder.status == "pending")
        .order_by(ExecutionOrder.created_at.asc()).limit(limit))
    return [_to_dict(r) for r in res.scalars().all()]


async def ack(db: AsyncSession, order_id: int, status: str,
              ticket: Optional[int] = None, fill_price: Optional[float] = None) -> dict:
    res = await db.execute(select(ExecutionOrder).where(ExecutionOrder.id == order_id))
    row = res.scalar_one_or_none()
    if row is None:
        return {"error": f"order {order_id} not found"}
    row.status = status
    if ticket is not None:
        row.ticket = ticket
    if fill_price is not None:
        row.fill_price = fill_price
    await db.commit()
    return _to_dict(row)


async def recent(db: AsyncSession, limit: int = 50) -> List[dict]:
    res = await db.execute(
        select(ExecutionOrder).order_by(ExecutionOrder.created_at.desc()).limit(limit))
    return [_to_dict(r) for r in res.scalars().all()]


def _to_dict(r: ExecutionOrder) -> dict:
    return {
        "id": r.id, "symbol": r.symbol, "side": r.side, "order_type": r.order_type,
        "volume": r.volume, "price": r.price, "sl": r.sl, "tp": r.tp,
        "magic": r.magic, "comment": r.comment, "source": r.source,
        "status": r.status, "ticket": r.ticket, "fill_price": r.fill_price,
        "created_at": str(r.created_at) if r.created_at else None,
    }
