"""Trade journal — every closed trade logged, classified and summarised.

classify(): BREAKEVEN when |pips| ≤ JOURNAL_BE_PIPS (1.0) or |net| ≤ JOURNAL_BE_USD
(0.50) — a stop moved to entry closes a hair either side of zero; otherwise WIN when
net > 0, LOSS when net < 0. stats(): win rate (with and without breakevens), profit
factor, expectancy per trade, average win / loss (pips and $), best / worst,
streaks, per-symbol and per-day tables, equity curve. Reports post pips and counts;
dollar figures only when JOURNAL_SHOW_USD=true (the channel has subscribers).
"""

from __future__ import annotations

import datetime as dt
from typing import Dict, List, Optional

from decouple import config

BE_PIPS = float(config("JOURNAL_BE_PIPS", default="1.0"))
BE_USD = float(config("JOURNAL_BE_USD", default="0.50"))
SHOW_USD = str(config("JOURNAL_SHOW_USD", default="false")).lower() in ("1", "true", "yes", "on")


def classify(net: float, pips: float, be_pips: float = BE_PIPS, be_usd: float = BE_USD) -> str:
    if abs(pips) <= be_pips or abs(net) <= be_usd:
        return "breakeven"
    return "win" if net > 0 else "loss"


def _ts(x) -> Optional[dt.datetime]:
    try:
        return dt.datetime.fromtimestamp(int(x), dt.timezone.utc) if x else None
    except (TypeError, ValueError, OSError):
        return None


def to_row(d: Dict) -> Optional[Dict]:
    """One EA deal → a journal row (None if malformed)."""
    from services.broker_feed import canon
    from services.flash_card import pips as pips_of
    try:
        sym = canon(d["symbol"])
        side = str(d["side"]).upper()
        op, cp, net = float(d["open_price"]), float(d["close_price"]), float(d.get("net") or 0)
    except (KeyError, TypeError, ValueError):
        return None
    p = pips_of(sym, side, op, cp)
    return {"ticket": str(d["ticket"]), "symbol": sym, "broker_symbol": d["symbol"], "side": side,
            "volume": float(d.get("volume") or 0), "open_price": op, "close_price": cp,
            "open_time": _ts(d.get("open_time")), "close_time": _ts(d.get("close_time")),
            "net": round(net, 2), "pips": p, "result": classify(net, p),
            "magic": int(d.get("magic") or 0), "comment": (d.get("comment") or "")[:64]}


async def record(deals: List[Dict]) -> Dict:
    """Insert new tickets only (the EA may resend)."""
    rows = [r for r in (to_row(d) for d in deals or []) if r]
    if not rows:
        return {"logged": 0}
    try:
        from sqlalchemy import select
        from database.db import AsyncSessionLocal
        from models.closed_trade_model import ClosedTrade
        async with AsyncSessionLocal() as db:
            have = set((await db.execute(select(ClosedTrade.ticket).where(
                ClosedTrade.ticket.in_([r["ticket"] for r in rows])))).scalars().all())
            new = [r for r in rows if r["ticket"] not in have]
            for r in new:
                db.add(ClosedTrade(**r))
            await db.commit()
        return {"logged": len(new), "duplicates": len(rows) - len(new)}
    except Exception as e:
        return {"logged": 0, "error": f"{type(e).__name__}: {e}"[:160]}


def summarise(rows: List[Dict]) -> Dict:
    """rows: dicts with symbol, side, net, pips, result, close_time (oldest-first)."""
    n = len(rows)
    if not n:
        return {"trades": 0}
    wins = [r for r in rows if r["result"] == "win"]
    losses = [r for r in rows if r["result"] == "loss"]
    bes = [r for r in rows if r["result"] == "breakeven"]
    gross_w = sum(r["net"] for r in wins)
    gross_l = -sum(r["net"] for r in losses)
    decided = len(wins) + len(losses)
    streak_w = streak_l = cw = cl = 0
    for r in rows:
        if r["result"] == "win":
            cw, cl = cw + 1, 0
        elif r["result"] == "loss":
            cl, cw = cl + 1, 0
        streak_w, streak_l = max(streak_w, cw), max(streak_l, cl)
    equity, curve = 0.0, []
    for r in rows:
        equity += r["net"]
        curve.append({"t": r["close_time"].isoformat() if r.get("close_time") else None, "equity": round(equity, 2)})
    by_sym: Dict[str, Dict] = {}
    for r in rows:
        s = by_sym.setdefault(r["symbol"], {"trades": 0, "wins": 0, "losses": 0, "breakeven": 0, "net": 0.0, "pips": 0.0})
        s["trades"] += 1
        s[{"win": "wins", "loss": "losses", "breakeven": "breakeven"}[r["result"]]] += 1
        s["net"] = round(s["net"] + r["net"], 2)
        s["pips"] = round(s["pips"] + r["pips"], 1)
    return {"trades": n, "wins": len(wins), "losses": len(losses), "breakeven": len(bes),
            "win_rate_pct": round(100 * len(wins) / decided, 1) if decided else None,
            "win_rate_incl_be_pct": round(100 * len(wins) / n, 1),
            "breakeven_rate_pct": round(100 * len(bes) / n, 1),
            "net": round(sum(r["net"] for r in rows), 2), "pips": round(sum(r["pips"] for r in rows), 1),
            "avg_win": round(gross_w / len(wins), 2) if wins else None,
            "avg_loss": round(-gross_l / len(losses), 2) if losses else None,
            "avg_win_pips": round(sum(r["pips"] for r in wins) / len(wins), 1) if wins else None,
            "avg_loss_pips": round(sum(r["pips"] for r in losses) / len(losses), 1) if losses else None,
            "profit_factor": round(gross_w / gross_l, 2) if gross_l else None,
            "expectancy": round(sum(r["net"] for r in rows) / n, 2),
            "best": max(rows, key=lambda r: r["net"])["net"], "worst": min(rows, key=lambda r: r["net"])["net"],
            "max_win_streak": streak_w, "max_loss_streak": streak_l,
            "by_symbol": dict(sorted(by_sym.items(), key=lambda kv: -kv[1]["net"])), "equity_curve": curve}


async def rows_since(since: Optional[dt.datetime] = None, symbol: Optional[str] = None) -> List[Dict]:
    try:
        from sqlalchemy import select
        from database.db import AsyncSessionLocal
        from models.closed_trade_model import ClosedTrade
        q = select(ClosedTrade).order_by(ClosedTrade.close_time)
        if since:
            q = q.where(ClosedTrade.close_time >= since)
        if symbol:
            q = q.where(ClosedTrade.symbol == symbol.upper().replace("/", ""))
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(q)).scalars().all()
    except Exception:
        return []
    return [{"ticket": r.ticket, "symbol": r.symbol, "side": r.side, "volume": r.volume, "open_price": r.open_price,
             "close_price": r.close_price, "close_time": r.close_time, "net": r.net or 0.0, "pips": r.pips or 0.0,
             "result": r.result, "magic": r.magic, "comment": r.comment} for r in rows]


def report_card(period: str, s: Dict) -> str:
    if not s.get("trades"):
        return f"📒 *Trade journal — {period}*: no closed trades."
    lines = [f"📒 *Trade journal — {period}*",
             f"{s['trades']} trades · ✅ {s['wins']} win · ❌ {s['losses']} loss · ➖ {s['breakeven']} breakeven",
             f"Win rate {s['win_rate_pct'] if s['win_rate_pct'] is not None else '—'}% "
             f"(incl. breakevens {s['win_rate_incl_be_pct']}%) · net {s['pips']:+,.1f} pips"
             + (f" · PF {s['profit_factor']}" if s.get("profit_factor") else "")]
    if SHOW_USD:
        lines.append(f"Net ${s['net']:+,.2f} · expectancy ${s['expectancy']:+,.2f}/trade")
    top = list(s["by_symbol"].items())[:4]
    if top:
        lines.append("By market: " + " · ".join(f"{k} {v['pips']:+,.1f}p ({v['wins']}W/{v['losses']}L/{v['breakeven']}BE)"
                                               for k, v in top))
    return "\n".join(lines)
