"""Order-flow pressure as a confluence factor — guarded, dormant until L2 arrives.

Reads an L2 book snapshot from Redis key `book:{symbol}` (JSON {"bids":[[p,s]..],
"asks":[[p,s]..]}) if REDIS_URL is set, computes the depth imbalance via the flow
engine, and reports whether that pressure CONFIRMS or DIVERGES from a trade side.

Returns None when there's no book (free tiers have no L2 feed), so it's a no-op
that lights up automatically the moment a depth feed populates Redis — same
pattern as the Polygon stream and MiroFish.
"""

from __future__ import annotations

import json
from typing import Optional

from decouple import config

from flow.engine import depth_imbalance, classify

REDIS_URL = config("REDIS_URL", default=None)


async def _book_from_redis(symbol: str) -> Optional[dict]:
    if not REDIS_URL:
        return None
    try:
        import redis.asyncio as redis_async
        cli = await redis_async.from_url(REDIS_URL, decode_responses=True)
        raw = await cli.get(f"book:{symbol}")
        try:
            await cli.close()
        except Exception:
            pass
        return json.loads(raw) if raw else None
    except Exception:
        return None


async def pressure(symbol: str, book: Optional[dict] = None,
                   levels: int = 5, strong: float = 0.3) -> Optional[dict]:
    """Imbalance read for a symbol, or None if no book is available."""
    if book is None:
        book = await _book_from_redis(symbol)
    if not book:
        return None
    bids = book.get("bids") or []
    asks = book.get("asks") or []
    if not bids and not asks:
        return None
    imb = depth_imbalance(bids, asks, levels)
    out = classify(imb, strong)
    out["source"] = "book"
    return out


def confluence(pressure_read: Optional[dict], side: str) -> dict:
    """Does order-flow pressure agree with the trade side?"""
    if not pressure_read:
        return {"flow": None, "agreement": "none", "note": "no order-flow data"}
    p = pressure_read.get("pressure")           # long | short | neutral
    if p == "neutral":
        agree = "neutral"
    elif (p == "long" and side == "long") or (p == "short" and side == "short"):
        agree = "confirms"
    else:
        agree = "diverges"
    return {"flow": pressure_read, "agreement": agree,
            "note": f"order-flow {p} vs {side} → {agree}"}
