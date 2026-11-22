"""TwelveData rate limiter + response cache, installed once at the HTTP layer.

Root cause it fixes (Render log): ``TwelveData error 429: You have run out of API
credits for the current minute. 10 API credits were used, with the current limit
being 8.`` The free plan allows 8 requests/minute (and ~800/day), while the scans
fired dozens at once — gold usually lost the race, so gold endpoints returned 502.

``install()`` wraps ``httpx.AsyncClient.send`` (every async call site) and exposes
``acquire_sync()`` for the urllib call in the scraper. For api.twelvedata.com only:
  • CACHE   identical requests are served from memory within a TTL by interval:
            1day/1week 12h · 4h 30m · 1h 10m · 15min 4m · /price 60s
  • LIMIT   at most TD_RATE_PER_MIN real requests per rolling minute (default 7,
            one under the cap); callers queue for a slot. If the wait would exceed
            TD_MAX_WAIT_S (default 75) the request fails fast with a local 429.
  • BUDGET  counts requests per UTC day (TD_DAILY_LIMIT, default 790) — over budget,
            only cached data is served.
"""

from __future__ import annotations

import asyncio
import collections
import json
import threading
import time
from typing import Dict, Optional, Tuple
from urllib.parse import parse_qsl, urlsplit

from decouple import config

RATE = int(config("TD_RATE_PER_MIN", default="7"))
MAX_WAIT_S = float(config("TD_MAX_WAIT_S", default="75"))
DAILY_LIMIT = int(config("TD_DAILY_LIMIT", default="790"))
HOST = "api.twelvedata.com"
TTL = {"1week": 43200, "1month": 43200, "1day": 43200, "4h": 1800, "2h": 1200,
       "1h": 600, "30min": 300, "15min": 240, "5min": 120, "1min": 50}

_lock = threading.Lock()
_slots: collections.deque = collections.deque(maxlen=200)    # reserved start times
_cache: "collections.OrderedDict[str, Tuple[float, bytes]]" = collections.OrderedDict()
_stats = {"day": "", "requests": 0, "cache_hits": 0, "rejected": 0}
_installed = {"done": False}


def _day() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def cache_key(url: str) -> Tuple[str, int]:
    """(key without the apikey, ttl seconds) for a TwelveData URL."""
    parts = urlsplit(url)
    q = sorted((k, v) for k, v in parse_qsl(parts.query) if k != "apikey")
    ttl = 60 if parts.path.endswith("/price") else TTL.get(dict(q).get("interval", ""), 300)
    return parts.path + "?" + "&".join(f"{k}={v}" for k, v in q), ttl


def cache_get(key: str, ttl: int) -> Optional[bytes]:
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            _cache.move_to_end(key)
            _stats["cache_hits"] += 1
            return hit[1]
    return None


def cache_put(key: str, body: bytes) -> None:
    with _lock:
        _cache[key] = (time.time(), body)
        _cache.move_to_end(key)
        while len(_cache) > 600:
            _cache.popitem(last=False)


def reserve(max_wait: float = MAX_WAIT_S, now: Optional[float] = None,
            credits: int = 1) -> Optional[float]:
    """Reserve ``credits`` slots (TwelveData bills 1 credit PER SYMBOL in a batch
    request). Returns seconds to wait before sending, or None (queue/budget full)."""
    credits = max(1, int(credits))
    with _lock:
        now = time.time() if now is None else now
        if _stats["day"] != _day():
            _stats.update(day=_day(), requests=0, rejected=0, cache_hits=0)
        if _stats["requests"] + credits > DAILY_LIMIT:
            _stats["rejected"] += 1
            return None
        added, start = [], now
        for _ in range(credits):
            start = now
            if len(_slots) >= RATE:
                start = max(now, _slots[-RATE] + 60.5)
            _slots.append(start)
            added.append(start)
        if start - now > max_wait:                     # roll back — don't hold slots
            for _ in added:
                _slots.pop()
            _stats["rejected"] += 1
            return None
        _stats["requests"] += credits
        return start - now


def credits_for(url: str) -> int:
    sym = dict(parse_qsl(urlsplit(url).query)).get("symbol", "")
    return max(1, len([x for x in sym.split(",") if x.strip()]))


async def acquire(max_wait: float = MAX_WAIT_S, credits: int = 1) -> bool:
    w = reserve(max_wait, credits=credits)
    if w is None:
        return False
    if w > 0:
        await asyncio.sleep(w)
    return True


def acquire_sync(max_wait: float = MAX_WAIT_S, credits: int = 1) -> bool:
    w = reserve(max_wait, credits=credits)
    if w is None:
        return False
    if w > 0:
        time.sleep(w)
    return True


def _ok_payload(body: bytes) -> bool:
    try:
        d = json.loads(body)
    except ValueError:
        return False
    return isinstance(d, dict) and d.get("status") != "error" and ("values" in d or "price" in d)


def record_fresh(url: str, body: bytes) -> None:
    """Tell the freshness guard which symbols just returned REAL (non-cached) data —
    every TwelveData path (series, /price, batches) passes here, not only ohlc_service.
    For a batch, only symbols whose own entry is valid count."""
    try:
        from services.freshness import record
        syms = [x.strip() for x in dict(parse_qsl(urlsplit(url).query)).get("symbol", "").split(",")
                if x.strip()]
        d = json.loads(body)
        for sym in syms:
            node = d.get(sym) if len(syms) > 1 else d
            if isinstance(node, dict) and node.get("status") != "error" and \
                    ("price" in node or "values" in node):
                record(sym, "twelvedata")
    except Exception:
        pass


def install() -> None:
    """Wrap httpx.AsyncClient.send once (idempotent)."""
    if _installed["done"]:
        return
    import httpx
    orig = httpx.AsyncClient.send

    async def send(self, request, *args, **kwargs):
        if request.url.host != HOST:
            return await orig(self, request, *args, **kwargs)
        key, ttl = cache_key(str(request.url))
        body = cache_get(key, ttl)
        if body is not None:
            return httpx.Response(200, content=body, request=request,
                                  headers={"content-type": "application/json", "x-td-cache": "hit"})
        if not await acquire(credits=credits_for(str(request.url))):
            msg = {"status": "error", "code": 429,
                   "message": "local TwelveData limiter: queue/budget full — try later"}
            return httpx.Response(200, json=msg, request=request)
        resp = await orig(self, request, *args, **kwargs)
        raw = await resp.aread()
        if resp.status_code == 200 and _ok_payload(raw):
            cache_put(key, raw)
            record_fresh(str(request.url), raw)
        return resp

    httpx.AsyncClient.send = send
    _installed["done"] = True


def status() -> Dict:
    with _lock:
        now = time.time()
        recent = sum(1 for t in _slots if now - 60 <= t <= now)
        return {"installed": _installed["done"], "rate_per_min": RATE, "daily_limit": DAILY_LIMIT,
                "used_today": _stats["requests"], "cache_hits_today": _stats["cache_hits"],
                "rejected_today": _stats["rejected"], "in_last_minute": recent,
                "cached_series": len(_cache)}
