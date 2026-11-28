"""Owner theses (ops token): GET /thesis/{symbol} — Elliott scenarios vs live prices → best bias."""

from fastapi import APIRouter

router = APIRouter(prefix="/thesis", tags=["thesis"])


@router.get("")
async def all_theses():
    from services import thesis
    return {"symbols": sorted(thesis.load())}


@router.get("/{symbol}")
async def one(symbol: str):
    from services import thesis
    r = await thesis.run(symbol)
    return {**r, "text": thesis.text(r)} if r.get("ok") else r
