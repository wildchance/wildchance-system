"""High-impact news guard — FLAG-ONLY, all currencies, all tier-1 events.

Mean-reversion & fade setups get inverted by scheduled news (rate decisions,
CPI, jobs, GDP). This does NOT block a signal — it annotates a setup whose pair
has a high-impact release near it, per the pair's OWN currencies, so you fade at
your discretion / reduce size / stand aside.

Sources (independent, best-effort):
  1. Deterministic NFP clock (first Friday) for USD — no network.
  2. The ForexFactory calendar feed (wildchance_scraper.fetch_calendar) for every
     other tier-1 event (FOMC/CPI/ECB/BOE/BOC/RBA/RBNZ/…), matched to the pair's
     currencies.

Config:
  USDJPY_NEWS_WINDOW_DAYS   ± days around an event to flag (default 1)
"""

from __future__ import annotations

import asyncio
import datetime as dt
from typing import List, Optional, Set

from decouple import config

NEWS_WINDOW_DAYS = config("USDJPY_NEWS_WINDOW_DAYS", default=1, cast=int)
# Master switch — set NEWS_WARNINGS_ENABLED=false to remove the news line entirely
# (also disables the same-day tier-1 block that reuses this function).
NEWS_WARNINGS_ENABLED = config("NEWS_WARNINGS_ENABLED", default="true").lower() == "true"

# Trailing release-frequency tokens — "Core CPI m/m" and "Core CPI y/y" are the SAME
# print, so we strip these to dedupe one release down to one line.
_FREQ_SUFFIXES = (" m/m", " y/y", " q/q", " mom", " yoy", " qoq")


def _dedupe_events(hits: List[str]) -> List[str]:
    """Collapse the same release listed under several frequencies into one line.

    'USD Core CPI m/m', 'USD Core CPI y/y', 'USD CPI m/m' → 'USD Core CPI',
    'USD CPI' (order preserved). Prevents one CPI day showing as three warnings.
    """
    out: List[str] = []
    seen: set = set()
    for h in hits:
        base = h
        low = base.lower()
        for suf in _FREQ_SUFFIXES:
            if low.endswith(suf):
                base = base[: -len(suf)].rstrip()
                break
        key = base.lower()
        if key not in seen:
            seen.add(key)
            out.append(base)
    return out

# Tier-1 event substrings (currency-agnostic). Central-bank rate decisions, CPI,
# employment, GDP, and the Fed's own set.
_HIGH_IMPACT = (
    "non-farm", "nonfarm", "non farm", "payroll", "unemployment",
    "employment change", "jobless", "claimant count",
    "cash rate", "official bank rate", "federal funds", "refinancing rate",
    "overnight rate", "rate statement", "rate decision", "monetary policy",
    "official cash rate", "bank rate", "press conference",
    "cpi", "inflation", "gdp", "retail sales", "fomc", "ppi",
)

# Metals / indices are USD-quoted risk assets → driven by USD data.
_USD_PROXY_PREFIXES = ("XAU", "XAG", "GOLD", "XPT", "XPD", "WTI", "USOIL",
                       "SPX", "US30", "NAS", "US500", "NDX")


def symbol_currencies(symbol: str) -> Set[str]:
    """The currencies whose news moves this instrument.

    EURUSD → {EUR, USD}; USDJPY → {USD, JPY}; XAU/USD, NAS100 → {USD}.
    """
    s = (symbol or "").upper().replace("/", "").replace(" ", "").replace("-", "")
    if any(s.startswith(p) for p in _USD_PROXY_PREFIXES):
        return {"USD"}
    if len(s) >= 6 and s[:6].isalpha():
        return {s[:3], s[3:6]}
    if len(s) == 3 and s.isalpha():
        return {s}
    return {"USD"}   # safe default: still flag on USD tier-1 events


def _first_friday(year: int, month: int) -> dt.date:
    d = dt.date(year, month, 1)
    return d + dt.timedelta(days=(4 - d.weekday()) % 7)   # weekday 4 == Friday


def _nearby_nfp(for_date: dt.date) -> List[dt.date]:
    out: List[dt.date] = []
    for delta in (-1, 0, 1):
        idx = (for_date.month - 1) + delta
        y = for_date.year + idx // 12
        m = idx % 12 + 1
        out.append(_first_friday(y, m))
    return out


def nfp_window(for_date: dt.date, win: int = 1) -> Optional[str]:
    """The NFP date if ``for_date`` is within ``win`` days of one, else None.

    Deterministic (first-Friday clock, no network) — used by the signal audit to
    tag which fires landed in a jobs-report window.
    """
    for nfp in _nearby_nfp(for_date):
        if abs((nfp - for_date).days) <= win:
            return nfp.isoformat()
    return None


def _parse_date(v) -> Optional[dt.date]:
    if not v:
        return None
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _event_ccy(ev: dict) -> str:
    return (ev.get("ccy") or ev.get("country") or "").upper()


def _is_high_impact(ev: dict) -> bool:
    name = (ev.get("event") or ev.get("title") or "").lower()
    impact = (ev.get("impact") or "").lower()
    return "high" in impact or any(k in name for k in _HIGH_IMPACT)


def filter_high_impact(events, currencies: Optional[Set[str]] = None) -> List[dict]:
    """Normalize + keep only tier-1 events, optionally limited to ``currencies``.

    Returns rows sorted by date: {date, ccy, event, impact, actual, forecast,
    previous}. Powers the real-time calendar endpoint.
    """
    out: List[dict] = []
    for ev in events or []:
        ccy = _event_ccy(ev)
        if currencies and ccy not in currencies:
            continue
        if not _is_high_impact(ev):
            continue
        d = _parse_date(ev.get("date"))
        out.append({
            "date": d.isoformat() if d else None,
            "ccy": ccy,
            "event": ev.get("event") or ev.get("title"),
            "impact": ev.get("impact"),
            "actual": ev.get("actual"),
            "forecast": ev.get("forecast"),
            "previous": ev.get("previous"),
        })
    out.sort(key=lambda r: (r["date"] or "9999", r["ccy"]))
    return out


async def high_impact_calendar(currencies: Optional[Set[str]] = None) -> List[dict]:
    """Live tier-1 calendar from the ForexFactory feed (best-effort, this week)."""
    try:
        from wildchance.wildchance_scraper import fetch_calendar
        events = await asyncio.to_thread(fetch_calendar)
    except Exception:
        events = []
    return filter_high_impact(events, currencies)


async def news_flag(for_date: dt.date, symbol: str = "USD/JPY",
                    win: Optional[int] = None) -> Optional[str]:
    """Warning string if a tier-1 event for ``symbol``'s currencies is UPCOMING
    within ``win`` days (default USDJPY_NEWS_WINDOW_DAYS), else None.

    FORWARD-LOOKING: only events that are today or up to ``win`` days AHEAD are
    flagged — ``0 <= (event - for_date).days <= win``. A print that has already
    released is no longer a fade-inversion risk, so past events are never flagged
    (this is the fix for the alert repeating an outcome we already had). Callers
    decide whether to flag or block — pass win=0 to test today only.
    """
    if not NEWS_WARNINGS_ENABLED:
        return None
    win = max(0, NEWS_WINDOW_DAYS if win is None else win)
    ccys = symbol_currencies(symbol)
    hits: List[str] = []

    # 1) Deterministic NFP clock (only if the pair carries USD).
    if "USD" in ccys:
        for nfp in _nearby_nfp(for_date):
            if 0 <= (nfp - for_date).days <= win:      # today .. win days ahead
                hits.append(f"USD NFP {nfp.isoformat()}")

    # 2) Live calendar feed, matched to the pair's currencies (best-effort).
    try:
        from wildchance.wildchance_scraper import fetch_calendar
        events = await asyncio.to_thread(fetch_calendar)
        for ev in events or []:
            d = _parse_date(ev.get("date"))
            ccy = _event_ccy(ev)
            if (d and ccy in ccys and _is_high_impact(ev)
                    and 0 <= (d - for_date).days <= win):   # upcoming only
                nm = ev.get("event") or ev.get("title") or "high-impact"
                hits.append(f"{ccy} {nm}")
    except Exception:
        pass

    if not hits:
        return None
    uniq = _dedupe_events(list(dict.fromkeys(hits)))[:4]
    horizon = "today" if win == 0 else f"in the next {win}d"
    return (
        f"⚠️ High-impact news {horizon} for {'/'.join(sorted(ccys))} "
        f"({'; '.join(uniq)}) — scheduled prints can whip price & widen spreads. "
        "Trade at your discretion / reduce size."
    )
