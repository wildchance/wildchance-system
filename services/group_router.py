"""Group router — every card posted to the main channel is COPIED to the right group / topic.

Configure in Render (values are chat ids, optionally ':topic' for a forum topic thread):
  GROUP_ROUTES = {"gold": "-1001234567890:12", "fx": "-1001234567890:15", "crypto": "-1009876543210",
                  "indices": "-1001234567890:20", "dxy": "-1001234567890:22", "general": "-1001234567890:3",
                  "results": "-1005555555555", "all": ""}
Get the numbers with /chatid — add the bot as admin in the group, type /chatid inside the topic.

How it works: install() wraps the HTTP client once. When a sendMessage / sendPhoto to the main
channel (TELEGRAM_CHAT_ID) succeeds, the text / caption is classified by the markets it names:
  gold (XAU, XAG, GOLD) · crypto (BTC, ETH, SOL, …) · indices (NAS100, US30, SPX500, US500, US100)
  · dxy · fx (any XXX/YYY or XXXYYY currency pair)
Exactly one market family → that route; several families or none → "general"; flash-card photos
and track-record reports → "results" too; "all" receives everything. Photos are re-sent by
Telegram file_id (no re-upload). Copies run in the background and never delay the main post.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Dict, List, Optional, Set, Tuple

from decouple import config

_installed = {"done": False}
_CRYPTO = ("BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "ADA", "LTC")
_FIAT = ("USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD", "CNH")
_IDX = ("NAS100", "US30", "SPX500", "US500", "US100", "NDX", "SPX", "DOW", "US2000")


def routes() -> Dict[str, Tuple[str, Optional[int]]]:
    raw = config("GROUP_ROUTES", default="") or ""
    try:
        data = json.loads(raw) if raw.strip() else {}
    except ValueError:
        return {}
    out = {}
    for k, v in data.items():
        v = str(v or "").strip()
        if not v:
            continue
        chat, _, thread = v.partition(":")
        out[k.lower()] = (chat, int(thread) if thread.isdigit() else None)
    return out


def families(text: str) -> Set[str]:
    t = (text or "").upper()
    fam: Set[str] = set()
    if re.search(r"\b(XAU|XAG|GOLD)\b|XAU/?USD|XAG/?USD", t):
        fam.add("gold")
    if re.search(r"\b(" + "|".join(_CRYPTO) + r")(/?USDT?|USD)?\b", t):
        fam.add("crypto")
    if re.search(r"\b(" + "|".join(_IDX) + r")\b", t):
        fam.add("indices")
    if re.search(r"\bDXY\b", t):
        fam.add("dxy")
    for a, b in re.findall(r"\b([A-Z]{3})/?([A-Z]{3})\b", t):
        if a in _FIAT and b in _FIAT and a != b:
            fam.add("fx")
            break
    return fam


def destinations(text: str, is_result: bool, table: Dict[str, Tuple[str, Optional[int]]]) -> List[Tuple[str, Optional[int]]]:
    fam = families(text)
    keys = [next(iter(fam))] if len(fam) == 1 else ["general"]
    if is_result:
        keys.append("results")
    keys.append("all")
    seen, out = set(), []
    for k in keys:
        d = table.get(k)
        if d and d not in seen:
            seen.add(d)
            out.append(d)
    return out


def is_result_text(text: str) -> bool:
    t = text or ""
    return bool(re.search(r"\+\d+(\.\d+)? pips|Signals followed|Track record|Trade journal", t))


async def _copy(method: str, body: Dict, dests, token_url: str) -> None:
    import httpx
    from services.access import INTERNAL_TOKEN  # noqa: F401  (keeps import cheap / validates app context)
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            for chat, thread in dests:
                payload = {**body, "chat_id": chat}
                if thread:
                    payload["message_thread_id"] = thread
                r = await c.post(f"{token_url}/{method}", json=payload, headers={"x-group-copy": "1"})
                if r.status_code == 400 and "parse" in r.text.lower() and "text" in payload:
                    payload.pop("parse_mode", None)
                    await c.post(f"{token_url}/{method}", json=payload, headers={"x-group-copy": "1"})
                elif r.status_code != 200:
                    from services import errors
                    errors.record("telegram_fail", f"group copy {chat}:{thread or ''}", f"HTTP {r.status_code} {r.text[:120]}")
    except Exception as e:
        from services import errors
        errors.swallow("group_router.copy", e)


def install() -> None:
    if _installed["done"]:
        return
    import httpx
    orig = httpx.AsyncClient.send
    if getattr(orig, "_wc_group_router", False):        # already wrapped (never mirror twice)
        _installed["done"] = True
        return

    async def send(self, request, *args, **kwargs):
        resp = await orig(self, request, *args, **kwargs)
        try:
            url = str(request.url)
            if "api.telegram.org/bot" in url and request.headers.get("x-group-copy") != "1" and \
                    (url.endswith("/sendMessage") or url.endswith("/sendPhoto")) and resp.status_code == 200:
                _maybe_mirror(url, request, resp)
        except Exception:
            pass
        return resp

    send._wc_group_router = True
    httpx.AsyncClient.send = send
    _installed["done"] = True


def _maybe_mirror(url: str, request, resp) -> None:
    from services.telegram_service import CHAT_ID
    table = routes()
    if not table or not CHAT_ID:
        return
    data = resp.json().get("result") or {}
    if str((data.get("chat") or {}).get("id")) != str(CHAT_ID) and \
            str((data.get("chat") or {}).get("username", "")) != str(CHAT_ID).lstrip("@"):
        return                                              # only the main channel is mirrored
    base = url.rsplit("/", 1)[0]
    if url.endswith("/sendPhoto"):
        photos = data.get("photo") or []
        if not photos:
            return
        cap = data.get("caption") or ""
        body = {"photo": photos[-1]["file_id"], "caption": cap}
        dests = destinations(cap, True, table)
        method = "sendPhoto"
    else:
        try:
            body = json.loads(request.content or b"{}")
        except ValueError:
            return
        body.pop("chat_id", None)
        text = body.get("text") or ""
        dests = destinations(text, is_result_text(text), table)
        method = "sendMessage"
    if dests:
        asyncio.get_event_loop().create_task(_copy(method, body, dests, base))
