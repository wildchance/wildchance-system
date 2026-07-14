"""Gold cross-session CBDR confluence scan — Asian premium → London discount.

Builds today's Asian and London CBDR boxes, reads the weekly (ICT) + macro bias,
and arms confluence-scored LIMIT orders (sell the Asian premium into the London
discount; buy the pre-London discount back to premium). The bias filter is the
quality gate — a fade only arms when the higher timeframe agrees.

Boot-safe: returns {orders: []} (not an error) when data or a session box is
missing. Pairs with backtest.cbdr_confluence_backtest — validate the edge on
history before pointing this at a live account.
"""

from __future__ import annotations

import datetime as _dt
from typing import Optional

from services.ohlc_service import fetch_ohlc
from services.gold_scan import _tg
from gold.ict import classify_week
from gold import macro as gmacro
from cbdr.engine import build_cbdr, cbdr_box
from cbdr.confluence import cross_session_confluence

_ASIA = (0, 8)
_LONDON = (8, 13)


def _hour(ts):
    return ts.hour if hasattr(ts, "hour") else int(str(ts).replace("T", " ").split(" ")[1][:2])


def _day(ts):
    return ts.date().isoformat() if hasattr(ts, "date") else str(ts)[:10]


def _box(bars, span):
    sel = [b for b in bars if span[0] <= _hour(b[0]) < span[1]]
    if len(sel) < 2:
        return None
    hi, lo = cbdr_box([b[2] for b in sel], [b[3] for b in sel])
    return build_cbdr(hi, lo) if hi > lo else None


async def scan(balance: float = 5000.0, risk_usd: float = 20.0,
               min_score: int = 65, notify: bool = False) -> dict:
    daily = await fetch_ohlc("XAU/USD", "1day", 25)
    if len(daily) < 3:
        return {"orders": [], "reason": "no XAU/USD daily bars"}
    profile = classify_week(daily)
    weekly = (profile or {}).get("bias") or "neutral"
    entry_px = daily[-1][4]
    try:
        macro = gmacro.macro_confluence(weekly if weekly in ("long", "short") else "long",
                                        entry_px).get("bias", "neutral")
    except Exception:
        macro = "neutral"

    h1 = await fetch_ohlc("XAU/USD", "1h", 48)
    if len(h1) < 4:
        return {"orders": [], "reason": "no XAU/USD 1h bars"}
    now = _dt.datetime.now(_dt.timezone.utc)
    today = now.date().isoformat()
    day_bars = [b for b in h1 if _day(b[0]) == today] or h1

    asian = _box(day_bars, _ASIA)
    if asian is None:
        return {"orders": [], "reason": "Asian CBDR box not formed yet",
                "weekly_bias": weekly, "macro_bias": macro}
    london = _box(day_bars, _LONDON)     # None until London prints — targets fall back

    conf = cross_session_confluence(asian, london, weekly_bias=weekly,
                                    macro_bias=macro, min_score=min_score)
    conf["profile"] = (profile or {}).get("profile")

    if notify and conf["orders"]:
        conf["sent"] = await _tg(_format(conf))
    return conf


async def history_for_backtest(days: int = 60):
    """Fetch H1 history → (grouped_by_day, per_day_weekly_bias) for the backtest.

    The per-day bias is a deterministic HTF-trend proxy: the sign of the 5-day
    daily-close momentum (up→long, down→short, flat→neutral). That's exactly the
    dimension we want to split results by — does selling the Asian premium work in
    down-trending weeks and fail in up-trending ones?
    """
    hours = min(5000, max(days + 5, days) * 24)
    h1 = await fetch_ohlc("XAU/USD", "1h", hours)
    if not h1:
        return {}, {}
    grouped: dict = {}
    for b in h1:
        grouped.setdefault(_day(b[0]), []).append(b)

    daily = await fetch_ohlc("XAU/USD", "1day", days + 10)
    closes = {_day(d[0]): d[4] for d in daily}
    dates = sorted(closes)
    bias: dict = {}
    for i, dte in enumerate(dates):
        if i >= 5:
            mom = closes[dte] - closes[dates[i - 5]]
            bias[dte] = "long" if mom > 0 else "short" if mom < 0 else "neutral"
        else:
            bias[dte] = "neutral"
    # any session day without a daily-momentum read defaults to neutral
    for dte in grouped:
        bias.setdefault(dte, "neutral")
    return grouped, bias


def _format(conf: dict) -> str:
    lines = [f"🎯 *GOLD CBDR Confluence* — weekly {conf['weekly_bias']} · macro {conf['macro_bias']}"]
    a = conf.get("asian_box") or {}
    lines.append(f"_Asian box {a.get('low')}–{a.get('high')}"
                 + (f" · London {conf['london_box']['low']}–{conf['london_box']['high']}_"
                    if conf.get("london_box") else "_"))
    for o in conf["orders"]:
        arrow = "🟢 BUY" if o["side"] == "long" else "🔴 SELL"
        tps = " → ".join(str(t) for t in o["targets"])
        lines.append(f"{arrow} limit `{o['entry']}`  SL `{o['stop']}`  "
                     f"[{o['conviction']} {o['score']}]\n  TP {tps}\n  _{o['reason']}_")
    if not conf["orders"]:
        lines.append("_no confluence limit cleared the score threshold_")
    return "\n".join(lines)
