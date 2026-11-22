"""Z-score × COT/retail confluence setups.

  GET /zconfluence/scan?z=2&notify=true   graded setups (A confluence · B z-only · X conflict)
"""

from fastapi import APIRouter, Query

from services import zconfluence as zc

router = APIRouter(prefix="/zconfluence", tags=["zconfluence"])


@router.get("/scan")
async def scan(z: float = Query(2.0, ge=1.0, le=5.0), notify: bool = Query(False)):
    return await zc.scan(z, notify)
