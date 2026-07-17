"""STRATOPS muster — collect every live candidate, sort, and allocate (network glue).

Runs AFCENT's scanners, normalises each fired signal into a candidate with its
campaign framing, then hands the set to gold.stratops for scoring + allocation
under ARCENT's exposure cap. Read-only: it does not open positions — it returns the
engagement list (take / hold / stand-down) for the operator (or a follow-up
execute step) to act on.
"""

from __future__ import annotations

from typing import List

from sqlalchemy.ext.asyncio import AsyncSession

from services import gold_scan, gold_intraday
from services.candlerange_service import crt_read
from services import gold_positions as gp
from gold.limit_orders import size_limit
from gold.objective import advances, campaign_objective
from gold import stratops


def _with_campaign(sig: dict) -> dict:
    if sig and "campaign" not in sig and sig.get("entry") is not None:
        side = "long" if sig.get("signal") == "LONG" else "short"
        sig["campaign"] = advances(side, sig["entry"])
    return sig


async def muster(db: AsyncSession, balance: float = 5000.0,
                 risk_usd: float = 20.0) -> dict:
    """Gather candidates from the tiers + swing + CRT, score, and allocate."""
    cands: List[dict] = []

    def add(sig):
        if sig and sig.get("signal") in ("LONG", "SHORT"):
            cands.append(_with_campaign(sig))

    # MARCENT / AFCENT — the tiered intraday scan (protraction softened to a score).
    add(await gold_intraday.scan(balance=balance, risk_usd=risk_usd,
                                 require_protraction=False, notify=False))
    # The weekly-profile swing scan.
    add(await gold_scan.scan(balance=balance, risk_usd=risk_usd, notify=False))
    # SOCCENT — confirmed CRT strikes (Asian + NY).
    for sess in ("asia", "ny"):
        r = await crt_read("XAU/USD", session=sess)
        conf = (r or {}).get("confirmation") or {}
        if conf.get("confirmed"):
            add(size_limit(conf["order"], balance, risk_usd))

    positions = await gp.list_positions(db, status="OPEN", limit=100)
    positions += await gp.list_positions(db, status="PENDING", limit=100)

    result = stratops.allocate(cands, positions)
    result["candidates"] = len(cands)
    # Surface the standing campaign objective even if no candidate fired.
    try:
        from utils.price_fetcher import get_forex_price
        price = await get_forex_price("XAU/USD")
        result["campaign"] = campaign_objective(price)
    except Exception:
        pass
    return result
