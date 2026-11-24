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
                from services import council_gate
                cg = await council_gate.check(r["symbol"], r["side"], "swing")
                if not cg["pass"]:
                    continue
                if await alert_once(f"swing:{r['symbol']}:{r['side']}:{r['levels']['tp2_mean']:.4g}",
                                    sw.format_alert(r) + "\n" + cg["line"]):
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


# --- evidence: backtest + sweep on daily bars (broker feed first, TwelveData fallback) ---
async def _daily(symbol: str, n: int = 2000):
    from services.ohlc_service import fetch_ohlc
    s = symbol.upper()
    rev = {v: k for k, v in sw.DISPLAY.items()}
    s = rev.get(s, s if "/" in s or len(s) != 6 else f"{s[:3]}/{s[3:]}")
    return await fetch_ohlc(s, "1day", n)


@router.get("/backtest/{symbol:path}")
async def backtest(symbol: str, period: int = Query(20, ge=5, le=100),
                   k: float = Query(2.0, ge=1.0, le=4.0), max_hold: int = Query(10, ge=1, le=60),
                   walk_filter: bool = Query(False), trades: bool = Query(False)):
    """One config of the ±kσ re-entry fade on daily history."""
    from backtest.bb_engine import simulate
    bars = await _daily(symbol)
    if len(bars) < 100:
        return {"symbol": symbol, "error": f"only {len(bars)} daily bars — need ≥100"}
    out = simulate(bars, period, k, max_hold, walk_filter)
    if not trades:
        out.pop("trades", None)
    return {"symbol": symbol, "bars": len(bars), **out}


@router.get("/sweep/{symbol:path}")
async def sweep_one(symbol: str):
    """54-config grid with 70/30 in-sample / out-of-sample split (pick by OOS)."""
    import asyncio
    from backtest.bb_engine import sweep
    bars = await _daily(symbol)
    if len(bars) < 200:
        return {"symbol": symbol, "error": f"only {len(bars)} daily bars — need ≥200"}
    return {"symbol": symbol, **await asyncio.to_thread(sweep, bars)}


@router.get("/{symbol:path}")
async def one(symbol: str):
    s = symbol.upper()
    rev = {v: k for k, v in sw.DISPLAY.items()}
    if s in rev:
        s = rev[s]
    elif "/" not in s and len(s) == 6 and s.isalpha():
        s = f"{s[:3]}/{s[3:]}"
    return await sw.analyze(s)

