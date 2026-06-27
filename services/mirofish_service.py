"""MiroFish macro-sentiment adapter.

Drives an external MiroFish instance (if MIROFISH_BASE_URL is set) to produce
a 0-100 sentiment score per forex pair:
  >=65 = bullish bias   (supportive of long setups)
  36-64 = neutral
  <=35 = bearish bias   (supportive of short setups)

The simulation pipeline runs for several minutes, so scores are cached per pair
for CACHE_TTL seconds (default 6 h, matching wildchance feed refresh rhythm).
Callers always get the cached result instantly; a background asyncio.Task runs
the simulation and updates the cache when finished.

When MIROFISH_BASE_URL is not set every call returns None immediately -- the
wildchance system operates normally without MiroFish as a factor.

MiroFish API layout (Flask, prefix-mounted):
  POST /api/graph/ontology/generate   -- upload doc, build knowledge graph
  POST /api/graph/build               -- kick off graph construction (async)
  GET  /api/graph/task/<task_id>      -- poll graph build
  POST /api/simulation/create         -- create simulation
  POST /api/simulation/prepare        -- prepare agents (async)
  POST /api/simulation/prepare/status -- poll preparation
  POST /api/report/generate           -- generate report (async)
  POST /api/report/generate/status    -- poll report generation
  GET  /api/report/<report_id>        -- fetch completed report
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional

import httpx
from decouple import config

MIROFISH_BASE_URL: Optional[str] = (config("MIROFISH_BASE_URL", default=None) or "").rstrip("/") or None
CACHE_TTL: int = int(config("MIROFISH_CACHE_TTL", default=21600))  # 6 hours

# {pair: {"score": int, "bias": str, "cached_at": float}}
_cache: dict[str, dict] = {}
# pairs currently being simulated (prevents duplicate background tasks)
_running: set[str] = set()

_BULLISH_KW = (
    "bullish", "buy", "long", "upside", "rally", "breakout",
    "strength", "rise", "higher", "support", "positive", "gains",
)
_BEARISH_KW = (
    "bearish", "sell", "short", "downside", "decline", "breakdown",
    "weakness", "fall", "lower", "resistance", "negative", "losses",
)


def _is_fresh(pair: str) -> bool:
    entry = _cache.get(pair)
    return bool(entry and (time.time() - entry["cached_at"]) < CACHE_TTL)


def parse_score(markdown: str) -> dict:
    """Return {"score": 0-100, "bias": ...} from a MiroFish report markdown."""
    text = markdown.lower()
    b = sum(text.count(kw) for kw in _BULLISH_KW)
    s = sum(text.count(kw) for kw in _BEARISH_KW)
    total = b + s or 1
    score = round(b / total * 100)
    bias = "bullish" if score >= 65 else ("bearish" if score <= 35 else "neutral")
    return {"score": score, "bias": bias}


async def _simulate(pair: str, context: dict) -> Optional[str]:
    """Run the full MiroFish pipeline and return report markdown, or None on failure."""
    if not MIROFISH_BASE_URL:
        return None
    lines = [f"Analyze the forex pair {pair}."] + [
        f"{k.replace('_', ' ').title()}: {v}" for k, v in context.items()
    ]
    brief = "\n".join(lines)
    try:
        async with httpx.AsyncClient(base_url=MIROFISH_BASE_URL, timeout=30) as c:
            # 1. Build knowledge graph from market brief
            files = {"files": ("brief.txt", brief.encode(), "text/plain")}
            data = {
                "simulation_requirement": f"Simulate forex market participant sentiment for {pair}",
                "project_name": f"wildchance-{pair.replace('/', '')}-{int(time.time())}",
                "context": f"Forex trading analysis for {pair}",
            }
            o = (await c.post("/api/graph/ontology/generate", files=files, data=data)).json()
            if not (o.get("success") and (o.get("data") or {}).get("project_id")):
                return None
            project_id = o["data"]["project_id"]

            build = (await c.post("/api/graph/build", json={
                "project_id": project_id, "graph_name": pair,
                "chunk_size": 500, "chunk_overlap": 50,
            })).json()
            task_id = (build.get("data") or {}).get("task_id")
            graph_id: Optional[str] = None
            if task_id:
                for _ in range(30):                       # poll up to 2 min
                    await asyncio.sleep(4)
                    t = (await c.get(f"/api/graph/task/{task_id}")).json()
                    d = t.get("data") or {}
                    if d.get("status") == "completed":
                        graph_id = d.get("graph_id")
                        break
            if not graph_id:
                return None

            # 2. Create and prepare simulation
            s = (await c.post("/api/simulation/create", json={
                "project_id": project_id, "graph_id": graph_id,
            })).json()
            sim_id = (s.get("data") or {}).get("simulation_id")
            if not sim_id:
                return None

            p = (await c.post("/api/simulation/prepare", json={"simulation_id": sim_id})).json()
            ptask = (p.get("data") or {}).get("task_id")
            if ptask:
                for _ in range(30):                       # poll up to 2.5 min
                    await asyncio.sleep(5)
                    st = (await c.post("/api/simulation/prepare/status",
                                       json={"task_id": ptask})).json()
                    if (st.get("data") or {}).get("status") in ("completed", "ready"):
                        break

            # 3. Generate and fetch report
            r = (await c.post("/api/report/generate", json={"simulation_id": sim_id})).json()
            report_id = (r.get("data") or {}).get("report_id")
            if not report_id:
                return None

            for _ in range(36):                           # poll up to 3 min
                await asyncio.sleep(5)
                rs = (await c.post("/api/report/generate/status",
                                   json={"report_id": report_id})).json()
                if (rs.get("data") or {}).get("status") == "completed":
                    break

            report = (await c.get(f"/api/report/{report_id}")).json()
            return (report.get("data") or {}).get("markdown_content")
    except Exception:
        return None


async def _refresh(pair: str, context: dict) -> None:
    _running.add(pair)
    try:
        markdown = await _simulate(pair, context)
        if markdown:
            result = parse_score(markdown)
            result["cached_at"] = time.time()
            _cache[pair] = result
    finally:
        _running.discard(pair)


async def score(pair: str, context: Optional[dict] = None) -> Optional[dict]:
    """Return the cached MiroFish score for pair, or None if unconfigured.

    When the cache is stale and context is provided, a background simulation
    is started and the caller gets the stale entry (or None) while it runs.
    """
    if not MIROFISH_BASE_URL:
        return None
    if _is_fresh(pair):
        return _cache[pair]
    if context is not None and pair not in _running:
        asyncio.create_task(_refresh(pair, context))
    return _cache.get(pair)


async def status() -> dict:
    if not MIROFISH_BASE_URL:
        return {"configured": False, "base_url": None, "reachable": None}
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            resp = await c.get(f"{MIROFISH_BASE_URL}/health")
            ok = resp.status_code < 400
    except Exception:
        ok = False
    return {
        "configured": True,
        "base_url": MIROFISH_BASE_URL,
        "reachable": ok,
        "cached_pairs": list(_cache.keys()),
        "running": list(_running),
    }
