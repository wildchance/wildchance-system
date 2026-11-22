"""Macro: FRED dashboard, AI brief, and the active direction locks.

  GET /macro/dashboard      FRED series, yield curve state, regime tags (cached 6h)
  GET /macro/brief          5-line AI brief written from the FRED numbers (NVIDIA)
  GET /macro/locks          effective Stage-0 direction locks (regimes + AI + manual)
"""

from fastapi import APIRouter, Query

from services import macro_dashboard as md

router = APIRouter(prefix="/macro", tags=["macro"])


@router.get("/dashboard")
async def dashboard(force: bool = Query(False)):
    return await md.dashboard(force)


@router.get("/brief")
async def brief(force: bool = Query(False)):
    return await md.brief(force)


@router.get("/locks")
async def locks():
    from services import swing_service as sw
    return {"regimes": sw.active_regimes(), "presets": sw.REGIME_PRESETS,
            "effective_locks": sw.macro_bias()}
