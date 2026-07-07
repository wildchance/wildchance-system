"""Read closed trades and build monthly / all-time performance scorecards.

This is the persistence glue for usdjpy/scorecard.py. It reads CLOSED trades
(those with a realized result_r) from UsdJpyTrade and turns them into:
  - an all-time scorecard + reflection verdict
  - a per-calendar-month breakdown
  - a BUY-vs-SELL breakdown
  - the current-month scorecard (the number the monthly digest reports)

Stop-aware mode collapses any stop-breached trade to -1R before scoring, so the
reflection loop can be run on the more conservative outcome series too.
"""

from __future__ import annotations

import datetime as _dt
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.usdjpy_model import UsdJpyTrade
from models.gold_position_model import GoldPosition
from usdjpy.engine import realized_r
from usdjpy.scorecard import build_scorecard, by_group


def _month(entry_date) -> str:
    """YYYY-MM bucket key from a date/datetime/ISO string."""
    return str(entry_date)[:7]


async def _closed_trades(db: AsyncSession) -> List[UsdJpyTrade]:
    res = await db.execute(
        select(UsdJpyTrade)
        .where(UsdJpyTrade.status == "CLOSED")
        .order_by(UsdJpyTrade.entry_date.asc())
    )
    return list(res.scalars().all())


def _rows(trades: List[UsdJpyTrade], stop_aware: bool) -> List[dict]:
    out = []
    for t in trades:
        if t.result_r is None:
            continue
        r = realized_r(t.result_r, bool(t.stop_breached)) if stop_aware else t.result_r
        out.append({
            "month": _month(t.entry_date),
            "action": t.action,
            "result_r": r,
            "entry_date": str(t.entry_date),
        })
    return out


async def build_report(db: AsyncSession, stop_aware: bool = False,
                       month: Optional[str] = None) -> dict:
    """Full scorecard report. If `month` (YYYY-MM) is given, also surface it."""
    trades = await _closed_trades(db)
    rows = _rows(trades, stop_aware)
    all_r = [row["result_r"] for row in rows]

    by_month = by_group(rows, "month")
    current_month = month or (max(by_month) if by_month else None)

    return {
        "mode": "stop_aware" if stop_aware else "workbook_faithful",
        "closed_trades": len(rows),
        "all_time": build_scorecard(all_r).to_dict(),
        "current_month": current_month,
        "current_month_scorecard": by_month.get(current_month) if current_month else None,
        "by_month": dict(sorted(by_month.items())),
        "by_action": by_group(rows, "action"),
    }


async def monthly_digest_text(db: AsyncSession, month: Optional[str] = None) -> Optional[str]:
    """Human-readable monthly scorecard for Telegram, or None if nothing closed."""
    report = await build_report(db, stop_aware=False, month=month)
    m = report["current_month"]
    card = report["current_month_scorecard"]
    if not m or not card:
        return None

    verdict_icon = {"GREEN": "🟢", "AMBER": "🟡", "RED": "🔴",
                    "INCONCLUSIVE": "⚪"}.get(card["verdict"], "⚪")
    pf = card["profit_factor"]
    pf_str = "∞" if pf is None else f"{pf:.2f}"
    wr = card["win_rate"]
    wr_str = "—" if wr is None else f"{wr:.0%}"

    lines = [
        f"📊 *Monthly Scorecard — {m}*",
        "",
        f"{verdict_icon} *{card['verdict']}*  ·  confidence ×{card['confidence_factor']}",
        f"Trades: {card['n']}  (W {card['wins']} / L {card['losses']})",
        f"Win rate: {wr_str}",
        f"Expectancy: {card['expectancy']:+.2f}R per trade",
        f"Total: {card['total_r']:+.2f}R   ·   Profit factor: {pf_str}",
        f"Max drawdown: {card['max_drawdown_r']:.2f}R",
        f"Best / worst: {card['best_r']:+.2f}R / {card['worst_r']:+.2f}R",
        "",
        f"_{card['lesson']}_",
    ]
    return "\n".join(lines)


async def window_report(db: AsyncSession, days: int = 7,
                        stop_aware: bool = False) -> dict:
    """Scorecard over the trailing `days` (by entry_date). For the weekly review."""
    cutoff = (_dt.datetime.now(_dt.timezone.utc).date() - _dt.timedelta(days=days))
    trades = await _closed_trades(db)
    rows = [r for r in _rows(trades, stop_aware)
            if r["entry_date"][:10] >= cutoff.isoformat()]
    return {
        "mode": "stop_aware" if stop_aware else "workbook_faithful",
        "window_days": days,
        "since": cutoff.isoformat(),
        "closed_trades": len(rows),
        "scorecard": build_scorecard([r["result_r"] for r in rows]).to_dict(),
        "by_action": by_group(rows, "action"),
    }


async def gold_report(db: AsyncSession, month: Optional[str] = None) -> dict:
    """Scorecard over CLOSED gold swing positions (realized R), all-time + monthly.

    Reuses the same reflection engine as USD/JPY so gold finally has the feedback
    loop it was missing — every closed position carries a result_r.
    """
    res = await db.execute(
        select(GoldPosition)
        .where(GoldPosition.status == "CLOSED")
        .order_by(GoldPosition.opened_at.asc()))
    trades = list(res.scalars().all())
    rows = []
    for t in trades:
        if t.result_r is None:
            continue
        rows.append({
            "month": _month(t.opened_at),
            "action": t.action,
            "result_r": t.result_r,
            "exit_reason": t.exit_reason,
            "opened_at": str(t.opened_at),
        })
    all_r = [r["result_r"] for r in rows]
    by_month = by_group(rows, "month")
    current_month = month or (max(by_month) if by_month else None)
    return {
        "instrument": "XAU/USD",
        "closed_trades": len(rows),
        "all_time": build_scorecard(all_r).to_dict(),
        "current_month": current_month,
        "current_month_scorecard": by_month.get(current_month) if current_month else None,
        "by_month": dict(sorted(by_month.items())),
        "by_action": by_group(rows, "action"),
        "by_exit": by_group(rows, "exit_reason"),
    }


async def gold_digest_text(db: AsyncSession) -> Optional[str]:
    """Human-readable gold scorecard for Telegram, or None if nothing closed."""
    rep = await gold_report(db)
    card = rep["all_time"]
    if not card["n"]:
        return None
    icon = {"GREEN": "🟢", "AMBER": "🟡", "RED": "🔴",
            "INCONCLUSIVE": "⚪"}.get(card["verdict"], "⚪")
    pf = card["profit_factor"]
    pf_str = "∞" if pf is None else f"{pf:.2f}"
    wr = card["win_rate"]
    wr_str = "—" if wr is None else f"{wr:.0%}"
    exits = "  ".join(f"{k} {v['n']}" for k, v in rep["by_exit"].items())
    lines = [
        "🏆 *GOLD Scorecard — all-time*",
        "",
        f"{icon} *{card['verdict']}*  ·  confidence ×{card['confidence_factor']}",
        f"Trades: {card['n']}  (W {card['wins']} / L {card['losses']})  ·  win {wr_str}",
        f"Expectancy: {card['expectancy']:+.2f}R   ·   Total: {card['total_r']:+.2f}R",
        f"Profit factor: {pf_str}   ·   Max DD: {card['max_drawdown_r']:.2f}R",
        (f"Exits: {exits}" if exits else ""),
        "",
        f"_{card['lesson']}_",
    ]
    return "\n".join(l for l in lines if l != "")


async def window_digest_text(db: AsyncSession, days: int = 7,
                             label: str = "Weekly") -> Optional[str]:
    """Human-readable trailing-window review for Telegram, or None if empty."""
    rep = await window_report(db, days=days)
    card = rep["scorecard"]
    if not card["n"]:
        return None
    icon = {"GREEN": "🟢", "AMBER": "🟡", "RED": "🔴",
            "INCONCLUSIVE": "⚪"}.get(card["verdict"], "⚪")
    pf = card["profit_factor"]
    pf_str = "∞" if pf is None else f"{pf:.2f}"
    wr = card["win_rate"]
    wr_str = "—" if wr is None else f"{wr:.0%}"
    lines = [
        f"🗓️ *{label} Review — last {days}d* (since {rep['since']})",
        "",
        f"{icon} *{card['verdict']}*  ·  confidence ×{card['confidence_factor']}",
        f"Trades: {card['n']}  (W {card['wins']} / L {card['losses']})  ·  win {wr_str}",
        f"Expectancy: {card['expectancy']:+.2f}R   ·   Total: {card['total_r']:+.2f}R",
        f"Profit factor: {pf_str}   ·   Max DD: {card['max_drawdown_r']:.2f}R",
        "",
        f"_{card['lesson']}_",
    ]
    return "\n".join(lines)
