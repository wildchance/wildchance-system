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
            if n else "no trades yet — install EA v4.59")


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
    ("calendar this week", "/data/calendar?days=7", lambda d: (
        ("✅", f"{len(d['events'])} blocking events ahead") if d.get("events")
        else ("✅", "no high-impact events left in the stored week — next week loads when Forex Factory "
                    "publishes it (Thu–Fri), or paste it to the bot"))),
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
    ("Shanghai (SHFE)", "/ops/shfe", lambda d: (
        ("✅", " · ".join(d.get("lines", [])[1:]) or f"settlement {d.get('day')}") if d.get("ok")
        else ("⚠️", d.get("reason") or "not reachable"))),
    ("AI (NVIDIA)", "/ops/ai?test=true", lambda d: (
        ("✅", f"{(d.get('last') or {}).get('model')} answering") if (d.get("last") or {}).get("ok")
        else ("❌", (d.get("hint") or str((d.get("last") or {}).get("error") or "no reply"))[:200]))),
    ("database", "/health", lambda d: (
        ("✅", f"{d.get('db_backend') or d.get('database') or 'postgresql'} connected")
        if (d.get("db_backend") or "postgresql") != "sqlite" and d.get("db_reachable", True) is not False
        else ("❌", "no DATABASE_URL on this service — local SQLite, wiped on every deploy (accounts, journal, "
                    "settings lost). Set DATABASE_URL to Neon."))),
    ("telegram bot", "/ops/telegram", lambda d: (
        ("✅", f"{d.get('bot')} webhook OK") if d.get("ok")
        else ("❌", "; ".join(d.get("problems") or [d.get("reason") or "not reachable"])[:200]))),
    ("data blackout", "/ops/freshness", lambda d: (
        ("✅", "every watched market priced within the limit") if not d.get("dark")
        else ("⏳", "app just woke up — prices arrive with the next scans") if d.get("uptime_min", 99) < 30
        else ("❌", f"no price > {d.get('limit_min'):.0f} min: {', '.join(d['dark'])}"))),
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



@router.get("/freshness")
async def freshness_view():
    """Data-blackout check: newest real price per watched market (EA / TwelveData / pool)."""
    from services import freshness
    up = (time.time() - freshness.BOOT) / 60
    rep = freshness.blackouts()
    rep["uptime_min"] = round(up, 1)
    return rep


@router.get("/ai")
async def ai_status(test: bool = False):
    """NVIDIA AI: is the key set, did the last call work, and why not (key never shown)."""
    from services import ai_sentiment as ai
    if test:
        ai._down_until["t"] = 0.0                         # a manual test always really calls NVIDIA
        await ai.ask_model([{"role": "user", "content": "Reply with the single word: ok"}])
    hint = None
    st, err = ai.LAST.get("status"), str(ai.LAST.get("error") or "")
    if st == "no key":
        hint = "add NVIDIA_API_KEY in Render → Environment, then redeploy (the key is read at start-up)"
    elif st in (401, 403):
        hint = "NVIDIA rejected the key — create a new one at build.nvidia.com and replace it in Render"
    elif st == 404:
        hint = "model not available — set NVIDIA_MODEL to a model your build.nvidia.com account lists"
    elif st == 429:
        hint = "NVIDIA rate / credit limit — check credits at build.nvidia.com"
    return {"key_set": bool(ai.NVIDIA_API_KEY), "model": ai.NVIDIA_MODEL, "last": ai.LAST, "hint": hint}


@router.get("/telegram")
async def telegram_status():
    """Why the bot is silent: Telegram's own view of our webhook (token never shown)."""
    from routes import telegram_routes as tr
    if tr.application is None:
        return {"ok": False, "reason": "TELEGRAM_BOT_TOKEN / BOT_TOKEN not set on this server — bot disabled"}
    try:
        info = await tr.application.bot.get_webhook_info()
        me = await tr.application.bot.get_me()
    except Exception as e:
        return {"ok": False, "reason": f"Telegram API unreachable or token rejected: {type(e).__name__}: {e}"[:200]}
    want = tr.webhook_target()
    me_url = (tr.WEBHOOK_BASE_URL or "").rstrip("/")
    import datetime as _dt
    last = info.last_error_date
    problems = []
    if not info.url:
        problems.append("no webhook registered — POST /ops/telegram/reset")
    elif want and info.url != want:
        problems.append(f"webhook points to ANOTHER service ({info.url}) — two Render services share this bot "
                        "token; suspend one, or set TELEGRAM_WEBHOOK_URL to the main URL on both")
    if info.last_error_message:
        problems.append(f"Telegram's last delivery error: {info.last_error_message}")
    if (info.pending_update_count or 0) > 5:
        problems.append(f"{info.pending_update_count} messages waiting — Telegram cannot deliver them")
    return {"ok": not problems, "bot": f"@{me.username}", "webhook_url": info.url, "expected_url": want,
            "pending_updates": info.pending_update_count,
            "last_error": info.last_error_message,
            "last_error_at": last.astimezone(_dt.timezone.utc).isoformat() if last else None,
            "this_service": me_url or None, "pinned": bool(tr.PINNED_WEBHOOK),
            "secret_set": bool(tr.WEBHOOK_SECRET), "problems": problems}


@router.post("/telegram/reset")
async def telegram_reset():
    """Re-register the webhook (keeps pending messages so nothing you sent is lost)."""
    from routes import telegram_routes as tr
    if tr.application is None or not tr.webhook_target():
        return {"ok": False, "reason": "bot token or WEBHOOK_BASE_URL / RENDER_EXTERNAL_URL missing"}
    r = await tr.register_webhook(tr.application.bot, tr.webhook_target())
    return {"ok": r != "failed (flood control)", "result": r, "webhook_url": tr.webhook_target()}


@router.post("/treasury/check")
async def treasury_check():
    """Hourly: watch-only treasury balances; owner alert on any incoming / outgoing move."""
    from services import treasury
    v = await treasury.load()
    if not v["addresses"]:
        return {"skipped": "no addresses"}
    rep = await treasury.snapshot(alert=True)
    return {"total_usd": rep["total_usd"], "alerts": rep["alerts"], "alert_sent": rep["alert_sent"]}


@router.get("/shfe")
async def shfe_view():
    """Shanghai Futures Exchange: gold premium vs spot, copper pulse (experimental)."""
    from services import shfe
    rep = await shfe.report()
    return {**rep, "lines": shfe.lines(rep)}


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
        srcs = df.status()["sources"]
        failing = sorted(s for s, h in srcs.items() if h.get("score", 1) < 0.5)
        reasons = {s: str(srcs[s].get("last_err") or "") for s in failing}
    except Exception:
        failing, reasons = [], {}
    text = errors.digest_text(day, counts, failing, await errors.load_details(day), reasons)
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
