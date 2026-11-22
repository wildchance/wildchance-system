"""WildChance 4-step pipeline audit.

  POST /pipeline/audit        body: {"watchlist": [...], ...options}  → R01…R_N ladder
  POST /pipeline/calibration  body: [{"prob": .6, "won": true, "r": 1.8}, ...] → Brier

Watchlist item: {"symbol": "XAUUSD", "side": "BUY", "win_rate": 0.48, "rr": 2.1,
"adx": 27, "cost_r": 0.05, "range_bound": false, "fakeout_prob": 0.2,
"news": ["US CPI"], "entry": 3540, "stop": 3505, "sigma": 75}
"""

from __future__ import annotations

from typing import Dict, List, Optional

from fastapi import APIRouter, Body
from pydantic import BaseModel, Field

from lunar import engine as lu
from pipeline import engine as pe

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


class AuditRequest(BaseModel):
    watchlist: List[Dict] = Field(default_factory=list)
    risk_pct: float = 0.5
    max_concurrent: int = 3
    prop_mode: bool = True
    use_lunar: bool = False
    macro_bias: str = "BOTH"
    lunar_observation_hours: Optional[float] = 24


@router.post("/audit")
async def audit(req: AuditRequest):
    lunar = lu.lunar_state(None, req.lunar_observation_hours or 24) if req.use_lunar else None
    out = pe.run_audit(req.watchlist, req.risk_pct, req.max_concurrent, req.prop_mode,
                       lunar=lunar, use_lunar=req.use_lunar, macro_bias=req.macro_bias)
    if lunar:
        out["lunar"] = lunar
    return out


@router.post("/calibration")
async def calibration(records: List[Dict] = Body(default_factory=list)):
    return pe.calibration(records)
