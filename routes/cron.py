"""In-app scheduler endpoints (replaces GitHub's unreliable cron as the primary clock).

  GET|POST /cron/tick            run every job due since the last tick (call every 5 min)
  GET|POST /cron/run?job=<name>  run one job now (ignores the slot de-dup) — manual test
  GET      /cron/jobs            the schedule, with each job's next run in UTC
  GET      /cron/status          last ticks: jobs run, HTTP codes, failures

Auth: CRON_TOKEN (falls back to BRIDGE_TOKEN) as ?token= or the X-Cron-Token header.
Ticks answer immediately and run jobs in the background (cron-job.org free = 30 s
timeout). If most calls in a tick fail (data outage), Telegram gets ONE warning per 6h.
"""

from __future__ import annotations

import datetime as dt
from collections import deque
from typing import Dict, List, Optional

import httpx
from decouple import config
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Query

from services import cron_schedule as cs

router = APIRouter(prefix="/cron", tags=["cron"])

TOKEN = (config("CRON_TOKEN", default=None) or config("BRIDGE_TOKEN", default=None))
HISTORY: deque = deque(maxlen=30)
_running = {"busy": False}


def _auth(token: Optional[str]):
    if not TOKEN:
        raise HTTPException(503, "cron disabled — set CRON_TOKEN (or BRIDGE_TOKEN)")
    if token != TOKEN:
        raise HTTPException(403, "bad cron token")


async def _claim(slot: str) -> bool:
    """Insert the slot row; False if another tick already ran it. No DB → run anyway."""
    try:
        from database.db import AsyncSessionLocal
        from models.cron_model import CronRun
        async with AsyncSessionLocal() as db:
            db.add(CronRun(slot=slot))
            await db.commit()
        return True
    except Exception as e:
        return "unique" not in str(e).lower() and "duplicate" not in str(e).lower()


async def _call_all(calls, now: Optional[dt.datetime] = None) -> List[Dict]:
    from main import app
    out = []
    now = now or dt.datetime.now(dt.timezone.utc)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://app", timeout=900) as c:
        for method, path in calls:
            held = cs.hold_reason(path, now)
            if held:                                   # central trade gate (owner's rules)
                out.append({"call": f"{method} {cs.redact(path)}", "code": 0,
                            "detail": f"held: {held}"})
                continue
            try:
                r = await c.request(method, path)
                code, body = r.status_code, r.text[:160]
            except Exception as e:
                code, body = 599, f"{type(e).__name__}: {e}"[:160]
            out.append({"call": f"{method} {cs.redact(path)}", "code": code,
                        "detail": body if code >= 400 else None})
    return out


async def _run_jobs(items, now: dt.datetime, forced: bool = False) -> Dict:
    ran = []
    for slot, job in items:
        if not forced and not await _claim(slot):
            continue
        ran.append({"slot": slot, "results": await _call_all(job.calls, now)})
    rec = {"tick_utc": now.isoformat(timespec="seconds"), "jobs": ran}
    HISTORY.appendleft(rec)
    calls = [r for j in ran for r in j["results"] if r["code"] != 0]   # executed only
    failed = [r for r in calls if r["code"] >= 500]
    if len(calls) >= 4 and len(failed) / len(calls) >= 0.6:
        from services.realtime import alert_once
        sample = "\n".join(f"• {r['call']} → {r['code']}" for r in failed[:6])
        await alert_once("cron-outage",
                         f"⚠️ *WildChance outage*: {len(failed)}/{len(calls)} scheduled calls "
                         f"failed at {rec['tick_utc']}.\n{sample}\nMost likely the market-data "
                         f"feed (EA not pushing / TwelveData limit). Check /health and /cron/status.",
                         ttl_hours=6)
    return rec


async def _freshness_alert() -> Dict:
    from services.freshness import gold_status
    st = await gold_status()
    if st["status"] == "STALE":
        from services.realtime import alert_once
        await alert_once("data-stale",
                         f"🔴 *Data STALE*: newest gold price is {st['age_min']} min old while the "
                         f"market is open (limit {st['stale_after_min']:.0f}). Every signal is blind "
                         f"until the EA feed / TwelveData recovers. Sources: {st['sources']}",
                         ttl_hours=3)
    return st


async def _tick_bg(items, now):
    if _running["busy"]:
        return
    _running["busy"] = True
    try:
        await _run_jobs(items, now)
        try:
            HISTORY[0]["data"] = await _freshness_alert()
        except Exception:
            pass
    finally:
        _running["busy"] = False


@router.api_route("/tick", methods=["GET", "POST"])
async def tick(background: BackgroundTasks, token: Optional[str] = Query(None),
               x_cron_token: Optional[str] = Header(None)):
    _auth(token or x_cron_token)
    now = dt.datetime.now(dt.timezone.utc)
    items = cs.due(now)
    if _running["busy"]:
        return {"accepted": False, "reason": "previous tick still running", "due": len(items)}
    background.add_task(_tick_bg, items, now)
    return {"accepted": True, "now_utc": now.isoformat(timespec="seconds"),
            "due": [k for k, _ in items]}


@router.api_route("/run", methods=["GET", "POST"])
async def run(background: BackgroundTasks, job: str = Query(...),
              token: Optional[str] = Query(None), x_cron_token: Optional[str] = Header(None)):
    _auth(token or x_cron_token)
    match = [j for j in cs.jobs() if j.name == job]
    if not match:
        raise HTTPException(404, f"unknown job {job!r} — see /cron/jobs")
    now = dt.datetime.now(dt.timezone.utc)
    background.add_task(_run_jobs, [(f"{job}@manual", match[0])], now, True)
    return {"accepted": True, "job": job, "calls": len(match[0].calls)}


@router.get("/jobs")
async def list_jobs():
    now = dt.datetime.now(dt.timezone.utc)
    rows = []
    for j in cs.jobs():
        nxt = None
        for h in range(1, 24 * 40 + 1):                 # next slot within ~40 days
            s = cs.slots(j, now + dt.timedelta(hours=h), lookback_min=60)
            if s:
                nxt = min(s).isoformat(timespec="minutes")
                break
        rows.append({"job": j.name, "tz": j.tz, "minute": j.minute,
                     "hours": list(j.hours) if j.hours else "every", "next_utc": nxt,
                     "calls": [f"{m} {cs.redact(p)}" for m, p in j.calls]})
    rows.sort(key=lambda r: r["next_utc"] or "9")
    return {"now_utc": now.isoformat(timespec="minutes"), "jobs": rows}


@router.get("/status")
async def status():
    return {"busy": _running["busy"], "token_set": bool(TOKEN), "ticks": list(HISTORY)}
