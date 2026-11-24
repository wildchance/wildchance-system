"""WildChance TERMINAL — one private screen with everything (owner / ops token).

  GET /terminal?token=…        the page (dark, auto-refresh 60 s, phone friendly)
  GET /terminal/data?token=…   one JSON with every tile (cached reads — no paid calls)
"""

import asyncio
import datetime as dt
import os

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(prefix="/terminal", tags=["terminal"])
WATCH = ["EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "USD/CAD", "AUD/USD", "XAU/USD", "GBP/JPY", "EUR/JPY"]
OPTION_SYMS = ["XAU/USD", "EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "USD/CAD", "AUD/USD", "NAS100", "SPX500", "US30"]
_PAGE = os.path.join(os.path.dirname(__file__), "..", "static", "terminal", "index.html")


async def _safe(coro, default=None):
    try:
        return await asyncio.wait_for(coro, timeout=25)
    except Exception:
        return default


@router.get("", response_class=HTMLResponse)
async def page():
    with open(_PAGE, encoding="utf-8") as f:
        return HTMLResponse(f.read())


@router.get("/data")
async def data():
    return await build()


async def build() -> dict:
    from services import data_store, econ_calendar as ec, fx_rotation as fx, regime, signal_tracker as stk
    from services import backtest as bt
    from services.data_feeds import macro_cached
    from services.options_levels import run as opt_run
    await _safe(ec.ensure_loaded())
    st = await _safe(fx.strength(), {})
    rot = await _safe(fx.rotation(), {})
    rows = await _safe(stk._load(), []) or []
    open_rows = [r for r in rows if r["result"] == "open"]
    regimes = {}
    for s in WATCH:
        regimes[s] = await _safe(regime.compute(s), {})
    from services.ohlc_service import fetch_ohlc_raw
    walls = {}
    for s in OPTION_SYMS:
        bars = await _safe(fetch_ohlc_raw(s, "1h", 2), []) or []
        live = bars[-1]["close"] if bars else None              # map futures strikes onto live spot
        o = await _safe(opt_run(s, live), {}) or {}
        if o.get("ok"):
            walls[s] = {"source": o.get("underlying"), "asof": o.get("asof"), "pc": o.get("put_call_ratio"),
                        "live": live,
                        "levels": [{"label": x["label"], "price": x["price"]} for x in o.get("levels", [])
                                   if any(w in x["label"] for w in ("ceiling", "hedge wall", "floor", "shelf", "pain"))]}
    feed = None
    try:
        from services.wildchance_service import get_latest_feed
        feed = await _safe(get_latest_feed(), None)
    except Exception:
        pass
    positioning = sorted([{k: s.get(k) for k in ("pair", "verdict", "confidence", "retail_long", "cot_rank", "cot_agrees")}
                          for s in (feed or {}).get("signals") or []], key=lambda x: -(x.get("confidence") or 0))
    backtests = {}
    for eng in ("z", "asia"):
        for s in WATCH:
            v = await _safe(bt.edge(eng, s), None)
            if v and v.get("ok"):
                backtests.setdefault(eng, []).append({k: v.get(k) for k in ("symbol", "trades", "win_rate_pct",
                                                                            "expectancy_r", "max_drawdown_r")}
                                                     | {"stable": (v.get("walk_forward") or {}).get("stable")})
    audit, _ = await _safe(data_store.get("data_audit_last"), (None, None)) or (None, None)
    m = macro_cached()
    return {"as_of": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "macro": {"vix": m.get("vix"), "yields": m.get("us_yields")},
            "strength": st.get("strength"), "pairings": st.get("pairings"), "rotation": rot,
            "exposure": fx.net_exposure(open_rows), "exposure_cap": fx.EXPOSURE_MAX,
            "open_signals": [{k: r.get(k) for k in ("symbol", "side", "entry", "stop", "targets", "source", "hits",
                                                    "sent_at")} for r in open_rows][-20:],
            "signal_stats": stk.summarise(rows), "regimes": regimes, "walls": walls,
            "calendar": [{k: e.get(k) for k in ("time_utc", "ccy", "title", "forecast", "previous", "blocks")}
                         for e in ec.upcoming(3, high_only=True)][:12],
            "positioning": positioning, "backtests": backtests,
            "audit": {"summary": (audit or {}).get("summary"), "as_of": (audit or {}).get("as_of"),
                      "issues": [i for i in (audit or {}).get("items", []) if i.get("status") != "✅"][:12]}}
