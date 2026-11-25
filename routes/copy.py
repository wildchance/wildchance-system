"""Copy-trader dashboard (owner): GET /copy (page), /copy/status (JSON), POST /copy/control."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse

router = APIRouter(prefix="/copy", tags=["copy"])


@router.get("")
async def page():
    return FileResponse("static/copy/index.html")


@router.get("/status")
async def status():
    from services import copy_trade
    return await copy_trade.status()


@router.post("/control")
async def control(login: str = Query(..., description="account login or 'all'"),
                  paused: Optional[bool] = Query(None), mult: Optional[float] = Query(None, ge=0, le=10)):
    from services import copy_trade
    return await copy_trade.control(login, paused=paused, mult=mult)
