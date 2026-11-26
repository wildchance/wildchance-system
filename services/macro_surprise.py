"""Macro surprise layer — growth / inflation / jobs / rates per currency, EdgeFinder-style.

Every released number (actual vs forecast; vs previous when no forecast) becomes a
signed vote for its currency and is REMEMBERED until the same indicator prints again:

    +1 beat · +2 big beat · −1 miss · −2 big miss · 0 in line
    (unemployment rate and jobless claims are inverted: a lower print is a beat)

Sources, all already in the system: the stored Forex Factory calendar / owner paste
(services.econ_calendar) and the EA's MT5 calendar (actuals the moment they print).

  growth    GDP · manufacturing PMI · services PMI · retail sales · consumer confidence
  inflation CPI · PPI · PCE
  jobs      NFP / employment change · unemployment rate · jobless claims · ADP · JOLTS
  rates     policy-rate decision (hike +1 / cut −1, ±2 when it surprised the forecast)
"""

from __future__ import annotations

import datetime as dt
import re
import time
from typing import Dict, List, Optional, Tuple

KEY = "macro_scores"
CCYS = ("USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF")
CATEGORIES = {"growth": ("gdp", "mpmi", "spmi", "retail", "conf"),
              "inflation": ("cpi", "ppi", "pce"),
              "jobs": ("nfp", "unemp", "claims", "adp", "jolts"),
              "rates": ("rate",)}
LABELS = {"gdp": "GDP", "mpmi": "mPMI", "spmi": "sPMI", "retail": "Retail Sales", "conf": "Cnsmr Conf",
          "cpi": "CPI", "ppi": "PPI", "pce": "PCE", "nfp": "NFP", "unemp": "Unemploy. Rate",
          "claims": "Claims", "adp": "ADP", "jolts": "JOLTS", "rate": "Interest Rate"}
INVERTED = {"unemp", "claims"}

# (indicator, regex on the release title) — first match wins; order matters
_RULES: List[Tuple[str, re.Pattern]] = [
    ("adp", re.compile(r"\bADP\b", re.I)),
    ("jolts", re.compile(r"JOLTS|job openings", re.I)),
    ("claims", re.compile(r"jobless claims|unemployment claims|claimant count", re.I)),
    ("unemp", re.compile(r"unemployment rate", re.I)),
    ("nfp", re.compile(r"non-?farm (employment|payrolls)|employment change|payrolls", re.I)),
    ("pce", re.compile(r"\bPCE\b", re.I)),
    ("ppi", re.compile(r"\bPPI\b|producer price", re.I)),
    ("cpi", re.compile(r"\bCPI\b|consumer price|\bHICP\b|inflation rate", re.I)),
    ("mpmi", re.compile(r"manufacturing PMI|ISM manufacturing|manufacturing index", re.I)),
    ("spmi", re.compile(r"services PMI|ISM services|non-manufacturing|services index", re.I)),
    ("retail", re.compile(r"retail sales", re.I)),
    ("conf", re.compile(r"consumer confidence|consumer sentiment|UoM|GfK", re.I)),
    ("gdp", re.compile(r"\bGDP\b", re.I)),
    ("rate", re.compile(r"(cash|policy|bank|official|overnight|funds) rate|rate (decision|statement)|"
                        r"refinancing rate|main refi|federal funds", re.I)),
]
# |surprise| at or above this counts double (units of the release)
_BIG = {"gdp": 0.3, "mpmi": 1.5, "spmi": 1.5, "retail": 0.5, "conf": 3.0, "cpi": 0.2, "ppi": 0.3,
        "pce": 0.2, "unemp": 0.2, "rate": 0.25}
_BIG_REL = {"nfp": 0.35, "claims": 0.06, "adp": 0.5, "jolts": 0.06}     # relative to |forecast|
_SKIP = re.compile(r"speaks|speech|minutes|testifies|press conference|\bf?omc\b member|core\b.*\bm/m\b.*flash",
                   re.I)


def classify(title: str) -> Optional[str]:
    if not title or _SKIP.search(title):
        return None
    for key, rx in _RULES:
        if rx.search(title):
            return key
    return None


def num(v) -> Optional[float]:
    """'3.2%' · '215K' · '-0.3%' · '1.2M' · '52.3' · '<0.10%' → float (K and M scaled)."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").replace("<", "").replace(">", "")
    m = re.match(r"^([-+]?\d*\.?\d+)\s*([KMBT%]?)", s, re.I)
    if not m:
        return None
    x = float(m.group(1))
    return x * {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}.get(m.group(2).upper(), 1)


def surprise(ind: str, actual: float, forecast: Optional[float], previous: Optional[float]) -> int:
    ref = forecast if forecast is not None else previous
    if ref is None:
        return 0
    if ind == "rate":                                 # decision: direction first, surprise doubles it
        move = actual - (previous if previous is not None else ref)
        base = 1 if move > 0.001 else -1 if move < -0.001 else 0
        if forecast is not None and abs(actual - forecast) >= 0.1:
            base = 2 if actual > forecast else -2
        return base
    d = actual - ref
    if ind in INVERTED:
        d = -d
    if abs(d) < 1e-9:
        return 0
    big = (abs(d) >= _BIG_REL[ind] * abs(ref)) if ind in _BIG_REL else abs(d) >= _BIG.get(ind, float("inf"))
    return (2 if big else 1) * (1 if d > 0 else -1)


def _events() -> List[Dict]:
    """Released events from both calendars, normalised: ccy, title, when, actual, forecast, previous."""
    out = []
    try:
        from services import econ_calendar
        for e in econ_calendar._events:
            out.append({"ccy": e.get("ccy"), "title": e.get("title"), "when": e.get("time_utc"),
                        "actual": e.get("actual"), "forecast": e.get("forecast"), "previous": e.get("previous")})
    except Exception:
        pass
    try:
        from wildchance import wildchance_scraper as wc
        for e in wc._ea_cal.get("events") or []:
            out.append({"ccy": e.get("ccy"), "title": e.get("event"), "when": f"{e.get('date')}T{e.get('time_utc')}",
                        "actual": e.get("actual"), "forecast": e.get("forecast"), "previous": e.get("previous")})
    except Exception:
        pass
    return out


def apply(state: Dict, events: List[Dict]) -> Tuple[Dict, int]:
    """Fold released numbers into the remembered state (newest print per ccy+indicator wins)."""
    n = 0
    for e in events:
        ccy = str(e.get("ccy") or "").upper()
        if ccy not in CCYS:
            continue
        ind = classify(str(e.get("title") or ""))
        act = num(e.get("actual"))
        if not ind or act is None:
            continue
        when = str(e.get("when") or "")
        cur = state.setdefault(ccy, {}).get(ind)
        if cur and cur.get("when", "") >= when:
            continue
        fc, pv = num(e.get("forecast")), num(e.get("previous"))
        state[ccy][ind] = {"score": surprise(ind, act, fc, pv), "actual": e.get("actual"),
                           "forecast": e.get("forecast"), "previous": e.get("previous"),
                           "title": e.get("title"), "when": when}
        hist = state.setdefault("_hist", {}).setdefault(ccy, [])
        hist.append([when, state[ccy][ind]["score"]])
        state["_hist"][ccy] = hist[-200:]
        n += 1
    return state, n


async def load() -> Dict:
    from services import data_store
    v, _ = await data_store.get(KEY)
    return dict(v or {})


async def ingest() -> Dict:
    try:
        from services import econ_calendar
        await econ_calendar.ensure_loaded()
    except Exception:
        pass
    state = await load()
    state, n = apply(state, _events())
    if n:
        from services import data_store
        state["_updated"] = time.time()
        await data_store.put(KEY, state)
    return {"new_prints": n, "currencies": sorted(k for k in state if k in CCYS)}


def indicators(state: Dict, ccy: str) -> Dict[str, int]:
    return {ind: int(v.get("score", 0)) for ind, v in (state.get(ccy) or {}).items()}


def category_scores(state: Dict, ccy: str) -> Dict[str, int]:
    ind = indicators(state, ccy)
    return {cat: sum(ind.get(k, 0) for k in keys) for cat, keys in CATEGORIES.items()}


def surprise_index(state: Dict, ccy: str, days: int = 90, now: Optional[dt.datetime] = None) -> Optional[int]:
    """Share of beats minus misses over the window, −100..+100 (Citi-style, simplified)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    cut = (now - dt.timedelta(days=days)).isoformat()[:16]
    rows = [s for w, s in (state.get("_hist") or {}).get(ccy, []) if str(w)[:16] >= cut]
    if not rows:
        return None
    return round(100 * sum((1 if s > 0 else -1 if s < 0 else 0) for s in rows) / len(rows))
