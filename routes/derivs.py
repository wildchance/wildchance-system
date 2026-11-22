"""Options context — the flowchart's PUT & CALL / EXPIRY TABLE boxes.

  GET /options/expiry?days=35     exchange expiry calendar (equity, CME gold/FX, Deribit)
  GET /options/btc                Deribit BTC open interest per expiry: max pain, walls, P/C
  GET /options/eth                same for ETH
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Query

from derivs.expiry import upcoming
from services.deribit_service import expiry_table

router = APIRouter(prefix="/options", tags=["options"])


@router.get("/expiry")
async def expiry(days: int = Query(35, ge=1, le=120)):
    return {"from": dt.date.today().isoformat(), "expiries": upcoming(dt.date.today(), days),
            "note": "exchange holidays not applied — a holiday moves expiry one business day earlier"}


@router.get("/btc")
async def btc():
    return await expiry_table("BTC")


@router.get("/eth")
async def eth():
    return await expiry_table("ETH")
