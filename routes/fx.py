"""Hedge-fund FX layer (ops token).

  GET /fx/strength?notify=true   8-currency strength matrix + cleanest pairings (morning card)
  GET /fx/rotation?notify=true   event rotation: currencies stepping aside + pairs to rotate to
  GET /fx/exposure               net open exposure per currency (tracked signals)
"""

import datetime as dt

from fastapi import APIRouter, Query

from services import fx_rotation as fx

router = APIRouter(prefix="/fx", tags=["fx"])


@router.get("/strength")
async def strength(notify: bool = Query(False)):
    st = await fx.strength(max_age_s=0 if notify else 1800)
    if notify:
        from services.realtime import alert_once
        rot = await fx.rotation()
        st["sent"] = await alert_once(f"fxstrength:{dt.date.today()}", fx.card(st, rot), ttl_hours=20)
    return st


@router.get("/rotation")
async def rotation(notify: bool = Query(False)):
    rot = await fx.rotation()
    if notify and rot.get("blocked"):
        from services.realtime import alert_once
        key = f"fxrot:{','.join(sorted(rot['blocked']))}:{dt.date.today()}"
        rot["sent"] = await alert_once(key, fx.card(await fx.strength(), rot), ttl_hours=12)
    return rot


@router.get("/exposure")
async def exposure():
    from services import signal_tracker
    rows = [r for r in await signal_tracker._load() if r["result"] == "open"]
    return {"open_signals": len(rows), "net_units": fx.net_exposure(rows), "cap": fx.EXPOSURE_MAX}
