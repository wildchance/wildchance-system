"""Portfolio construction framework API.

Exposes the four halves of the playbook as read/compute endpoints (all
stateless — these are calculators over the frozen framework, like /usdjpy/risk):

  GET  /portfolio                  the whole framework (tiers, ranking, summary)
  GET  /portfolio/tiers            the four conviction tiers + allocations
  GET  /portfolio/tiers/{n}        one tier (1-4)
  GET  /portfolio/candidates       every candidate across all tiers
  GET  /portfolio/ranking          current hedge-fund ranking
  POST /portfolio/score            run the Entry Precision Engine (>80 to enter)
  GET  /portfolio/risk             the dynamic risk budget for a balance/drawdown
  POST /portfolio/exposure         check basket exposures against the caps
  GET  /portfolio/drawdown/{pct}   the control that fires at a given drawdown
  POST /portfolio/plan             build the ATR SL/TP ladder for a trade
"""

from __future__ import annotations

from typing import Dict, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from portfolio import framework, risk_model, sltp
from portfolio.scoring import score_entry

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


# --- framework (declarative) ------------------------------------------------
@router.get("")
async def get_framework():
    return framework.to_dict()


@router.get("/tiers")
async def tiers():
    return {
        "total_allocation": framework.total_allocation(),
        "tiers": [t.to_dict() for t in framework.TIERS],
    }


@router.get("/tiers/{number}")
async def tier_one(number: int):
    try:
        return framework.tier(number).to_dict()
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/candidates")
async def candidates():
    return {"candidates": framework.all_candidates()}


@router.get("/ranking")
async def ranking():
    return {"ranking": framework.HEDGE_FUND_RANKING}


# --- Entry Precision Engine -------------------------------------------------
class ScoreIn(BaseModel):
    bias: str
    price: float
    ema200: float
    rsi: float
    macd_hist: float
    retail_long_pct: float
    atr_now: float
    atr_avg: float
    breakout: bool = False
    ema200_slope: Optional[float] = None


@router.post("/score")
async def score(payload: ScoreIn):
    try:
        return score_entry(
            bias=payload.bias,
            price=payload.price,
            ema200=payload.ema200,
            rsi=payload.rsi,
            macd_hist=payload.macd_hist,
            retail_long_pct=payload.retail_long_pct,
            atr_now=payload.atr_now,
            atr_avg=payload.atr_avg,
            breakout=payload.breakout,
            ema200_slope=payload.ema200_slope,
        ).to_dict()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# --- dynamic risk model -----------------------------------------------------
@router.get("/risk")
async def risk(
    balance: float = Query(..., gt=0),
    drawdown_pct: float = Query(0.0, ge=0.0),
):
    return risk_model.risk_budget(balance, drawdown_pct).to_dict()


class ExposureIn(BaseModel):
    exposures: Dict[str, float]   # {group: exposure_pct}


@router.post("/exposure")
async def exposure(payload: ExposureIn):
    try:
        return risk_model.aggregate_exposure(payload.exposures)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/drawdown/{pct}")
async def drawdown(pct: float):
    try:
        return risk_model.drawdown_action(pct)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# --- SL/TP architecture -----------------------------------------------------
class PlanIn(BaseModel):
    symbol: str
    bias: str
    entry: float
    atr: Optional[float] = None    # supply atr OR stop
    stop: Optional[float] = None


@router.post("/plan")
async def plan(payload: PlanIn):
    try:
        if payload.atr is not None:
            tp = sltp.build_plan(payload.symbol, payload.bias, payload.entry, payload.atr)
        elif payload.stop is not None:
            tp = sltp.plan_from_stop(payload.symbol, payload.bias, payload.entry, payload.stop)
        else:
            raise ValueError("supply either atr or stop")
        return tp.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
