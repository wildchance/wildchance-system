"""Options-flow FEED — pull the put/call walls + expected-move from a configured
provider, falling back to the operator-fed snapshot (gold.options_flow.INPUTS).

There is no free gold-options API, so this stays feed-AGNOSTIC: point
``OPTIONS_FEED_URL`` at any JSON source that returns the fields below (a broker
export, a sheet-published-as-JSON, your own scraper) and refresh() ingests it via
set_inputs. Without the URL it is a no-op — the operator POST /gold/options path
keeps working unchanged. This is the "build the feed later" slot, now wired: set
the env var and schedule POST /gold/options/refresh.

Provider JSON contract (flat or nested sigma both accepted):
  {"future": 4030.4, "put_wall": 3980, "call_wall": 4080,
   "sigma1": 18, "sigma2": 36, "sigma3": 54,     # OR "sigma": {"1":18,"2":36,"3":54}
   "put_vol": 12000, "call_vol": 8000, "as_of": "2026-07-23"}
"""

from __future__ import annotations

from typing import Optional

import httpx
from decouple import config

from gold import options_flow as of

OPTIONS_FEED_URL = config("OPTIONS_FEED_URL", default=None)
OPTIONS_FEED_KEY = config("OPTIONS_FEED_KEY", default=None)


def feed_configured() -> bool:
    """Is a live options feed wired (URL set)?"""
    return bool(OPTIONS_FEED_URL)


def _ingest(data: dict) -> dict:
    sig = data.get("sigma") or {}
    return of.set_inputs(
        future=data.get("future"),
        put_wall=data.get("put_wall"),
        call_wall=data.get("call_wall"),
        sigma1=data.get("sigma1", sig.get("1")),
        sigma2=data.get("sigma2", sig.get("2")),
        sigma3=data.get("sigma3", sig.get("3")),
        put_vol=data.get("put_vol"),
        call_vol=data.get("call_vol"),
        as_of=data.get("as_of") or "feed",
    )


async def refresh(url: Optional[str] = None, timeout: float = 10.0) -> dict:
    """Fetch the provider snapshot and feed it into options_flow. No URL → no-op
    (operator-fed snapshot returned unchanged, never raises)."""
    src = url or OPTIONS_FEED_URL
    if not src:
        return {"ok": False, "reason": "no OPTIONS_FEED_URL set — operator-fed only",
                "feed_configured": False, "snapshot": of.snapshot()}
    headers = {"Authorization": f"Bearer {OPTIONS_FEED_KEY}"} if OPTIONS_FEED_KEY else {}
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.get(src, headers=headers)
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        return {"ok": False, "reason": f"feed fetch failed: {e}",
                "feed_configured": True, "snapshot": of.snapshot()}
    ingested = _ingest(data if isinstance(data, dict) else {})
    return {"ok": True, "source": src, "feed_configured": True,
            "ingested": ingested, "snapshot": of.snapshot()}


def feed_status() -> dict:
    """Is the feed wired, and is a snapshot currently loaded?"""
    return {"feed_configured": feed_configured(),
            "operator_fed_ok": of.configured(),
            "as_of": of.INPUTS.get("as_of"),
            "note": ("live feed wired — POST /gold/options/refresh (or cron) ingests it"
                     if feed_configured() else
                     "no live feed — set OPTIONS_FEED_URL, or keep feeding via POST /gold/options")}
