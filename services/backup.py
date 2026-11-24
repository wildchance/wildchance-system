"""Weekly backup of the research moat — never lose the signal history.

Tables: data_cache (signal tracker log, calendar + learned event moves, CME chains, backtests,
error counters, feed health, base rates …), signal_snapshots (point-in-time store),
closed_trades (journal), live_cases (sniper shots + council votes), member_signals.
build() → gzip'd JSON; send() posts it privately to the owner's Telegram chat (OWNER_CHAT_ID)
as a file every Sunday; /ops/backup downloads it on demand. Members / billing tables are NOT
included (personal data stays in Neon).
"""

from __future__ import annotations

import datetime as dt
import gzip
import json
from typing import Dict, Tuple

TABLES = ("data_cache", "signal_snapshots", "closed_trades", "live_cases", "member_signals")


async def build() -> Tuple[bytes, Dict]:
    from sqlalchemy import text
    from database.db import engine
    data, counts = {}, {}
    async with engine.connect() as conn:
        for t in TABLES:
            try:
                rows = (await conn.execute(text(f"SELECT * FROM {t}"))).mappings().all()
                data[t] = [dict(r) for r in rows]
                counts[t] = len(rows)
            except Exception as e:
                counts[t] = f"skipped ({type(e).__name__})"
    payload = {"created_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
               "tables": data}
    blob = gzip.compress(json.dumps(payload, default=str).encode("utf-8"))
    return blob, {"bytes": len(blob), "rows": counts}


async def send() -> Dict:
    import httpx
    from services.owner_notify import BOT_TOKEN, OWNER_CHAT_ID
    blob, meta = await build()
    if not (OWNER_CHAT_ID and BOT_TOKEN):
        return {"sent": False, "reason": "OWNER_CHAT_ID not set", **meta}
    name = f"wildchance-backup-{dt.date.today():%Y-%m-%d}.json.gz"
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument",
                             data={"chat_id": OWNER_CHAT_ID, "caption": f"🗄 Weekly backup · {meta['rows']}"[:1000]},
                             files={"document": (name, blob, "application/gzip")})
        ok = r.status_code == 200
        if not ok:
            from services import errors
            errors.record("telegram_fail", "backup sendDocument", f"HTTP {r.status_code}")
        return {"sent": ok, **meta}
    except Exception as e:
        from services import errors
        errors.swallow("backup.send", e)
        return {"sent": False, "reason": type(e).__name__, **meta}
