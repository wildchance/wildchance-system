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
async def pending(token: str = Query(...),
                  account: str = Query(None, description="fleet account filter: acc1..acc5"),
                  db: AsyncSession = Depends(get_db)):
    _auth(token)
    return {"orders": await te.pending(db, account=account)}


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


@router.post("/breakeven")
async def breakeven(token: str = Query(...), db: AsyncSession = Depends(get_db)):
    """Arm the runner's stop to break-even on any scale-out group whose first partial has
    filled, queuing the SL-to-BE modify for the bridge. Idempotent — runs on ack too, but
    schedule it (cron) as a safety net so a missed ack still trails the stop. Token-guarded."""
    _auth(token)
    return await te.breakeven_sweep(db)


@router.get("/routing")
async def routing():
    """The strategy→account routing map — which setup (source) places on which account
    when STRATEGY_ROUTING is on. Set STRATEGY_ACCOUNTS (JSON) in the app env to change it;
    each per-account VPS connector (MT5_ACCOUNT=accN) then places only its strategy."""
    return {
        "strategy_routing_enabled": te.STRATEGY_ROUTING_ENABLED,
        "fleet_enabled": te.FLEET_ENABLED,
        "map": te.STRATEGY_ACCOUNTS,
        "default_account": te.STRATEGY_DEFAULT_ACCOUNT,
        "mode": ("per-setup routing (5 accounts, different setups)" if te.STRATEGY_ROUTING_ENABLED
                 else "copy-fleet (same trade × accounts)" if te.FLEET_ENABLED
                 else "single account (no routing)"),
        "note": ("each source routes to its account; run one VPS connector per account "
                 "with MT5_ACCOUNT=accN" if te.STRATEGY_ROUTING_ENABLED else
                 "set STRATEGY_ROUTING_ENABLED=true + STRATEGY_ACCOUNTS to split setups across accounts"),
    }


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
