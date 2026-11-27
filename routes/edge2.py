"""EdgeFinder v2 (owner / terminal): the scored board, carry, score history.

  GET  /edge2                 the board (cached 1 h; ?force=true rebuilds)
  GET  /edge2/carry           policy-rate differentials
  GET  /edge2/history/{sym}   daily [date, score, close] for the score-vs-price chart
  POST /edge2/ingest          fold new calendar actuals into the macro layer (hourly cron)
  POST /edge2/refresh         rebuild + store today's snapshot (daily cron)
"""

from fastapi import APIRouter, Query

router = APIRouter(prefix="/edge2", tags=["edge2"])


@router.get("")
async def board(force: bool = Query(False)):
    from services import edge_v2
    return await edge_v2.board(force=force)


@router.get("/carry")
async def carry():
    from services import edge_v2
    b = await edge_v2.board()
    rows = sorted([{"symbol": r["symbol"], **r["carry"]} for r in b["rows"] if r.get("carry")],
                  key=lambda x: -abs(x["diff"]))
    return {"rows": rows}


@router.get("/history/{symbol:path}")
async def history(symbol: str):
    from services import edge_v2
    return {"symbol": symbol, "history": await edge_v2.history(symbol)}


@router.post("/ingest")
async def ingest():
    """Hourly: new calendar actuals → macro layer, then rebuild the board (pool prices only)."""
    from services import edge_v2, macro_surprise
    import asyncio
    r = await macro_surprise.ingest()
    # the board rebuild takes a while (pool prices for every market): run it in the
    # background so the cron call answers fast instead of being logged as slow
    t = asyncio.create_task(edge_v2.board(force=True))
    _bg.add(t)
    t.add_done_callback(_bg.discard)
    return {**r, "board": "rebuilding"}


_bg: set = set()                                   # keep background tasks referenced


@router.post("/refresh")
async def refresh():
    from services import edge_v2
    return await edge_v2.snapshot()
