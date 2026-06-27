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

from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.usdjpy_model import UsdJpyTrade
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
