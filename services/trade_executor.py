"""Trade executor — translate a computed signal into a broker order + queue it.

App-side only (broker-agnostic). `build_order` is pure and testable; enqueue /
pending / ack manage the queue the MT5 bridge consumes. Nothing here talks to
MT5 — that's the standalone connector on the VPS (mt5_bridge/connector.py).
"""

from __future__ import annotations

from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.execution_model import ExecutionOrder

MAGIC = 770001                          # identifies this system's trades in MT5


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
