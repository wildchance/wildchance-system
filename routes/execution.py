"""Execution queue endpoints — the interface between the app and the MT5 bridge.

  GET  /execution/pending   orders the bridge should place (token-guarded)
  POST /execution/ack       bridge reports a fill/rejection (token-guarded)
  GET  /execution/orders    recent orders (dashboard/monitoring)

Security: set EXECUTION_TOKEN in env. Without it, the pull/ack endpoints return
503 (execution disabled) so orders are never exposed by accident.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from decouple import config

from database.db import get_db
from services import trade_executor as te

router = APIRouter(prefix="/execution", tags=["execution"])

EXECUTION_TOKEN = config("EXECUTION_TOKEN", default=None)


def _auth(token: str):
    if not EXECUTION_TOKEN:
        raise HTTPException(status_code=503, detail="execution disabled — set EXECUTION_TOKEN")
    if token != EXECUTION_TOKEN:
        raise HTTPException(status_code=403, detail="bad execution token")


class Ack(BaseModel):
    id: int
    status: str                 # filled | rejected | sent | cancelled
    ticket: int | None = None
    fill_price: float | None = None


@router.get("/pending")
async def pending(token: str = Query(...), db: AsyncSession = Depends(get_db)):
    _auth(token)
    return {"orders": await te.pending(db)}


@router.post("/ack")
async def ack(payload: Ack, token: str = Query(...), db: AsyncSession = Depends(get_db)):
    _auth(token)
    return await te.ack(db, payload.id, payload.status,
                        ticket=payload.ticket, fill_price=payload.fill_price)


@router.get("/orders")
async def orders(limit: int = Query(50, ge=1, le=500), db: AsyncSession = Depends(get_db)):
    return {"orders": await te.recent(db, limit)}


@router.get("/reconcile")
async def reconcile(token: str = Query(...),
                    stuck_minutes: int = Query(15, ge=1, le=240),
                    db: AsyncSession = Depends(get_db)):
    """Drift guard — compare the MT5 bridge's order state vs the tracked positions
    and flag orphan fills / stuck orders. Token-guarded. Run on a schedule once live."""
    _auth(token)
    return await te.reconcile(db, stuck_minutes=stuck_minutes)


@router.get("/status")
async def status(db: AsyncSession = Depends(get_db)):
    """Live-execution readiness: is the switch on, the token set, and how many
    orders are queued for the MT5 bridge."""
    queued = await te.pending(db)
    return {
        "execution_enabled": te.EXECUTION_ENABLED,
        "token_set": bool(EXECUTION_TOKEN),
        "queued_orders": len(queued),
        "mode": "LIVE" if te.EXECUTION_ENABLED else "PAPER",
        "note": ("routing tracked trades to the MT5 bridge"
                 if te.EXECUTION_ENABLED else
                 "paper — set EXECUTION_ENABLED=true (+ EXECUTION_TOKEN + run the VPS bridge) to go live"),
    }
