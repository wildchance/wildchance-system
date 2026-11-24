"""ONE link that runs every owner check at once.

  GET /ops/checklist?token=…      ✅ / ⚠️ / ❌ per item + what is still missing (key NAMES only)

Each check calls the app's own endpoint in-process (scheduler token), 60 s cap each,
all in parallel; the output is trimmed so it can be pasted back in one message.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable, Dict, List, Tuple

from decouple import config
from fastapi import APIRouter

router = APIRouter(prefix="/ops", tags=["ops"])

KEYS = {  # name → why (values are never shown)
    "OPS_TOKEN": "protects private pages", "BRIDGE_TOKEN": "EA → app link",
    "TELEGRAM_BOT_TOKEN": "Telegram alerts", "DATABASE_URL": "Neon database",
    "TWELVEDATA_API_KEY": "prices", "ALPHAVANTAGE_API_KEY": "news sentiment", "MARKETAUX_API_KEY": "news",
    "PAYSTACK_SECRET_KEY": "billing", "PAYSTACK_PRICE_TRADER": "plan price", "BILLING_WEBHOOK_SECRET": "billing webhook",
    "BTC_CONTRACT_SIZE": "BTC lots", "INDEX_USD_PER_POINT": "index lots", "STOCK_LOT_SHARES": "stock lots",
    "COINGECKO_API_KEY": "optional: crypto macro", "BLS_API_KEY": "optional: US jobs data"}


def _trim(x: Any, n: int = 600) -> Any:
    s = json.dumps(x, default=str)
    return x if len(s) <= n else s[:n] + "…"


def _judge_calendar(d):
    f = d.get("fetched", 0)
    return ("✅" if f else "❌", f"{f} events fetched, {d.get('high_upcoming')} high-impact this week"
            if f else "Forex Factory unreachable from Render — fallback needed")


def _judge_journal(d):
    n = d.get("trades", 0)
    return ("✅" if n else "⚠️", f"{n} trades logged · win {d.get('win_rate_pct')}% · BE {d.get('breakeven')}"
            if n else "no trades yet — install EA v4.53")


def _judge_bridge(d):
    acc = d.get("accounts") or []
    return ("✅" if acc else "⚠️", f"{len(acc)} EA account(s) connected" if acc else "EA not connected yet")


def _judge_snapshot(d):
    p = d.get("prices") or {}
    live = [k for k, v in p.items() if v.get("price") is not None]
    ok = len(live) >= len(p) - 2
    return ("✅" if ok and d.get("vix") is not None else "⚠️",
            f"{len(live)}/{len(p)} live prices · VIX {'ok' if d.get('vix') is not None else 'missing'}")


def _judge_ok(field: str, label: str) -> Callable:
    def f(d):
        good = bool(d.get(field)) if isinstance(d, dict) else bool(d)
        return ("✅" if good else "❌", label if good else (d.get("reason") if isinstance(d, dict) else "") or "empty")
    return f


def _judge_any(label: str) -> Callable:
    return lambda d: ("✅" if d else "❌", label if d else "empty")


CHECKS: List[Tuple[str, str, Callable]] = [
    ("live prices", "/data/snapshot", _judge_snapshot),
    ("calendar refresh", "/data/calendar/refresh", _judge_calendar),
    ("calendar this week", "/data/calendar?days=7", lambda d: ("✅" if d.get("events") else "⚠️",
                                                              f"{len(d.get('events') or [])} blocking events")),
    ("EA link", "/bridge/status", _judge_bridge),
    ("trade journal", "/journal/stats", _judge_journal),
    ("gold sweep", "/sweep/asset/XAU/USD", _judge_ok("ok", "zones computed")),
    ("gold dealer levels", "/data/options-levels/XAU/USD", _judge_ok("ok", "CBOE chain read")),
    ("gold weekly map", "/cbdr/week/XAU/USD", _judge_ok("ok", "Monday box found")),
    ("data sources", "/data/sources", _judge_any("health table")),
    ("council agents", "/sniper/agents", _judge_any("scores table")),
]


@router.get("/checklist")
async def checklist():
    import httpx
    import main
    from services.access import INTERNAL_TOKEN
    transport = httpx.ASGITransport(app=main.app)

    async def run(name, path, judge):
        try:
            async with httpx.AsyncClient(transport=transport, base_url="http://app",
                                         headers={"x-internal-token": INTERNAL_TOKEN}, timeout=60) as c:
                r = await c.get(path)
            if r.status_code != 200:
                return {"check": name, "status": "❌", "note": f"HTTP {r.status_code}", "path": path}
            d = r.json()
            st, note = judge(d)
            return {"check": name, "status": st, "note": note, "path": path, "data": _trim(d)}
        except Exception as e:
            return {"check": name, "status": "❌", "note": f"{type(e).__name__}: {str(e)[:120]}", "path": path}

    results = await asyncio.gather(*(run(*c) for c in CHECKS))
    missing = {k: why for k, why in KEYS.items()
               if not (config(k, default="") or (k == "TELEGRAM_BOT_TOKEN" and config("BOT_TOKEN", default="")))}
    todo = [f"{r['check']}: {r['note']}" for r in results if r["status"] != "✅"]
    todo += [f"set {k} in Render ({why})" for k, why in missing.items()]
    return {"summary": f"{sum(r['status'] == '✅' for r in results)}/{len(results)} checks ✅ · "
                       f"{len(missing)} settings missing",
            "todo": todo, "checks": results, "missing_settings": missing}
