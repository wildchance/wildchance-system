"""Point-in-time signal store (research layer A).

capture(signal) — when a card is sent, freeze what the system knew: entry / stop / targets,
the council votes, the 8-currency strength scores and event rotation, the pair's COT / retail
read, the nearest options levels, VIX regime and calendar context. close(signal) writes the
outcome (result + realised R) when the tracker resolves it. Reads only cached data (no paid
calls). /research/snapshots exports rows for analysis.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Dict, List, Optional


def realised_r(s: Dict) -> Optional[float]:
    """Equal split across targets: a hit target pays its R; after TP1 the rest exits at entry (0);
    a stop before TP1 = −1R. Open / unfilled → None."""
    if s.get("result") not in ("win", "loss", "breakeven") or not s.get("filled", True):
        return None if s.get("result") == "open" else 0.0
    risk = abs(s["entry"] - s["stop"])
    if not risk or not s.get("targets"):
        return None
    if s["result"] == "loss" and not s.get("hits"):
        return -1.0
    n = len(s["targets"])
    hit = {h["tp"] for h in s.get("hits") or []}
    got = sum(abs(t - s["entry"]) / risk if (i + 1) in hit else 0.0 for i, t in enumerate(s["targets"]))
    return round(got / n, 3)


async def context_for(symbol: str, side: str) -> Dict:
    ctx: Dict = {}
    try:
        from services import fx_rotation as fx
        st = await fx.strength()
        lg = fx.legs(symbol)
        ctx["strength"] = {c: v["score"] for c, v in st["strength"].items()}
        if lg and lg[0] in ctx["strength"] and lg[1] in ctx["strength"]:
            ctx["strength_gap"] = round(ctx["strength"][lg[0]] - ctx["strength"][lg[1]], 2)
        rot = await fx.rotation()
        ctx["blocked_ccys"] = sorted(rot.get("blocked") or {})
    except Exception:
        pass
    try:
        from services.wildchance_service import get_latest_feed
        feed = await get_latest_feed() or {}
        row = next((s for s in feed.get("signals") or [] if s.get("pair") == symbol), None)
        if row:
            ctx["positioning"] = {k: row.get(k) for k in ("verdict", "confidence", "retail_long", "cot_net",
                                                          "cot_rank", "cot_agrees")}
    except Exception:
        pass
    try:
        from services.options_levels import cached_levels
        ctx["options"] = [{"label": x["label"], "price": x["price"]} for x in cached_levels(symbol)][:8]
    except Exception:
        pass
    try:
        from services.data_feeds import macro_cached
        m = macro_cached()
        ctx["vix"] = (m.get("vix") or {}).get("close")
        ctx["vix_regime"] = (m.get("vix") or {}).get("regime")
    except Exception:
        pass
    try:
        from services import econ_calendar as ec
        nxt = [e for e in ec.upcoming(2, high_only=True)][:3]
        ctx["next_events"] = [f"{e['ccy']} {e['title']} {e['time_utc']}" for e in nxt]
    except Exception:
        pass
    ctx["hour_utc"] = dt.datetime.now(dt.timezone.utc).hour
    ctx["weekday"] = dt.datetime.now(dt.timezone.utc).weekday()
    return ctx


async def capture(s: Dict) -> bool:
    try:
        from database.db import AsyncSessionLocal
        from models.signal_snapshot_model import SignalSnapshot
        ctx = {"entry": s["entry"], "stop": s["stop"], "targets": s["targets"], "votes": s.get("votes") or {},
               "pending": s.get("pending"), **await context_for(s["symbol"], s["side"])}
        async with AsyncSessionLocal() as db:
            db.add(SignalSnapshot(id=s["id"], symbol=s["symbol"], side=s["side"], source=s["source"],
                                  context=json.dumps(ctx, default=str), result="open"))
            await db.commit()
        return True
    except Exception:
        return False


async def close(s: Dict) -> bool:
    try:
        from database.db import AsyncSessionLocal
        from models.signal_snapshot_model import SignalSnapshot
        async with AsyncSessionLocal() as db:
            row = await db.get(SignalSnapshot, s["id"])
            if not row:
                return False
            row.result, row.r_multiple = s["result"], realised_r(s)
            row.closed_at = dt.datetime.fromtimestamp(s.get("closed_at") or 0, dt.timezone.utc)
            await db.commit()
        return True
    except Exception:
        return False


async def export(limit: int = 500, source: Optional[str] = None) -> List[Dict]:
    try:
        from sqlalchemy import select
        from database.db import AsyncSessionLocal
        from models.signal_snapshot_model import SignalSnapshot
        q = select(SignalSnapshot).order_by(SignalSnapshot.created_at.desc()).limit(limit)
        if source:
            q = q.where(SignalSnapshot.source == source)
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(q)).scalars().all()
    except Exception:
        return []
    return [{"id": r.id, "created_at": r.created_at.isoformat() if r.created_at else None, "symbol": r.symbol,
             "side": r.side, "source": r.source, "result": r.result, "r": r.r_multiple,
             "context": json.loads(r.context or "{}")} for r in rows]
