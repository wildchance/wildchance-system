"""Economic calendar — automatic, adaptive event blackouts.

Source: Forex Factory's public weekly export (the same calendar the owner reads),
  https://nfs.faireconomy.media/ff_calendar_thisweek.json   (+ _nextweek)
refreshed every 6 h (one small request each — never more often), merged into a
60-day history persisted in Neon (data_store 'econ_calendar').

Blackouts (services.trade_gate.blackout): no NEW entries from CAL_BEFORE_MIN (30)
before to CAL_AFTER_MIN (60) after any HIGH-impact release for a symbol that carries
the event's currency (USD also covers gold, BTC, US indices and US stocks).

ADAPTS: after each release the move it caused is measured from hourly bars (range of
the release hour + the next, in pips) and kept per event × market (data_store
'event_moves'). A MEDIUM-impact event whose average move reaches CAL_PROMOTE_PIPS
(25) is promoted to a blackout too; the weekly card shows each event's typical move,
so the calendar learns which releases actually move each market.
"""

from __future__ import annotations

import datetime as dt
from typing import Dict, List, Optional

from decouple import config

FEEDS = ("https://nfs.faireconomy.media/ff_calendar_thisweek.json",
         "https://nfs.faireconomy.media/ff_calendar_nextweek.json")
BEFORE_MIN = int(config("CAL_BEFORE_MIN", default="30"))
AFTER_MIN = int(config("CAL_AFTER_MIN", default="60"))
PROMOTE_PIPS = float(config("CAL_PROMOTE_PIPS", default="25"))
KEEP_DAYS = 60
MIN_REFRESH_MIN = int(config("CAL_MIN_REFRESH_MIN", default="60"))   # never ask more often
BACKOFF_H = float(config("CAL_BACKOFF_H", default="3"))             # after HTTP 429 (rate limited)
_meta: Dict = {"last_try": 0.0, "last_ok": 0.0, "blocked_until": 0.0}
# markets used to measure each currency's event moves
PROBES = {"USD": ("EUR/USD", "XAU/USD"), "EUR": ("EUR/USD",), "GBP": ("GBP/USD",), "JPY": ("USD/JPY",),
          "AUD": ("AUD/USD",), "NZD": ("NZD/USD",), "CAD": ("USD/CAD",), "CHF": ("USD/CHF",)}
USD_ASSETS = ("XAU", "XAG", "BTC", "ETH", "NAS", "US30", "US100", "US500", "SPX", "US2000", "NVDA")

_events: List[Dict] = []
_moves: Dict[str, Dict] = {}
_loaded = False


def parse(rows: List[Dict]) -> List[Dict]:
    out = []
    for r in rows or []:
        try:
            t = dt.datetime.fromisoformat(str(r["date"])).astimezone(dt.timezone.utc)
        except (KeyError, ValueError, TypeError):
            continue
        out.append({"title": str(r.get("title") or "").strip(), "ccy": str(r.get("country") or "").upper(),
                    "time_utc": t.isoformat(timespec="minutes"), "impact": str(r.get("impact") or ""),
                    "forecast": r.get("forecast") or "", "previous": r.get("previous") or "",
                    "actual": r.get("actual") or ""})
    return out


def merge(old: List[Dict], new: List[Dict], now: Optional[dt.datetime] = None) -> List[Dict]:
    now = now or dt.datetime.now(dt.timezone.utc)
    keep = (now - dt.timedelta(days=KEEP_DAYS)).isoformat()
    by = {(e["title"], e["ccy"], e["time_utc"]): e for e in old if e["time_utc"] >= keep}
    for e in new:
        k = (e["title"], e["ccy"], e["time_utc"])
        by[k] = {**by.get(k, {}), **{x: v for x, v in e.items() if v != "" or x not in by.get(k, {})}}
    return sorted(by.values(), key=lambda e: e["time_utc"])


def currencies(symbol: str) -> set:
    s = symbol.upper().replace("/", "")
    if len(s) == 6 and s.isalpha() and not s.startswith(USD_ASSETS):
        return {s[:3], s[3:]}
    return {"USD"} | ({s[3:]} if len(s) == 6 and s.isalpha() else set())


def move_key(e: Dict, symbol: str) -> str:
    return f"{e['ccy']}|{e['title']}|{symbol.upper().replace('/', '')}"


def typical_move(e: Dict) -> Optional[Dict]:
    for sym in PROBES.get(e["ccy"], ()):
        m = _moves.get(move_key(e, sym))
        if m and m.get("n"):
            return {"symbol": sym, **m}
    return None


def is_blocking(e: Dict) -> bool:
    if e["impact"] == "High":
        return True
    if e["impact"] == "Medium":
        m = typical_move(e)
        return bool(m and m["n"] >= 2 and m["avg_pips"] >= PROMOTE_PIPS)
    return False


def blackout(symbol: str, now: Optional[dt.datetime] = None, events: Optional[List[Dict]] = None) -> Optional[str]:
    now = now or dt.datetime.now(dt.timezone.utc)
    ccys = currencies(symbol)
    for e in (events if events is not None else _events):
        if e["ccy"] not in ccys or not is_blocking(e):
            continue
        t = dt.datetime.fromisoformat(e["time_utc"])
        if t - dt.timedelta(minutes=BEFORE_MIN) <= now <= t + dt.timedelta(minutes=AFTER_MIN):
            return f"event blackout: {e['ccy']} {e['title']} {t:%H:%M} UTC (calendar)"
    return None


async def ensure_loaded() -> None:
    global _loaded, _events, _moves
    if _loaded:
        return
    from services import data_store
    ev, _ = await data_store.get("econ_calendar")
    mv, _ = await data_store.get("event_moves")
    _events, _moves, _loaded = list(ev or []), dict(mv or {}), True


def status() -> Dict:
    import time as _t
    now = _t.time()
    last_ok = _meta["last_ok"]
    try:                                   # the source-health registry is persisted — survives restarts
        from services.data_feeds import _health
        last_ok = max(last_ok, float((_health.get("forexfactory_calendar") or {}).get("last_ok") or 0))
    except Exception:
        pass
    return {"stored": len(_events), "high_upcoming": len(upcoming(7, high_only=True)),
            "last_ok_h": round((now - last_ok) / 3600, 1) if last_ok else None,
            "backing_off_min": max(0, round((_meta["blocked_until"] - now) / 60)) or None}


async def refresh(force: bool = False) -> Dict:
    """Polite: at most once per CAL_MIN_REFRESH_MIN, and CAL_BACKOFF_H hours after an HTTP 429."""
    global _events
    import time as _t
    await ensure_loaded()
    from services import data_store
    from services.data_feeds import _get, _health
    now = _t.time()
    if not force and (now < _meta["blocked_until"] or now - _meta["last_try"] < MIN_REFRESH_MIN * 60):
        return {"fetched": 0, "skipped": "polite interval / back-off", **status()}
    _meta["last_try"] = now
    got = []
    for url in FEEDS:
        # next week's file only appears late in the week (404 before) — tracked apart so it
        # never drags the main calendar source's health score
        data = await _get("forexfactory_calendar" if "thisweek" in url else "forexfactory_nextweek", url)
        if isinstance(data, list):
            got += parse(data)
        if "thisweek" in url and data is None and "429" in str((_health.get("forexfactory_calendar") or {})
                                                                   .get("last_err", "")):
            _meta["blocked_until"] = now + BACKOFF_H * 3600
            break
    if got:
        _meta["last_ok"] = now
        _events = merge(_events, got)
        await data_store.put("econ_calendar", _events)
    return {"fetched": len(got), **status()}


def upcoming(days: int = 7, high_only: bool = False, now: Optional[dt.datetime] = None) -> List[Dict]:
    now = now or dt.datetime.now(dt.timezone.utc)
    a, b = now.isoformat(), (now + dt.timedelta(days=days)).isoformat()
    return [{**e, "blocks": is_blocking(e), "typical": typical_move(e)} for e in _events
            if a <= e["time_utc"] <= b and (not high_only or is_blocking(e))]


def measure(bars: List[Dict], t: dt.datetime, pip: float) -> Optional[float]:
    """Range (pips) of the release hour and the next one. bars: UTC hourly, oldest-first."""
    h0 = t.replace(minute=0, second=0, microsecond=0)
    sel = [b for b in bars if h0 <= dt.datetime.fromisoformat(str(b["time"])[:19]).replace(tzinfo=dt.timezone.utc)
           < h0 + dt.timedelta(hours=2)]
    if not sel or not pip:
        return None
    return round((max(b["high"] for b in sel) - min(b["low"] for b in sel)) / pip, 1)


async def learn(now: Optional[dt.datetime] = None) -> Dict:
    """Measure the moves of High/Medium releases from the last 3 days not yet learned."""
    await ensure_loaded()
    from propfirm.target_lots import pip_size
    from services import data_store
    from services.ohlc_service import fetch_ohlc_raw
    now = now or dt.datetime.now(dt.timezone.utc)
    lo, hi = (now - dt.timedelta(days=3)).isoformat(), (now - dt.timedelta(hours=2)).isoformat()
    todo = [e for e in _events if lo <= e["time_utc"] <= hi and e["impact"] in ("High", "Medium")
            and not e.get("learned")]
    bars_cache: Dict[str, List[Dict]] = {}
    learned = []
    for e in todo:
        t = dt.datetime.fromisoformat(e["time_utc"])
        for sym in PROBES.get(e["ccy"], ()):
            if sym not in bars_cache:
                try:
                    bars_cache[sym] = await fetch_ohlc_raw(sym, "1h", 96, timezone="UTC") or []
                except Exception:
                    bars_cache[sym] = []
            pips = measure(bars_cache[sym], t, pip_size(sym) or 0.0001)
            if pips is None:
                continue
            m = _moves.setdefault(move_key(e, sym), {"n": 0, "avg_pips": 0.0, "max_pips": 0.0})
            m["avg_pips"] = round((m["avg_pips"] * m["n"] + pips) / (m["n"] + 1), 1)
            m["n"] += 1
            m["max_pips"] = max(m["max_pips"], pips)
            learned.append({"event": f"{e['ccy']} {e['title']}", "symbol": sym, "pips": pips})
        e["learned"] = True
    if todo:
        await data_store.put("event_moves", _moves)
        await data_store.put("econ_calendar", _events)
    return {"checked": len(todo), "learned": learned}


def week_card(events: List[Dict]) -> str:
    if not events:
        return "📅 *Week ahead*: no high-impact releases."
    lines = ["📅 *Week ahead — high-impact releases* (UTC; no new entries 30 min before → 60 min after)"]
    day = None
    for e in events:
        t = dt.datetime.fromisoformat(e["time_utc"])
        if t.date() != day:
            day = t.date()
            lines.append(f"*{t:%a %d %b}*")
        tm = e.get("typical")
        extra = f" · usually {tm['avg_pips']:g} pips on {tm['symbol']}" if tm else ""
        fc = f" (f {e['forecast']} / p {e['previous']})" if e.get("forecast") else ""
        lines.append(f"  {t:%H:%M} {e['ccy']} {e['title']}{fc}{extra}"
                     + ("" if e["impact"] == "High" else " ⬆ promoted"))
    return "\n".join(lines)
