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


def extract_text(content: bytes, channel: str = None) -> str:
    """Text of a sendMessage body — only when it goes to the SIGNAL channel. Bot replies
    to individual users (inbound commands) must never reach the members' feed."""
    try:
        d = json.loads(content)
    except (ValueError, AttributeError, TypeError):
        return ""
    if not isinstance(d, dict):
        return ""
    if channel is None:
        from decouple import config
        channel = config("TELEGRAM_CHAT_ID", default=None)
    if not channel or str(d.get("chat_id")) != str(channel):
        return ""
    return str(d.get("text") or "")


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


import re as _re

_FLOAT_NOISE = _re.compile(r"(?<![\w.])(-?\d+\.\d{6,})(?![\d])")


def tidy_numbers(text: str) -> str:
    """Round float noise (≥6 decimals, e.g. 3501.0123300000005) to the instrument's
    normal precision: ≥1000 → 2 dp (gold, indices, BTC), ≥10 → 3 dp (JPY pairs ~150,
    silver), else 5 dp (FX). Real FX quotes have ≤5 decimals, so they're never touched."""
    def fix(m):
        v = float(m.group(1))
        dp = 2 if abs(v) >= 1000 else 3 if abs(v) >= 10 else 5
        return f"{v:.{dp}f}"
    return _FLOAT_NOISE.sub(fix, text) if text else text


def _tidy_request(request):
    """Same Telegram sendMessage request with its text tidied (JSON or form body)."""
    import json as _json
    from urllib.parse import parse_qsl
    import httpx
    ctype = request.headers.get("content-type", "")
    raw = request.content or b""
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in ("content-length", "content-type")}
    if "json" in ctype:
        d = _json.loads(raw or b"{}")
        if isinstance(d, dict) and isinstance(d.get("text"), str):
            new = tidy_numbers(d["text"])
            if new != d["text"]:
                return httpx.Request(request.method, request.url, json={**d, "text": new}, headers=headers)
    elif "form" in ctype:
        d = dict(parse_qsl(raw.decode()))
        if "text" in d:
            new = tidy_numbers(d["text"])
            if new != d["text"]:
                return httpx.Request(request.method, request.url, data={**d, "text": new}, headers=headers)
    return request


def install() -> None:
    if _installed["done"]:
        return
    import httpx
    orig = httpx.AsyncClient.send

    async def send(self, request, *args, **kwargs):
        try:                               # one choke point: no float noise on any card
            if request.url.host == "api.telegram.org" and request.url.path.endswith("/sendMessage"):
                request = _tidy_request(request)
        except Exception:
            pass
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
