"""Swing mean-reversion endpoints (HTF Bollinger ±1σ/±2σ + CBDR confluence).

  GET /swing/scan                 whole universe, ranked ENTRY › LIMIT › WATCH › TREND
  GET /swing/scan?notify=true     + Telegram card for each ENTRY/LIMIT (deduped)
  GET /swing/{symbol}             one instrument, e.g. /swing/XAU%2FUSD or /swing/BTCUSD
  GET /swing/pipeline             swing fades fed through the 4-step pipeline
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from services import swing_service as sw

router = APIRouter(prefix="/swing", tags=["swing"])


@router.get("/scan")
async def scan(symbols: Optional[str] = Query(None, description="comma list; default universe"),
               notify: bool = Query(False)):
    syms = [s.strip() for s in symbols.split(",")] if symbols else None
    out = await sw.scan(syms)
    if notify:
        from services.realtime import alert_once
        sent = []
        for r in out["results"]:
            if r.get("status") in ("ENTRY", "LIMIT"):
                if await alert_once(f"swing:{r['symbol']}:{r['side']}:{r['levels']['tp2_mean']:.4g}",
                                    sw.format_alert(r)):
                    sent.append(r["symbol"])
        out["notified"] = sent
    return out


@router.get("/pipeline")
async def swing_pipeline(risk_pct: float = Query(0.5), max_concurrent: int = Query(3)):
    from indicators.bollinger import as_watchlist_item
    from pipeline.engine import run_audit
    scan_out = await sw.scan()
    wl = [as_watchlist_item(r["symbol"], r) for r in scan_out["results"]
          if r.get("status") in ("ENTRY", "LIMIT", "WATCH")]
    return run_audit(wl, risk_pct=risk_pct, max_concurrent=max_concurrent)


@router.get("/{symbol:path}")
async def one(symbol: str):
    s = symbol.upper()
    rev = {v: k for k, v in sw.DISPLAY.items()}
    if s in rev:
        s = rev[s]
    elif "/" not in s and len(s) == 6 and s.isalpha():
        s = f"{s[:3]}/{s[3:]}"
    return await sw.analyze(s)
