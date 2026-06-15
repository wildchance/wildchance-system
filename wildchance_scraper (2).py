#!/usr/bin/env python3
"""
WILDCHANCE CONFLUENCE ENGINE — data backbone
=============================================
Three-tier cadence that feeds engine.html (writes a feed.json the dashboard reads).

  Tier 1  every 6h   -> live prices + sentiment delta (fast)
  Tier 2  daily      -> ForexFactory calendar/price scrape + myfxbook retail scrape
  Tier 3  weekly     -> CFTC COT (Traders in Financial Futures), released Fri ~15:30 ET

Design notes
------------
* COT is the slow, dominant layer. Retail (myfxbook) and price (FF) adjust conviction/timing.
* Signal = contrarian fade of retail crowd extremes, CONFIRMED by COT institutional direction,
  TRIGGERED by events (e.g. NFP surprise).
* This file is a backbone: the .fetch_* functions show exactly where each source plugs in.
  Web scraping ToS vary — prefer official endpoints (CFTC Socrata API is free & legal) and
  your broker's price feed. myfxbook/ForexFactory: respect robots.txt / use their widgets/API.

Setup (credentials live in the environment, never in this file):
  export TWELVEDATA_KEY=your_key
  export MYFXBOOK_EMAIL=you@email.com
  export MYFXBOOK_PASSWORD=your_password

Run manually:
  python wildchance_scraper.py --tier weekly
  python wildchance_scraper.py --tier daily
  python wildchance_scraper.py --tier 6h

Schedule (Linux/macOS cron — `crontab -e`). Put the exports in the crontab too,
or source a ~/.wildchance.env file, since cron runs with a bare environment:
  TWELVEDATA_KEY=your_key
  MYFXBOOK_EMAIL=you@email.com
  MYFXBOOK_PASSWORD=your_password
  CD=/full/path/to/engine
  0 */6 * * *   cd $CD && /usr/bin/python3 wildchance_scraper.py --tier 6h    >> wc.log 2>&1
  30 6 * * *    cd $CD && /usr/bin/python3 wildchance_scraper.py --tier daily  >> wc.log 2>&1
  0 21 * * 5    cd $CD && /usr/bin/python3 wildchance_scraper.py --tier weekly >> wc.log 2>&1
  # weekly runs Fri 21:00 local, after CFTC's ~15:30 ET COT release.

Schedule (Windows Task Scheduler): create three Basic Tasks, each running
  python.exe  with arguments  wildchance_scraper.py --tier {6h|daily|weekly}
  "Start in" set to the engine folder. Set the env vars under the task's
  account (setx TWELVEDATA_KEY ... etc.) so they're visible to the task.

Serve the dashboard from the same folder so it can read feed.json:
  python -m http.server 8000   ->   http://localhost:8000/wildchance_engine.html
"""

import argparse, json, os, datetime as dt
from pathlib import Path

OUT = Path("feed.json")

# ---- Twelve Data (live prices) ----
# SECURITY: key comes from the environment ONLY — no literal in this file.
#   export TWELVEDATA_KEY=your_key
# Accepts TWELVEDATA_API_KEY too, the name the rest of the system (.env) uses,
# so a single key works for both the FastAPI app and this engine.
# If unset, fetch_prices() falls back to the price snapshot instead of failing.
TWELVEDATA_KEY = os.environ.get("TWELVEDATA_KEY") or os.environ.get("TWELVEDATA_API_KEY", "")

# Price snapshot used only if the API call fails (ForexFactory quotes, Jun-5 2026).
PRICE_FALLBACK = {
    "EUR/USD": 1.15233, "GBP/USD": 1.33383, "USD/JPY": 160.236,
    "USD/CAD": 1.39402, "AUD/USD": 0.70416, "USD/CHF": 0.79625,
    "XAU/USD": 4325.66,
}

# ---- myfxbook (retail sentiment / Community Outlook) ----
# SECURITY: credentials come from environment variables ONLY — never hardcode them.
#   export MYFXBOOK_EMAIL="you@email.com"
#   export MYFXBOOK_PASSWORD="..."
# If either is unset, fetch_retail() uses the snapshot fallback instead of failing.
MYFXBOOK_EMAIL = os.environ.get("MYFXBOOK_EMAIL", "")
MYFXBOOK_PASSWORD = os.environ.get("MYFXBOOK_PASSWORD", "")

# Retail snapshot fallback (myfxbook Community Outlook, Jun-5 2026).
RETAIL_FALLBACK = {
    "EUR/USD": {"long_pct": 60, "short_pct": 40},
    "GBP/USD": {"long_pct": 61, "short_pct": 39},
    "USD/JPY": {"long_pct": 30, "short_pct": 70},
    "USD/CAD": {"long_pct": 42, "short_pct": 58},
    "AUD/USD": {"long_pct": 55, "short_pct": 45},
    "USD/CHF": {"long_pct": 38, "short_pct": 62},
    "XAU/USD": {"long_pct": 73, "short_pct": 27},
}

# ---- Gold COT cohort choice ----
# The COMEX disaggregated report has two camps you can confirm signals against:
#   "managed_money"  -> follow speculative funds (same philosophy as FX Leveraged Money).
#                       Consistent with the rest of the engine; gold is treated like FX.
#   "commercial"     -> follow Producer/Merchant hedgers ("smart money"), i.e. FADE the
#                       Managed Money crowd. Many gold COT traders prefer this.
# This only changes which net goes into the engine's `lev` (direction) field for gold.
GOLD_COT_COHORT = os.environ.get("GOLD_COT_COHORT", "managed_money")  # or "commercial"

# ---- your watchlist: pair -> (cot_market_code, sign mapping COT base-ccy dir to PAIR dir) ----
# sign = +1 if a long base-ccy COT means the PAIR goes up; -1 if it inverts (e.g. USDJPY).
WATCH = {
    "EUR/USD": dict(cot="EURO FX",          sign=+1),
    "GBP/USD": dict(cot="BRITISH POUND",    sign=+1),
    "USD/JPY": dict(cot="JAPANESE YEN",     sign=-1),
    "USD/CAD": dict(cot="CANADIAN DOLLAR",  sign=-1),
    "AUD/USD": dict(cot="AUSTRALIAN DOLLAR",sign=+1),
    "USD/CHF": dict(cot="SWISS FRANC",      sign=-1),
    "XAU/USD": dict(cot=None,               sign=+1),  # gold lives in the commodity COT report
}


# ============================================================================
# TIER 3 — WEEKLY: CFTC COT  (use the free Socrata API, no scraping needed)
# ============================================================================
def fetch_cot():
    """
    CFTC Traders in Financial Futures (TFF). Free Socrata endpoint:
      https://publicreporting.cftc.gov/resource/gpe5-46if.json
    Filter by market_and_exchange_names, order by report_date desc.

    Returns: {code: [ {d, lev, am, levL, levS, oi, chgOI}, ... 26 weeks ]}
    """
    import urllib.request, urllib.parse
    base = "https://publicreporting.cftc.gov/resource/gpe5-46if.json"
    out = {}
    name_map = {
        "EUR": "EURO FX - CHICAGO MERCANTILE EXCHANGE",
        "GBP": "BRITISH POUND - CHICAGO MERCANTILE EXCHANGE",
        "JPY": "JAPANESE YEN - CHICAGO MERCANTILE EXCHANGE",
        "CAD": "CANADIAN DOLLAR - CHICAGO MERCANTILE EXCHANGE",
        "AUD": "AUSTRALIAN DOLLAR - CHICAGO MERCANTILE EXCHANGE",
        "CHF": "SWISS FRANC - CHICAGO MERCANTILE EXCHANGE",
    }
    for code, market in name_map.items():
        q = {
            "$where": f"market_and_exchange_names='{market}'",
            "$order": "report_date_as_yyyy_mm_dd DESC",
            "$limit": "26",
        }
        url = base + "?" + urllib.parse.urlencode(q)
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                rows = json.load(r)
        except Exception as e:
            print(f"[COT] {code} fetch failed: {e}")
            continue
        series = []
        for r in reversed(rows):  # oldest -> newest
            levL = int(float(r.get("lev_money_positions_long", 0)))
            levS = int(float(r.get("lev_money_positions_short", 0)))
            amL  = int(float(r.get("asset_mgr_positions_long", 0)))
            amS  = int(float(r.get("asset_mgr_positions_short", 0)))
            series.append({
                "d": r["report_date_as_yyyy_mm_dd"][:10],
                "lev": levL - levS, "am": amL - amS,
                "levL": levL, "levS": levS,
                "oi": int(float(r.get("open_interest_all", 0))),
                "chgOI": int(float(r.get("change_in_open_interest_all", 0))),
            })
        out[code] = series
        print(f"[COT] {code}: {len(series)} weeks, latest {series[-1]['d']} net={series[-1]['lev']}")
    return out


def fetch_gold_cot():
    """
    Gold isn't in the Traders-in-Financial-Futures report — it lives in the
    DISAGGREGATED commodity COT (COMEX). Free Socrata endpoint:
      https://publicreporting.cftc.gov/resource/72hh-3qpy.json   (futures-only)
    The speculative cohort there is "Managed Money" (the commodity analogue of
    the Leveraged Money cohort in TFF), so we map it onto the same {lev,...}
    shape the engine already understands.

    Returns: {"XAU": [ {d, lev, am, levL, levS, oi, chgOI}, ...26 weeks ]} or {}.
      lev  = Managed Money net   (spec positioning, analogous to TFF Lev Money)
      am   = Producer/Merchant net (commercial hedgers — the "smart money" leg)
    """
    import urllib.request, urllib.parse
    base = "https://publicreporting.cftc.gov/resource/72hh-3qpy.json"
    market = "GOLD - COMMODITY EXCHANGE INC."
    q = {
        "$where": f"market_and_exchange_names='{market}'",
        "$order": "report_date_as_yyyy_mm_dd DESC",
        "$limit": "26",
    }
    url = base + "?" + urllib.parse.urlencode(q)
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            rows = json.load(r)
    except Exception as e:
        print(f"[COT] XAU (gold) fetch failed: {e}")
        return {}
    series = []
    for r in reversed(rows):  # oldest -> newest
        mmL = int(float(r.get("m_money_positions_long_all", 0)))
        mmS = int(float(r.get("m_money_positions_short_all", 0)))
        pmL = int(float(r.get("prod_merc_positions_long", 0)))
        pmS = int(float(r.get("prod_merc_positions_short", 0)))
        mm_net = mmL - mmS          # Managed Money (speculative)
        pm_net = pmL - pmS          # Producer/Merchant (commercial hedgers)
        # `lev` is the DIRECTION field the engine reads. Pick the cohort per config.
        if GOLD_COT_COHORT == "commercial":
            lev_net, levL_, levS_ = pm_net, pmL, pmS
            am_net = mm_net          # keep the other camp visible in `am`
        else:  # managed_money (default) — gold treated like FX spec money
            lev_net, levL_, levS_ = mm_net, mmL, mmS
            am_net = pm_net
        series.append({
            "d": r["report_date_as_yyyy_mm_dd"][:10],
            "lev": lev_net, "am": am_net,
            "levL": levL_, "levS": levS_,
            "oi": int(float(r.get("open_interest_all", 0))),
            "chgOI": int(float(r.get("change_in_open_interest_all", 0))),
        })
    if not series:
        print("[COT] XAU (gold): no rows returned")
        return {}
    print(f"[COT] XAU (gold, {GOLD_COT_COHORT}): {len(series)} weeks, "
          f"latest {series[-1]['d']} net={series[-1]['lev']}")
    return {"XAU": series}
def fetch_retail():
    """
    myfxbook Community Outlook via the official API.
      1. login.json(email,password)            -> session token
      2. get-community-outlook.json(session)    -> per-symbol long/short %
      3. logout.json(session)                   -> release the session
    Maps myfxbook symbol names (EURUSD, XAUUSD, ...) to our WATCH pairs.
    Falls back to RETAIL_FALLBACK if creds are missing or any call fails,
    so the engine never starves.

    NOTE: the iframe/<script> widgets you copied are display-only — JS can't read
    cross-domain iframe content — so this API path is what actually feeds signals.
    """
    if not (MYFXBOOK_EMAIL and MYFXBOOK_PASSWORD):
        print("[RETAIL] MYFXBOOK_EMAIL/PASSWORD not set — using fallback snapshot")
        return {k: dict(v) for k, v in RETAIL_FALLBACK.items()}

    import urllib.request, urllib.parse
    base = "https://www.myfxbook.com/api"

    def _get(path, params):
        url = f"{base}/{path}?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.load(r)

    session = None
    try:
        login = _get("login.json", {"email": MYFXBOOK_EMAIL, "password": MYFXBOOK_PASSWORD})
        if login.get("error"):
            print(f"[RETAIL] login failed: {login.get('message')} — using fallback")
            return {k: dict(v) for k, v in RETAIL_FALLBACK.items()}
        session = login["session"]

        data = _get("get-community-outlook.json", {"session": session})
        if data.get("error"):
            print(f"[RETAIL] outlook failed: {data.get('message')} — using fallback")
            return {k: dict(v) for k, v in RETAIL_FALLBACK.items()}

        # map "EURUSD" -> "EUR/USD" for our watchlist
        want = {p.replace("/", ""): p for p in WATCH}  # {'EURUSD':'EUR/USD', 'XAUUSD':'XAU/USD', ...}
        out = {}
        for sym in data.get("symbols", []):
            raw = sym.get("name", "")
            pair = want.get(raw)
            if not pair:
                continue
            lp = round(float(sym.get("longPercentage", 0)))
            sp = round(float(sym.get("shortPercentage", 100 - lp)))
            out[pair] = {"long_pct": lp, "short_pct": sp}
        # backfill any watchlist pair myfxbook didn't return
        for p in WATCH:
            out.setdefault(p, dict(RETAIL_FALLBACK[p]))
        live = sum(1 for p in WATCH if out[p] != RETAIL_FALLBACK.get(p))
        print(f"[RETAIL] myfxbook: {len(out)} pairs ({live} live)")
        return out
    except Exception as e:
        print(f"[RETAIL] myfxbook error ({e}) — using fallback snapshot")
        return {k: dict(v) for k, v in RETAIL_FALLBACK.items()}
    finally:
        if session:
            try:
                _get("logout.json", {"session": session})
            except Exception:
                pass

CALENDAR_FALLBACK = [
    {"date": "2026-06-05", "ccy": "USD", "event": "Non-Farm Employment Change",
     "actual": 172, "forecast": 85, "previous": 179, "impact": "high"},
    {"date": "2026-06-05", "ccy": "USD", "event": "Unemployment Rate",
     "actual": 4.3, "forecast": 4.3, "previous": 4.3, "impact": "high"},
    {"date": "2026-06-05", "ccy": "USD", "event": "Average Hourly Earnings m/m",
     "actual": 0.3, "forecast": 0.3, "previous": 0.2, "impact": "high"},
    {"date": "2026-06-05", "ccy": "CAD", "event": "Employment Change",
     "actual": 87.8, "forecast": 10.6, "previous": -17.7, "impact": "high"},
]

def _num(v):
    """Parse FF calendar strings into numbers.
    Jobs-style values use K as the base unit (the scale score_nfp expects):
      '172K'->172, '1.2M'->1200, '4.3%'->4.3, '-17.7K'->-17.7, ''->None.
    """
    if v is None:
        return None
    s = str(v).strip()
    if s in ("", "-"):
        return None
    s = s.replace("%", "").replace(",", "").replace("<", "").replace(">", "")
    # K is the base unit; M = 1000*K, B = 1e6*K
    scale = 1.0
    if s and s[-1] in "KkMmBb":
        scale = {"k": 1.0, "m": 1e3, "b": 1e6}[s[-1].lower()]
        s = s[:-1]
    try:
        return round(float(s) * scale, 4)
    except ValueError:
        return None

def fetch_calendar():
    """
    Parseable economic calendar from FairEconomy's public ForexFactory feed:
      https://nfs.faireconomy.media/ff_calendar_thisweek.json
    This mirrors the ForexFactory calendar as JSON (no scraping of the HTML page).
    Normalizes each event into {date, ccy, event, actual, forecast, previous, impact}
    with numeric actual/forecast/previous so score_nfp() can compute surprises.
    Falls back to CALENDAR_FALLBACK (this week's prints) on any error.

    Note: jobs prints like '172K' are normalized to 172 (thousands) to match the
    snapshot scale the NFP scorer expects.
    """
    import urllib.request
    url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "wildchance-engine/1.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = json.load(r)
    except Exception as e:
        print(f"[CAL] calendar fetch failed ({e}) — using fallback snapshot")
        return [dict(e) for e in CALENDAR_FALLBACK]

    events = []
    for it in raw:
        events.append({
            "date": (it.get("date", "") or "")[:10],
            "ccy": it.get("country", ""),       # feed uses "country" for the currency code
            "event": it.get("title", ""),
            "actual": _num(it.get("actual")),
            "forecast": _num(it.get("forecast")),
            "previous": _num(it.get("previous")),
            "impact": (it.get("impact", "") or "").lower(),
        })
    if not events:
        print("[CAL] calendar returned 0 events — using fallback snapshot")
        return [dict(e) for e in CALENDAR_FALLBACK]
    highs = sum(1 for e in events if e["impact"] in ("high", "red"))
    print(f"[CAL] faireconomy: {len(events)} events ({highs} high-impact)")
    return events


# ============================================================================
# TIER 1 — 6H: live prices + sentiment delta
# ============================================================================
def fetch_prices():
    """
    Live prices from Twelve Data /price (batch). Returns {pair: float}.
    Falls back to PRICE_FALLBACK on any error so the engine never starves.

    Free-tier note: 8 req/min, 800/day. We send ONE batched request for all
    symbols (counts as 7 credits — one per symbol — so a 6h cadence is well safe).
    Batch response shape: {"EUR/USD": {"price": "1.15"}, "GBP/USD": {...}, ...}
    Single-symbol shape:  {"price": "1.15"}
    Error shape:          {"code": 4xx, "message": "...", "status": "error"}
    """
    import urllib.request, urllib.parse
    if not TWELVEDATA_KEY:
        print("[PRICE] TWELVEDATA_KEY not set — using fallback snapshot")
        return dict(PRICE_FALLBACK)
    symbols = list(WATCH.keys())
    url = ("https://api.twelvedata.com/price?symbol="
           + urllib.parse.quote(",".join(symbols))
           + "&apikey=" + TWELVEDATA_KEY)
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            data = json.load(r)
    except Exception as e:
        print(f"[PRICE] Twelve Data fetch failed ({e}) — using fallback snapshot")
        return dict(PRICE_FALLBACK)

    # API-level error returned as 200 with a status field
    if isinstance(data, dict) and data.get("status") == "error":
        print(f"[PRICE] Twelve Data error: {data.get('message')} — using fallback")
        return dict(PRICE_FALLBACK)

    out = {}
    if len(symbols) == 1 and "price" in data:           # single-symbol shape
        out[symbols[0]] = float(data["price"])
    else:                                                # batch shape
        for pair in symbols:
            node = data.get(pair)
            if isinstance(node, dict) and "price" in node:
                try:
                    out[pair] = float(node["price"])
                except (TypeError, ValueError):
                    pass
    # backfill any missing symbol from the snapshot so downstream never KeyErrors
    for pair in symbols:
        out.setdefault(pair, PRICE_FALLBACK[pair])
    got = sum(1 for p in symbols if out[p] != PRICE_FALLBACK.get(p))
    print(f"[PRICE] Twelve Data: {len(out)} symbols ({got} live)")
    return out


# ============================================================================
# SIGNAL ENGINE  (mirror of the dashboard logic, server-side)
# ============================================================================
def pct_rank(arr, val):
    return sum(1 for x in arr if x <= val) / len(arr) if arr else 0.5

def cot_state(cot_by_code, base_code):
    s = cot_by_code.get(base_code)
    if not s:
        return None
    last, prev = s[-1], s[-2]
    nets = [r["lev"] for r in s]
    return {"net": last["lev"], "dwk": last["lev"] - prev["lev"],
            "rank": pct_rank(nets, last["lev"]), "dir": 1 if last["lev"] > 0 else -1}

def base_code(pair):
    # map pair -> COT base ccy code we stored
    m = {"EUR/USD": "EUR", "GBP/USD": "GBP", "USD/JPY": "JPY",
         "USD/CAD": "CAD", "AUD/USD": "AUD", "USD/CHF": "CHF", "XAU/USD": "XAU"}
    return m.get(pair)

def evaluate(pair, price, retail, cot_by_code):
    rl = retail["long_pct"]; rs = retail["short_pct"]
    extreme = max(rl, rs)
    retail_dir = 1 if rl > rs else -1
    contrarian = -retail_dir
    c = cot_state(cot_by_code, base_code(pair))
    sign = WATCH[pair]["sign"]
    cot_pair_dir = c["dir"] * sign if c else 0
    agree = (contrarian == cot_pair_dir) if c else False
    if extreme < 58:
        verdict, conf = "FLAT", max(8, int(40 + (extreme - 50) * 1.4))
    elif c and not agree:
        verdict, conf = "WATCH", max(8, int(35 + (extreme - 60) * 1.2))
    else:
        verdict = "LONG" if contrarian > 0 else "SHORT"
        conf = int(50 + (extreme - 50) * 1.6 + (abs(c["rank"] - 0.5) * 40 if c else 0))
    conf = max(8, min(94, conf))
    return {"pair": pair, "price": price, "retail_long": rl, "retail_extreme": extreme,
            "contrarian": "long" if contrarian > 0 else "short",
            "cot_net": c["net"] if c else None, "cot_dwk": c["dwk"] if c else None,
            "cot_rank": round(c["rank"], 2) if c else None,
            "verdict": verdict, "confidence": conf, "cot_agrees": agree}

def score_nfp(events, signals):
    # pick the most recent NFP entry that actually has an actual + forecast
    nfps = [e for e in events if "Non-Farm" in (e.get("event") or "")
            and e.get("actual") is not None and e.get("forecast") is not None]
    if not nfps:
        return {}
    nfp = sorted(nfps, key=lambda e: e.get("date", ""))[-1]
    surprise = round(nfp["actual"] - nfp["forecast"], 2)
    usd_dir = 1 if surprise > 0 else -1  # hot NFP -> USD up
    return {"date": nfp.get("date"), "actual": nfp["actual"], "forecast": nfp["forecast"],
            "previous": nfp.get("previous"),
            "surprise_k": surprise, "usd_direction": "up" if usd_dir > 0 else "down",
            "beat_pct": round(surprise / nfp["forecast"] * 100) if nfp["forecast"] else None,
            "note": "Hot print pushes Fed cuts further out -> USD bid; "
                    "confirm against COT before fading retail."}


def run(tier):
    feed = {}
    if OUT.exists():
        try: feed = json.loads(OUT.read_text())
        except Exception: feed = {}

    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if tier == "weekly":
        cot = fetch_cot()
        cot.update(fetch_gold_cot())   # adds {"XAU": [...]} from the COMEX disaggregated report
        feed["cot"] = cot
        feed["cot_updated"] = now
    if tier in ("weekly", "daily"):
        feed["retail"] = fetch_retail()
        feed["calendar"] = fetch_calendar()
        feed["daily_updated"] = now
    # 6h always refreshes price + recomputes signals
    feed["prices"] = fetch_prices()
    feed["price_updated"] = now

    cot_by_code = feed.get("cot", {})
    retail = feed.get("retail", fetch_retail())
    prices = feed["prices"]
    feed["signals"] = [evaluate(p, prices[p], retail[p], cot_by_code) for p in WATCH]
    feed["nfp"] = score_nfp(feed.get("calendar", fetch_calendar()), feed["signals"])
    feed["tier_last_run"] = {**feed.get("tier_last_run", {}), tier: now}

    OUT.write_text(json.dumps(feed, indent=2))
    print(f"[{tier}] feed.json written — {len(feed['signals'])} signals @ {now}")
    for s in feed["signals"]:
        print(f"  {s['pair']:8} {s['verdict']:6} conf {s['confidence']:>2}%  "
              f"retail {s['retail_long']}%L  COTnet {s['cot_net']}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", choices=["6h", "daily", "weekly"], default="6h")
    run(ap.parse_args().tier)
