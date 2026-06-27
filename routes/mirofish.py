"""MiroFish macro-sentiment endpoints.

  GET  /mirofish/status              adapter configured? reachable?
  GET  /mirofish/score/{symbol}      latest cached score for this instrument
  POST /mirofish/score/{symbol}      trigger a fresh simulation (async)

Scores are 0-100: >=65 bullish, <=35 bearish, 36-64 neutral.
A GET on a pair that hasn't been simulated yet returns score=null, bias=null
with a note explaining whether MiroFish is configured or just pending.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from instruments.catalog import get as get_instrument
from correlation.engine import dxy_direction
from services.dxy_service import fetch_dxy
from services.wildchance_service import get_latest_feed
import services.mirofish_service as mf_svc

router = APIRouter(prefix="/mirofish", tags=["mirofish"])

_COT_CODE = {
    "EUR/USD": "EUR", "GBP/USD": "GBP", "USD/JPY": "JPY",
    "USD/CAD": "CAD", "AUD/USD": "AUD", "USD/CHF": "CHF", "XAU/USD": "XAU",
}


async def _build_context(pair: str) -> dict:
    """Assemble a concise market context dict to pass into MiroFish."""
    feed = await get_latest_feed()
    dxy_closes, _ = await fetch_dxy("1h")
    ins = get_instrument(pair)
    signals = (feed or {}).get("signals", [])
    cot = (feed or {}).get("cot", {})
    sig = next((s for s in signals if s.get("pair") == pair), {})
    code = _COT_CODE.get(pair)
    cot_rows = cot.get(code, []) if code else []
    return {
        "dxy_direction": dxy_direction(dxy_closes) if dxy_closes else "unknown",
        "dxy_sign": ins.dxy_sign if ins else 0,
        "signal_verdict": sig.get("verdict", ""),
        "signal_confidence": sig.get("confidence", ""),
        "cot_net": cot_rows[-1].get("lev") if cot_rows else None,
    }


@router.get("/status")
async def mirofish_status():
    """Is the MiroFish adapter configured and can it reach the instance?"""
    return await mf_svc.status()


@router.get("/score/{symbol:path}")
async def get_score(symbol: str):
    """Return the latest cached MiroFish sentiment score for an instrument."""
    ins = get_instrument(symbol)
    if ins is None:
        raise HTTPException(status_code=404, detail=f"unknown instrument '{symbol}'")
    result = await mf_svc.score(ins.symbol)
    return {
        "symbol": ins.symbol,
        "configured": bool(mf_svc.MIROFISH_BASE_URL),
        "score": result.get("score") if result else None,
        "bias": result.get("bias") if result else None,
        "cached_at": result.get("cached_at") if result else None,
        "note": (
            None if result
            else ("MiroFish not configured -- set MIROFISH_BASE_URL on Render"
                  if not mf_svc.MIROFISH_BASE_URL
                  else "simulation pending -- POST /mirofish/score/{symbol} to trigger")
        ),
    }


@router.post("/score/{symbol:path}")
async def trigger_score(symbol: str):
    """Kick off a fresh MiroFish simulation for this pair (returns immediately)."""
    ins = get_instrument(symbol)
    if ins is None:
        raise HTTPException(status_code=404, detail=f"unknown instrument '{symbol}'")
    if not mf_svc.MIROFISH_BASE_URL:
        raise HTTPException(
            status_code=503,
            detail="MIROFISH_BASE_URL not configured -- add it to Render environment variables",
        )
    if ins.symbol in mf_svc._running:
        return {"symbol": ins.symbol, "status": "already_running",
                "note": "simulation already in progress"}
    ctx = await _build_context(ins.symbol)
    asyncio.create_task(mf_svc._refresh(ins.symbol, ctx))
    return {
        "symbol": ins.symbol,
        "status": "simulation_started",
        "context_sent": ctx,
        "note": "GET /mirofish/score/{symbol} to retrieve score when ready (~3-5 min)",
    }
