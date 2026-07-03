"""EdgeFinder service — build the per-pair bias scoreboard from live layers.

Reads the STORED wildchance feed (retail + COT + confluence, no per-pair fetch)
so the board is cheap, fetches the high-impact calendar ONCE and reuses it across
pairs, and optionally enriches each row with the MMM weekly-cycle bias (an OHLC
fetch per pair — off by default to respect the free-tier rate limit).
"""

from __future__ import annotations

import datetime as _dt
from typing import List, Optional

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from services.wildchance_service import get_latest_feed
from services import mmm_service
from services import seasonality_service
from services.news_guard import (
    symbol_currencies, filter_high_impact, high_impact_calendar, nfp_window,
)
from mmm.engine import directional_bias
from edgefinder.engine import score_pair
from models.edge_snapshot_model import EdgeSnapshot


def _news_for(pair: str, events: list, today: _dt.date) -> Optional[str]:
    ccys = symbol_currencies(pair)
    hits = [f"{h['ccy']} {h['event']}" for h in filter_high_impact(events, ccys)][:2]
    if "USD" in ccys:
        nfp = nfp_window(today)
        if nfp:
            hits.insert(0, f"USD NFP {nfp}")
    return ("⚠️ " + "; ".join(dict.fromkeys(hits))) if hits else None


async def scoreboard(with_mmm: bool = False, with_season: bool = False) -> dict:
    """Full per-pair bias board, ranked by absolute conviction.

    with_mmm / with_season each add a per-pair OHLC fetch, so they're off by
    default to respect the free-tier rate limit.
    """
    feed = await get_latest_feed()
    signals = (feed or {}).get("signals", [])
    events = await high_impact_calendar(None)          # one fetch, reused
    today = _dt.date.today()

    rows: List[dict] = []
    for sig in signals:
        pair = sig.get("pair")
        mmm_bias = None
        if with_mmm and pair:
            read = await mmm_service.read(pair)
            mmm_bias = directional_bias(read) if read else None
        seasonal = await seasonality_service.seasonal_for(pair) if (with_season and pair) else None
        news = _news_for(pair, events, today) if pair else None
        rows.append(score_pair(sig, mmm_bias=mmm_bias, seasonal=seasonal, news=news))

    rows.sort(key=lambda r: abs(r["score"]), reverse=True)
    return {
        "as_of": (feed or {}).get("updated"),
        "count": len(rows),
        "with_mmm": with_mmm,
        "with_season": with_season,
        "board": rows,
    }


_BIAS_ICON = {"STRONG LONG": "🟢🟢", "LONG": "🟢", "NEUTRAL": "➖",
              "SHORT": "🔴", "STRONG SHORT": "🔴🔴"}


async def digest_text(top_n: int = 5, min_score: int = 2,
                      with_mmm: bool = False) -> Optional[str]:
    """Telegram-ready 'top bias' digest, or None if nothing meets ``min_score``."""
    board = (await scoreboard(with_mmm=with_mmm)).get("board", [])
    strong = [r for r in board if abs(r["score"]) >= min_score][:top_n]
    if not strong:
        return None
    lines = ["🧭 *EdgeFinder — Top Bias*", ""]
    for r in strong:
        news = " ⚠️" if r.get("news") else ""
        lines.append(
            f"{_BIAS_ICON.get(r['bias'], '')} *{r['pair']}* {r['bias']} "
            f"({r['score']:+d})  ·  sys {r.get('system_verdict') or '—'}{news}"
        )
    lines += ["", "_retail + COT aggregate, most-conviction first. ⚠️ = news-adjacent._"]
    return "\n".join(lines)


async def pair_read(symbol: str) -> Optional[dict]:
    """Deep single-pair read: always includes the MMM bias + news."""
    feed = await get_latest_feed()
    signals = (feed or {}).get("signals", [])
    want = symbol.upper().replace("-", "/")
    sig = next((s for s in signals
                if (s.get("pair") or "").upper().replace("-", "/") == want), None)
    if not sig:
        return None
    read = await mmm_service.read(sig["pair"])
    mmm_bias = directional_bias(read) if read else None
    seasonal = await seasonality_service.seasonal_for(sig["pair"])
    events = await high_impact_calendar(None)
    news = _news_for(sig["pair"], events, _dt.date.today())
    row = score_pair(sig, mmm_bias=mmm_bias, seasonal=seasonal, news=news)
    row["mmm_read"] = read
    row["seasonal"] = seasonal
    return row


# ---------------------------------------------------------------------------
# History — snapshot the board daily, read trend, flag bias shifts
# ---------------------------------------------------------------------------
async def snapshot(db: AsyncSession) -> dict:
    """Persist today's board (one row per pair). Idempotent per day (re-run
    replaces today's rows)."""
    board = (await scoreboard()).get("board", [])
    today = _dt.date.today()
    await db.execute(delete(EdgeSnapshot).where(EdgeSnapshot.snap_date == today))
    for r in board:
        db.add(EdgeSnapshot(snap_date=today, pair=r["pair"], bias=r["bias"],
                            score=r["score"], system_verdict=r.get("system_verdict")))
    await db.commit()
    return {"snap_date": str(today), "rows": len(board)}


def _norm(p: str) -> str:
    return (p or "").upper().replace("/", "").replace("-", "").replace(" ", "")


async def history(db: AsyncSession, pair: str, days: int = 30) -> dict:
    """Score/bias trend for a pair over the trailing ``days`` (separator-insensitive)."""
    key = _norm(pair)
    since = _dt.date.today() - _dt.timedelta(days=days)
    res = await db.execute(
        select(EdgeSnapshot).where(EdgeSnapshot.snap_date >= since)
        .order_by(EdgeSnapshot.snap_date.asc()))
    rows = [r for r in res.scalars().all() if _norm(r.pair) == key]
    return {"pair": pair, "days": days,
            "points": [{"date": str(r.snap_date), "score": r.score, "bias": r.bias}
                       for r in rows]}


async def shifts(db: AsyncSession) -> dict:
    """Pairs whose bias changed between the two most recent snapshots."""
    res = await db.execute(
        select(EdgeSnapshot.snap_date).distinct()
        .order_by(EdgeSnapshot.snap_date.desc()).limit(2))
    dates = [d for (d,) in res.all()]
    if len(dates) < 2:
        return {"shifts": [], "note": "need at least 2 daily snapshots"}
    cur_d, prev_d = dates[0], dates[1]

    async def _rows(day):
        r = await db.execute(select(EdgeSnapshot).where(EdgeSnapshot.snap_date == day))
        return {x.pair: x for x in r.scalars().all()}

    cur, prev = await _rows(cur_d), await _rows(prev_d)
    out = []
    for pair, c in cur.items():
        p = prev.get(pair)
        if not p:
            continue
        if c.bias != p.bias or (c.score > 0) != (p.score > 0):
            out.append({"pair": pair, "from": p.bias, "to": c.bias,
                        "score_from": p.score, "score_to": c.score,
                        "delta": c.score - p.score})
    out.sort(key=lambda x: abs(x["delta"]), reverse=True)
    return {"from": str(prev_d), "to": str(cur_d), "shifts": out}
