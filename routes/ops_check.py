"""ONE link that runs every owner check at once.

  GET /ops/checklist?token=…      ✅ / ⚠️ / ❌ per item + what is still missing (key NAMES only)

Each check calls the app's own endpoint in-process (scheduler token), all in parallel.
The page ALWAYS answers within ~WAIT_S seconds: checks still queued behind the
TwelveData limit (7 requests/min on the free plan) keep running in the background and
show as ⏳ — open the link again a minute later and their results are there (kept
10 min). The output is trimmed so it can be pasted back in one message.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Callable, Dict, List, Tuple

from decouple import config
from fastapi import APIRouter

router = APIRouter(prefix="/ops", tags=["ops"])

KEYS = {  # name → why (values are never shown)
    "OPS_TOKEN": "protects private pages", "BRIDGE_TOKEN": "EA → app link",
    "TELEGRAM_BOT_TOKEN": "Telegram alerts", "DATABASE_URL": "Neon database",
    "TWELVEDATA_API_KEY": "prices", "ALPHAVANTAGE_API_KEY": "news sentiment", "MARKETAUX_API_KEY": "news",
    "PAYSTACK_SECRET_KEY": "billing", "PAYSTACK_PRICE_TRADER": "plan price", "BILLING_WEBHOOK_SECRET": "billing webhook",
    "BTC_CONTRACT_SIZE": "BTC lots — using default 1; confirm in MT5 Specification",
    "INDEX_USD_PER_POINT": "index lots — using default 1; confirm in MT5 Specification",
    "STOCK_LOT_SHARES": "stock lots — using default 1; confirm in MT5 Specification",
    "OWNER_CHAT_ID": "your private Telegram chat for owner reminders (CME bulletin, outages)",
    "COINGECKO_API_KEY": "optional: crypto macro", "BLS_API_KEY": "optional: US jobs data"}


def _trim(x: Any, n: int = 600) -> Any:
    s = json.dumps(x, default=str)
    return x if len(s) <= n else s[:n] + "…"


def _judge_calendar(d):
    h, n = d.get("last_ok_h"), d.get("stored", 0)
    if h is not None and h <= 12:
        return "✅", f"{n} events stored · refreshed {h} h ago · {d.get('high_upcoming')} high-impact this week"
    if n:
        why = f"backing off {d['backing_off_min']} min after a rate limit" if d.get("backing_off_min") else \
            "no successful refresh since restart"
        return "⚠️", f"{n} stored events still in use · {why}"
    return "❌", "no calendar — Forex Factory unreachable from Render"


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
    ("live prices", "/data/snapshot?symbols=EUR/USD,USD/JPY,XAU/USD,BTC/USD", _judge_snapshot),
    ("calendar feed", "/data/calendar/status", _judge_calendar),
    ("calendar this week", "/data/calendar?days=7", lambda d: ("✅" if d.get("events") else "⚠️",
                                                              f"{len(d.get('events') or [])} blocking events")),
    ("EA link", "/bridge/status", _judge_bridge),
    ("trade journal", "/journal/stats", _judge_journal),
    ("gold sweep", "/sweep/asset/XAU/USD", _judge_ok("ok", "zones computed")),
    ("gold dealer levels", "/data/options-levels/XAU/USD", lambda d: (
        ("✅", f"{d.get('underlying')} · as of {d.get('asof') or 'live'}") if d.get("ok")
        else ("❌", d.get("reason") or "no chain"))),
    ("EUR/USD dealer levels", "/data/options-levels/EUR/USD", lambda d: (
        ("✅", f"{d.get('underlying')} · as of {d.get('asof') or 'live'}") if d.get("ok")
        else ("❌", d.get("reason") or "no chain"))),
    ("gold weekly map", "/cbdr/week/XAU/USD", _judge_ok("ok", "Monday box found")),
    ("data sources", "/data/sources", _judge_any("health table")),
    ("council agents", "/sniper/agents", _judge_any("scores table")),
]


WAIT_S = 25
KEEP_S = 600
_done: Dict[str, Tuple[float, Dict]] = {}          # path → (finished at, result)
_running: Dict[str, "asyncio.Task"] = {}


async def _run(name: str, path: str, judge: Callable) -> Dict:
    import httpx
    import main
    from services.access import INTERNAL_TOKEN
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://app",
                                     headers={"x-internal-token": INTERNAL_TOKEN}, timeout=300) as c:
            r = await c.get(path)
        if r.status_code != 200:
            res = {"check": name, "status": "❌", "note": f"HTTP {r.status_code}", "path": path}
        else:
            d = r.json()
            st, note = judge(d)
            res = {"check": name, "status": st, "note": note, "path": path, "data": _trim(d)}
    except Exception as e:
        res = {"check": name, "status": "❌", "note": f"{type(e).__name__}: {str(e)[:120]}", "path": path}
    _done[path] = (time.time(), res)
    return res


@router.get("/checklist")
async def checklist(fresh: bool = False):
    now = time.time()
    tasks = {}
    for name, path, judge in CHECKS:
        hit = _done.get(path)
        if hit and now - hit[0] < KEEP_S and not fresh:
            continue
        t = _running.get(path)
        if t is None or t.done():
            t = _running[path] = asyncio.ensure_future(_run(name, path, judge))
        tasks[path] = t
    if tasks:
        await asyncio.wait(list(tasks.values()), timeout=WAIT_S)
    results = []
    for name, path, _ in CHECKS:
        hit = _done.get(path)
        if hit and (path not in tasks or tasks[path].done()):
            results.append({**hit[1], "age_s": int(time.time() - hit[0])})
        else:
            results.append({"check": name, "status": "⏳", "path": path,
                            "note": "still running (TwelveData queue: 7 requests/min) — open the link again in ~1 min"})
    missing = {k: why for k, why in KEYS.items()
               if not (config(k, default="") or (k == "TELEGRAM_BOT_TOKEN" and config("BOT_TOKEN", default="")))}
    todo = [f"{r['check']}: {r['note']}" for r in results if r["status"] not in ("✅", "⏳")]
    todo += [f"set {k} in Render ({why})" for k, why in missing.items()]
    pending = sum(r["status"] == "⏳" for r in results)
    return {"summary": f"{sum(r['status'] == '✅' for r in results)}/{len(results)} checks ✅"
                       + (f" · {pending} still running — reload in a minute" if pending else "")
                       + f" · {len(missing)} settings missing",
            "todo": todo, "checks": results, "missing_settings": missing}
