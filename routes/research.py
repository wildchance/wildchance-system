"""Research layer (ops token).

  GET /research/backtest?engine=z&symbol=EUR/USD   walk-forward backtest (stored)
  GET /research/backtest/all?engine=z               the whole watch list (weekly job)
  GET /research/snapshots?limit=200&source=z-setup  point-in-time signal store export
  GET /research/council                             weighted-council calibration
"""

from typing import Optional

from fastapi import APIRouter, Query

from services import backtest as bt

router = APIRouter(prefix="/research", tags=["research"])
WATCH = ["EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "USD/CAD", "AUD/USD", "XAU/USD", "EUR/JPY", "GBP/JPY"]


@router.get("/backtest")
async def backtest(engine: str = Query("z"), symbol: str = Query("EUR/USD")):
    return await bt.run(engine, symbol)


@router.get("/backtest/all")
async def backtest_all(engine: str = Query("z"), notify: bool = Query(False)):
    out = []
    for s in WATCH:
        try:
            r = await bt.run(engine, s)
        except Exception as e:
            r = {"ok": False, "symbol": s, "reason": type(e).__name__}
        out.append({k: r.get(k) for k in ("symbol", "ok", "trades", "win_rate_pct", "expectancy_r", "profit_factor",
                                          "max_drawdown_r", "sharpe", "reason")}
                   | {"stable": (r.get("walk_forward") or {}).get("stable")})
    if notify:
        from services.realtime import alert_once
        import datetime as dt
        good = [o for o in out if o.get("ok") and (o.get("expectancy_r") or 0) > 0 and o.get("stable")]
        lines = [f"🔬 *Backtest — {engine} engine* (walk-forward, 4 folds)"]
        for o in sorted([o for o in out if o.get("ok") and o.get("trades")], key=lambda o: -(o["expectancy_r"] or 0)):
            mark = "✅" if o in good else "⚠️" if (o["expectancy_r"] or 0) > 0 else "❌"
            lines.append(f"{mark} {o['symbol']}: {o['trades']} trades · win {o['win_rate_pct']}% · "
                         f"{o['expectancy_r']:+.2f}R/trade · DD {o['max_drawdown_r']}R"
                         + (" · stable" if o.get("stable") else ""))
        await alert_once(f"bt:{engine}:{dt.date.today():%G-%V}", "\n".join(lines), ttl_hours=24 * 6)
    return {"engine": engine, "results": out}


@router.get("/snapshots")
async def snapshots(limit: int = Query(200, ge=1, le=2000), source: Optional[str] = Query(None)):
    from services import snapshots as sn
    return {"rows": await sn.export(limit, source)}


@router.get("/council")
async def council_calibration():
    from services import council_gate
    from services import council as cn
    scores = await council_gate._agent_scores()
    return {"min_prob": cn.MIN_PROB, "prob_min_cases": cn.PROB_MIN_CASES, "agents": scores}
