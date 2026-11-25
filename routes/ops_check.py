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
from typing import Any, Callable, Dict, List, Optional, Tuple

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
            if n else "no trades yet — install EA v4.54")


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
    ("data audit (options, CME, retail)", "/ops/data-audit", lambda d: (
        "✅" if "❌ 0" in d.get("summary", "") and "⚠️ 0" in d.get("summary", "")
        else "❌" if "❌ 0" not in d.get("summary", "") else "⚠️", d.get("summary", ""))),
    ("council agents", "/sniper/agents", _judge_any("scores table")),
    ("database schema", "/ops/schema", lambda d: (
        "✅" if (d.get("drift") or {}).get("ok") else "❌" if (d.get("drift") or {}).get("ok") is False else "⚠️",
        "in sync" if (d.get("drift") or {}).get("ok") else f"missing: {(d.get('drift') or {}).get('missing')}"
        if (d.get("drift") or {}).get("ok") is False else str((d.get("drift") or {}).get("error"))[:120])),
    ("errors today", "/ops/errors", lambda d: (
        "✅" if not (d.get("today") or {}).get("total") else "⚠️" if (d["today"]["total"] < 20) else "❌",
        f"{(d.get('today') or {}).get('total', 0)} problem events · "
        + ", ".join(f"{k} {v}" for k, v in ((d.get('today') or {}).get('by_kind') or {}).items()))),
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



@router.get("/data-audit")
async def data_audit(notify: bool = False):
    """Weekly audit of the options / CME / retail / calendar inputs (also sent to the owner)."""
    from services import data_audit as da
    rep = await da.run()
    if notify:
        from services.owner_notify import send
        rep["sent"] = await send(da.message(rep))
    return rep



@router.get("/errors")
async def errors_view(kind: Optional[str] = None, n: int = 50):
    """Crashes, failed / slow jobs, slow requests, failed Telegram sends, swallowed engine errors."""
    from services import errors
    return {"today": errors.summary(), "recent": errors.recent(n, kind)}


@router.get("/errors/digest")
async def errors_digest(notify: bool = False):
    """Yesterday's problem events (+ failing data sources) — privately to the owner each morning."""
    import datetime as _dt
    from services import data_feeds as df, errors
    await errors.persist(force=True)
    day = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=1)).strftime("%Y-%m-%d")
    counts = await errors.load_day(day)
    try:
        await df.ensure_loaded()
        failing = sorted(s for s, h in df.status()["sources"].items() if h.get("score", 1) < 0.5)
    except Exception:
        failing = []
    text = errors.digest_text(day, counts, failing)
    out = {"day": day, "total": sum(counts.values()), "failing_sources": failing, "text": text}
    if notify:
        from services.owner_notify import send
        out["sent"] = await send(text)
    return out



@router.get("/backup")
async def backup(send: bool = False):
    """gzip'd JSON of the research tables (download), or ?send=true → owner's Telegram."""
    from fastapi.responses import Response
    from services import backup as bk
    if send:
        return await bk.send()
    blob, meta = await bk.build()
    import datetime as _dt
    return Response(blob, media_type="application/gzip", headers={
        "Content-Disposition": f'attachment; filename="wildchance-backup-{_dt.date.today():%Y-%m-%d}.json.gz"',
        "X-Backup-Rows": str(meta["rows"])})


@router.get("/schema")
async def schema():
    """Applied / pending migrations + model columns missing from the live database."""
    from database import migrations
    from database.db import engine
    return {"migrations": await migrations.status(engine), "drift": await migrations.drift(engine)}



@router.get("/deals/check")
async def deals_check():
    from services import deal_tracker
    return await deal_tracker.check(notify=True)



@router.get("/scout")
async def hardware_scout_run():
    from services import hardware_scout
    return await hardware_scout.scout(notify=True)
