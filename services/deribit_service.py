"""Deribit public options data (free, no key) → BTC/ETH expiry table with max pain."""

from __future__ import annotations

import time
from typing import Dict

import httpx

from derivs.options_oi import by_expiry, parse_deribit

_cache: Dict[str, Dict] = {}


async def expiry_table(currency: str = "BTC", top: int = 6) -> Dict:
    cur = currency.upper()
    hit = _cache.get(cur)
    if hit and time.time() - hit["_t"] < 900:
        return {k: v for k, v in hit.items() if k != "_t"}
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get("https://www.deribit.com/api/v2/public/get_book_summary_by_currency",
                            params={"currency": cur, "kind": "option"})
        res = r.json().get("result", []) if r.status_code == 200 else []
    except Exception:
        res = []
    if not res:
        return {"currency": cur, "ok": False, "reason": "Deribit unreachable"}
    spot = next((float(x["underlying_price"]) for x in res if x.get("underlying_price")), None)
    table = by_expiry(parse_deribit(res), spot)
    out = {"currency": cur, "ok": True, "spot": spot, "expiries": table[:top],
           "as_of": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime())}
    _cache[cur] = {**out, "_t": time.time()}
    return out
