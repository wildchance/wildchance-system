"""Real-time high-impact economic calendar — market-shift awareness.

  GET /calendar                 all tier-1 events this week (live feed)
  GET /calendar?ccy=USD,EUR     only these currencies
  GET /calendar/for/{symbol}    tier-1 events for a pair's currencies

Reads the same ForexFactory feed the confluence engine uses, filtered to
high-impact (rate decisions, CPI, jobs, GDP, FOMC/ECB/BOE/…). Free feed covers
the current week; the deterministic NFP clock supplements USD.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from services.news_guard import high_impact_calendar, symbol_currencies

router = APIRouter(prefix="/calendar", tags=["calendar"])


def _parse_ccy(ccy: str):
    if not ccy:
        return None
    return {c.strip().upper() for c in ccy.split(",") if c.strip()}


@router.get("")
async def calendar(ccy: str = Query("", description="comma list, e.g. USD,EUR")):
    currencies = _parse_ccy(ccy)
    events = await high_impact_calendar(currencies)
    return {"currencies": sorted(currencies) if currencies else "all",
            "count": len(events), "events": events}


@router.get("/for/{symbol:path}")
async def calendar_for_symbol(symbol: str):
    currencies = symbol_currencies(symbol)
    events = await high_impact_calendar(currencies)
    return {"symbol": symbol, "currencies": sorted(currencies),
            "count": len(events), "events": events}
