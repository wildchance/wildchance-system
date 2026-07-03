"""EdgeFinder service — build the per-pair bias scoreboard from live layers.

Reads the STORED wildchance feed (retail + COT + confluence, no per-pair fetch)
so the board is cheap, fetches the high-impact calendar ONCE and reuses it across
pairs, and optionally enriches each row with the MMM weekly-cycle bias (an OHLC
fetch per pair — off by default to respect the free-tier rate limit).
"""

from __future__ import annotations

import datetime as _dt
from typing import List, Optional

from services.wildchance_service import get_latest_feed
from services import mmm_service
from services.news_guard import (
    symbol_currencies, filter_high_impact, high_impact_calendar, nfp_window,
)
from mmm.engine import directional_bias
from edgefinder.engine import score_pair


def _news_for(pair: str, events: list, today: _dt.date) -> Optional[str]:
    ccys = symbol_currencies(pair)
    hits = [f"{h['ccy']} {h['event']}" for h in filter_high_impact(events, ccys)][:2]
    if "USD" in ccys:
        nfp = nfp_window(today)
        if nfp:
            hits.insert(0, f"USD NFP {nfp}")
    return ("⚠️ " + "; ".join(dict.fromkeys(hits))) if hits else None


async def scoreboard(with_mmm: bool = False) -> dict:
    """Full per-pair bias board, ranked by absolute conviction."""
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
        news = _news_for(pair, events, today) if pair else None
        rows.append(score_pair(sig, mmm_bias, news))

    rows.sort(key=lambda r: abs(r["score"]), reverse=True)
    return {
        "as_of": (feed or {}).get("updated"),
        "count": len(rows),
        "with_mmm": with_mmm,
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
    events = await high_impact_calendar(None)
    news = _news_for(sig["pair"], events, _dt.date.today())
    row = score_pair(sig, mmm_bias, news)
    row["mmm_read"] = read
    return row
