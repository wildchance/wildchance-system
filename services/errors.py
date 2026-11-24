"""Make failures LOUD — one place that counts what went wrong and what ran slow.

Sources
  http_500      any request that crashed (middleware in main.py)
  slow_request  any request slower than SLOW_REQUEST_S (20 s)
  cron_fail     a scheduled call that returned ≥ 400 or raised (routes/cron.py)
  cron_slow     a scheduled call slower than SLOW_CRON_S (120 s)
  telegram_fail a Telegram send that did not go through
  swallowed     errors caught inside key engines (signal tracker, council gate, snapshots,
                regime, FX rotation, sniper …) via errors.swallow(where, exc)
Counters are per UTC day (persisted to Neon 'errors:YYYY-MM-DD'); the last 300 events stay in
memory for /ops/errors. A daily digest goes privately to the owner (05:50 UTC) and the checklist
and terminal show today's count.
"""

from __future__ import annotations

import collections
import datetime as dt
import time
import traceback
from typing import Dict, Optional

from decouple import config

SLOW_REQUEST_S = float(config("SLOW_REQUEST_S", default="20"))
SLOW_CRON_S = float(config("SLOW_CRON_S", default="120"))
_recent: "collections.deque[Dict]" = collections.deque(maxlen=300)
_counts: Dict[str, Dict[str, int]] = {}            # day → "kind|where" → n
_dirty = {"n": 0}


def _day(ts: Optional[float] = None) -> str:
    return dt.datetime.fromtimestamp(ts or time.time(), dt.timezone.utc).strftime("%Y-%m-%d")


def record(kind: str, where: str, detail: str = "", ms: Optional[float] = None) -> None:
    try:
        now = time.time()
        _recent.append({"t": now, "kind": kind, "where": where[:120], "detail": (detail or "")[:240],
                        "ms": round(ms) if ms is not None else None})
        day = _counts.setdefault(_day(now), {})
        k = f"{kind}|{where[:120]}"
        day[k] = day.get(k, 0) + 1
        _dirty["n"] += 1
        for d in list(_counts)[:-3]:                   # keep 3 days in memory
            _counts.pop(d, None)
    except Exception:
        pass


def swallow(where: str, exc: BaseException) -> None:
    """Use inside an except block that must not crash the caller."""
    tb = traceback.extract_tb(exc.__traceback__)[-1:] if exc.__traceback__ else []
    loc = f" @ {tb[0].filename.split('/')[-1]}:{tb[0].lineno}" if tb else ""
    record("swallowed", where, f"{type(exc).__name__}: {exc}{loc}")


def summary(day: Optional[str] = None) -> Dict:
    day = day or _day()
    c = _counts.get(day, {})
    by_kind: Dict[str, int] = {}
    for k, n in c.items():
        kind = k.split("|", 1)[0]
        by_kind[kind] = by_kind.get(kind, 0) + n
    top = sorted(c.items(), key=lambda kv: -kv[1])[:12]
    return {"day": day, "total": sum(c.values()), "by_kind": by_kind,
            "top": [{"kind": k.split("|", 1)[0], "where": k.split("|", 1)[1], "n": n} for k, n in top]}


def recent(n: int = 50, kind: Optional[str] = None):
    rows = [r for r in _recent if not kind or r["kind"] == kind]
    return list(rows)[-n:][::-1]


async def persist(force: bool = False) -> None:
    if not force and _dirty["n"] < 20:
        return
    try:
        from services import data_store
        for day, c in _counts.items():
            await data_store.put(f"errors:{day}", c)
        _dirty["n"] = 0
    except Exception:
        pass


async def load_day(day: str) -> Dict[str, int]:
    if day in _counts:
        return _counts[day]
    try:
        from services import data_store
        v, _ = await data_store.get(f"errors:{day}")
        return dict(v or {})
    except Exception:
        return {}


def digest_text(day: str, counts: Dict[str, int], failing_sources) -> str:
    total = sum(counts.values())
    lines = [f"🩺 Daily health — {day}: {total} problem events"]
    if not total and not failing_sources:
        lines.append("No crashes, failed jobs, slow calls or failed sends. 👍")
        return "\n".join(lines)
    for k, n in sorted(counts.items(), key=lambda kv: -kv[1])[:10]:
        kind, where = k.split("|", 1)
        lines.append(f"• {kind} × {n} — {where}")
    if failing_sources:
        lines.append("Failing data sources: " + ", ".join(failing_sources))
    lines.append("Details: /ops/errors")
    return "\n".join(lines)
