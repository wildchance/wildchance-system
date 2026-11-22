"""Members' signal feed — every Telegram post the system makes, captured at the HTTP layer.

Installed like the TwelveData limiter: wraps httpx.AsyncClient.send and, for successful
``api.telegram.org/…/sendMessage`` calls, stores the text (system/health messages are
skipped) so paying members see the same signals on the website.
"""

from __future__ import annotations

import json
import re
from typing import Dict, List

_SKIP = re.compile(r"outage|data stale|stale|not configured|test", re.I)
_installed = {"done": False}


def kind_of(text: str) -> str:
    t = text.lower()
    for k, pat in (("z-setup", "z-setup"), ("swing", "swing fade"), ("cbdr", "cbdr"),
                   ("gold", "xau"), ("digest", "digest")):
        if pat in t:
            return k
    return "other"


def extract_text(content: bytes) -> str:
    try:
        return str(json.loads(content).get("text") or "")
    except (ValueError, AttributeError):
        return ""


async def record(text: str) -> None:
    if not text or _SKIP.search(text[:160]):
        return
    try:
        from database.db import AsyncSessionLocal
        from models.signal_feed_model import MemberSignal
        async with AsyncSessionLocal() as db:
            db.add(MemberSignal(kind=kind_of(text), text=text[:4000]))
            await db.commit()
    except Exception:
        pass


async def recent(db, limit: int = 50) -> List[Dict]:
    from sqlalchemy import select
    from models.signal_feed_model import MemberSignal
    rows = (await db.execute(select(MemberSignal).order_by(MemberSignal.id.desc())
                             .limit(limit))).scalars().all()
    return [{"at": r.created_at.isoformat() if r.created_at else None, "kind": r.kind,
             "text": r.text} for r in rows]


def install() -> None:
    if _installed["done"]:
        return
    import httpx
    orig = httpx.AsyncClient.send

    async def send(self, request, *args, **kwargs):
        resp = await orig(self, request, *args, **kwargs)
        try:
            if request.url.host == "api.telegram.org" and request.url.path.endswith("/sendMessage") \
                    and resp.status_code == 200:
                await record(extract_text(request.content))
        except Exception:
            pass
        return resp

    httpx.AsyncClient.send = send
    _installed["done"] = True
