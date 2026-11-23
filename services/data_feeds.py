"""Free data feeds + a health registry — diverse sources the system ranks by reliability.

PRICES (daily OHLC, oldest-first (date, o, h, l, c))
  stooq      decades of daily CSV — FX, metals, indices, oil, stocks          no key
  tiingo     FX / stocks / crypto end-of-day (TIINGO_API_KEY)                 free sign-up
  binance    crypto klines via data-api.binance.vision (works from US hosts)  no key
SENTIMENT
  alphavantage  NEWS_SENTIMENT per ticker (ALPHAVANTAGE_API_KEY, ~25/day)     free sign-up
  marketaux     news entity sentiment for stocks (MARKETAUX_API_KEY)          free sign-up
MACRO / RISK
  coingecko  global crypto cap + BTC dominance (COINGECKO_API_KEY optional)
  ecb        EUR/USD reference rate + deposit facility rate                   no key
  treasury   US yield curve (2y/10y/30y) + debt to the penny                  no key
  bls        CPI, unemployment, avg hourly earnings (BLS_API_KEY optional)
  cboe       VIX daily history                                                 no key

REGISTRY  every call records ok/fail + latency per source; ranked() orders a fallback
chain by recent success, so a source that starts failing drops down by itself, and
cross_check() compares the latest close across feeds and flags the outlier.
Parsers are pure (unit-tested offline); fetchers never raise — they return empty.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import time
from typing import Dict, List, Optional, Sequence, Tuple

import httpx
from decouple import config

Bar = Tuple[dt.date, float, float, float, float]
TIINGO_KEY = config("TIINGO_API_KEY", default=None)
AV_KEY = config("ALPHAVANTAGE_API_KEY", default=None)
MARKETAUX_KEY = config("MARKETAUX_API_KEY", default=None)
COINGECKO_KEY = config("COINGECKO_API_KEY", default=None)
BLS_KEY = config("BLS_API_KEY", default=None)
UA = {"User-Agent": "Mozilla/5.0 (data-feed)"}

# ------------------------------------------------------------------ registry
_health: Dict[str, Dict] = {}
_cache: Dict[str, Tuple[float, object]] = {}


def record(source: str, ok: bool, ms: float = 0.0, err: str = "") -> None:
    h = _health.setdefault(source, {"ok": 0, "fail": 0, "recent": [], "last_ok": None,
                                    "last_err": None, "ms": 0.0})
    h["ok" if ok else "fail"] += 1
    h["recent"] = (h["recent"] + [1 if ok else 0])[-20:]
    if ok:
        h["last_ok"] = time.time()
        h["ms"] = round(0.7 * h["ms"] + 0.3 * ms, 1) if h["ms"] else round(ms, 1)
    else:
        h["last_err"] = err[:160]


_persist = {"loaded": False, "saved_at": 0.0}


async def ensure_loaded() -> None:
    """Restore health scores saved before the last restart (free Render sleeps)."""
    if _persist["loaded"]:
        return
    _persist["loaded"] = True
    from services import data_store
    macro, _ = await data_store.get("macro")
    if isinstance(macro, dict) and not _macro:
        _macro.update(macro)
    saved, _ = await data_store.get("feed_health")
    for src, h in (saved or {}).items():
        if src not in _health and isinstance(h, dict):
            _health[src] = {**{"ok": 0, "fail": 0, "recent": [], "last_ok": None, "last_err": None,
                               "ms": 0.0}, **h}


async def persist(force: bool = False) -> None:
    if not force and time.time() - _persist["saved_at"] < 60:
        return
    _persist["saved_at"] = time.time()
    from services import data_store
    await data_store.put("feed_health", _health)


def score(source: str) -> float:
    """Recent success rate (Laplace), 0..1 — untried sources start at 0.5."""
    r = _health.get(source, {}).get("recent") or []
    return (sum(r) + 1) / (len(r) + 2)


def ranked(sources: Sequence[str]) -> List[str]:
    return sorted(sources, key=lambda s: -score(s))


def status() -> Dict:
    keys = {"tiingo": bool(TIINGO_KEY), "alphavantage": bool(AV_KEY), "marketaux": bool(MARKETAUX_KEY),
            "coingecko": bool(COINGECKO_KEY), "bls": bool(BLS_KEY)}
    return {"keys": keys, "sources": {s: {**{k: v for k, v in h.items() if k != "recent"},
                                          "score": round(score(s), 2)} for s, h in _health.items()}}


def _cached(key: str, ttl: float):
    hit = _cache.get(key)
    return hit[1] if hit and time.time() - hit[0] < ttl else None


async def _get(source: str, url: str, params: Optional[Dict] = None, headers: Optional[Dict] = None,
               as_json: bool = True, method: str = "GET", body: Optional[Dict] = None, timeout: float = 20):
    t0 = time.time()
    try:
        async with httpx.AsyncClient(timeout=timeout, headers={**UA, **(headers or {})},
                                     follow_redirects=True) as c:
            r = await (c.post(url, json=body) if method == "POST" else c.get(url, params=params))
        if r.status_code != 200:
            record(source, False, err=f"HTTP {r.status_code}: {r.text[:120].strip()}")
            return None
        data = r.json() if as_json else r.text
        record(source, True, (time.time() - t0) * 1000)
        return data
    except Exception as e:
        record(source, False, err=f"{type(e).__name__}: {e}"[:160])
        return None


# ------------------------------------------------------------------ symbols
def _s(symbol: str) -> str:
    return symbol.upper().replace("/", "").replace("-", "")


STOOQ_MAP = {"XAUUSD": "xauusd", "XAGUSD": "xagusd", "US30": "^dji", "NAS100": "^ndx", "SPX500": "^spx",
             "SPX": "^spx", "DXY": "dx.f", "USOIL": "cl.f", "WTI": "cl.f", "BTCUSD": "btcusd", "ETHUSD": "ethusd"}
CRYPTO_BASES = ("BTC", "ETH", "DOGE", "SOL", "XRP", "ADA", "LTC", "BNB")


def stooq_symbol(symbol: str) -> Optional[str]:
    s = _s(symbol)
    if s in STOOQ_MAP:
        return STOOQ_MAP[s]
    if len(s) == 6 and s.isalpha():
        return s.lower()                                    # eurusd, usdjpy …
    if s.isalpha() and len(s) <= 5:
        return f"{s.lower()}.us"                            # nvda.us
    return None


def is_crypto(symbol: str) -> bool:
    return _s(symbol).startswith(CRYPTO_BASES)


def binance_symbol(symbol: str) -> Optional[str]:
    s = _s(symbol)
    base = next((b for b in CRYPTO_BASES if s.startswith(b)), None)
    return f"{base}USDT" if base else None


# ------------------------------------------------------------------ parsers (pure)
def parse_ohlc_csv(text: str) -> List[Bar]:
    """Stooq / CBOE style CSV: Date,Open,High,Low,Close[,Volume] (any header case)."""
    out = []
    rows = list(csv.reader(io.StringIO(text or "")))
    if not rows:
        return out
    head = [h.strip().lower() for h in rows[0]]
    try:
        i = {k: head.index(k) for k in ("date", "open", "high", "low", "close")}
    except ValueError:
        return out
    for r in rows[1:]:
        try:
            raw = r[i["date"]].strip()
            d = (dt.datetime.strptime(raw, "%m/%d/%Y").date() if "/" in raw else dt.date.fromisoformat(raw[:10]))
            out.append((d, float(r[i["open"]]), float(r[i["high"]]), float(r[i["low"]]), float(r[i["close"]])))
        except (ValueError, IndexError):
            continue
    return sorted(out)


def parse_tiingo(data) -> List[Bar]:
    """Tiingo FX / stock rows, or crypto [{'priceData': [...]}]."""
    rows = data
    if isinstance(data, list) and data and isinstance(data[0], dict) and "priceData" in data[0]:
        rows = data[0]["priceData"]
    out = []
    for r in rows or []:
        try:
            out.append((dt.date.fromisoformat(str(r["date"])[:10]), float(r["open"]), float(r["high"]),
                        float(r["low"]), float(r["close"])))
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(out)


def parse_binance(rows) -> List[Bar]:
    out = []
    for k in rows or []:
        try:
            d = dt.datetime.fromtimestamp(int(k[0]) / 1000, dt.timezone.utc).date()
            out.append((d, float(k[1]), float(k[2]), float(k[3]), float(k[4])))
        except (TypeError, ValueError, IndexError):
            continue
    return sorted(out)


AV_TICKERS = {"EURUSD": "FOREX:EUR", "GBPUSD": "FOREX:GBP", "USDJPY": "FOREX:JPY", "AUDUSD": "FOREX:AUD",
              "USDCAD": "FOREX:CAD", "USDCHF": "FOREX:CHF", "BTCUSD": "CRYPTO:BTC", "ETHUSD": "CRYPTO:ETH",
              "DOGEUSD": "CRYPTO:DOGE"}


def parse_av_sentiment(data: Dict, ticker: str) -> Optional[Dict]:
    """Relevance-weighted Alpha Vantage ticker sentiment (−1..+1)."""
    num = den = 0.0
    n = 0
    for item in (data or {}).get("feed") or []:
        for ts in item.get("ticker_sentiment") or []:
            if ts.get("ticker") != ticker:
                continue
            try:
                w = float(ts.get("relevance_score") or 0)
                num += w * float(ts["ticker_sentiment_score"])
                den += w
                n += 1
            except (KeyError, TypeError, ValueError):
                continue
    return {"score": round(num / den, 3), "articles": n} if den > 0 else None


def parse_marketaux(data: Dict, symbol: str) -> Optional[Dict]:
    vals = [float(e["sentiment_score"]) for a in (data or {}).get("data") or []
            for e in a.get("entities") or []
            if (e.get("symbol") or "").upper() == symbol.upper() and e.get("sentiment_score") is not None]
    return {"score": round(sum(vals) / len(vals), 3), "articles": len(vals)} if vals else None


def parse_ecb_csv(text: str) -> Optional[Dict]:
    rows = list(csv.DictReader(io.StringIO(text or "")))
    rows = [r for r in rows if r.get("OBS_VALUE") not in (None, "")]
    if not rows:
        return None
    last = rows[-1]
    return {"date": last.get("TIME_PERIOD"), "value": float(last["OBS_VALUE"])}


def parse_treasury_csv(text: str) -> Optional[Dict]:
    rows = list(csv.DictReader(io.StringIO(text or "")))
    if not rows:
        return None
    latest = rows[0]                                        # newest first
    pick = {}
    for col, name in (("2 Yr", "us2y"), ("10 Yr", "us10y"), ("30 Yr", "us30y"), ("3 Mo", "us3m")):
        try:
            pick[name] = float(latest[col])
        except (KeyError, TypeError, ValueError):
            continue
    if "us2y" in pick and "us10y" in pick:
        pick["curve_2s10s"] = round(pick["us10y"] - pick["us2y"], 3)
    return {"date": latest.get("Date"), **pick} if pick else None


BLS_SERIES = {"CUUR0000SA0": "cpi_index", "LNS14000000": "unemployment_rate",
              "CES0500000003": "avg_hourly_earnings"}


def parse_bls(data: Dict) -> Dict:
    out = {}
    for s in ((data or {}).get("Results") or {}).get("series") or []:
        name = BLS_SERIES.get(s.get("seriesID"))
        pts = [p for p in s.get("data") or [] if p.get("value") not in (None, "-")]
        if not name or not pts:
            continue
        latest = pts[0]
        rec = {"value": float(latest["value"]), "period": f"{latest['year']}-{latest['period']}"}
        prior = next((p for p in pts if p["year"] == str(int(latest["year"]) - 1)
                      and p["period"] == latest["period"]), None)
        if prior:
            rec["yoy_pct"] = round(100 * (float(latest["value"]) / float(prior["value"]) - 1), 2)
        out[name] = rec
    return out


def parse_coingecko_global(data: Dict) -> Optional[Dict]:
    d = (data or {}).get("data") or {}
    try:
        return {"btc_dominance": round(d["market_cap_percentage"]["btc"], 2),
                "eth_dominance": round(d["market_cap_percentage"].get("eth", 0), 2),
                "total_cap_usd": d["total_market_cap"]["usd"],
                "cap_change_24h_pct": round(d.get("market_cap_change_percentage_24h_usd", 0), 2)}
    except (KeyError, TypeError):
        return None


# ------------------------------------------------------------------ fetchers
async def stooq_daily(symbol: str) -> List[Bar]:
    code = stooq_symbol(symbol)
    if not code:
        return []
    hit = _cached(f"stooq:{code}", 12 * 3600)
    if hit is not None:
        return hit
    text = await _get("stooq", "https://stooq.com/q/d/l/", {"s": code, "i": "d"}, as_json=False)
    bars = parse_ohlc_csv(text) if text else []
    if bars:
        _cache[f"stooq:{code}"] = (time.time(), bars)
    elif text is not None:
        record("stooq", False, err="no rows (symbol or limit)")
    return bars


async def tiingo_daily(symbol: str, years: int = 30) -> List[Bar]:
    if not TIINGO_KEY:
        return []
    s = _s(symbol)
    start = (dt.date.today() - dt.timedelta(days=365 * years)).isoformat()
    key = f"tiingo:{s}:{years}"
    hit = _cached(key, 12 * 3600)
    if hit is not None:
        return hit
    p = {"startDate": start, "resampleFreq": "1day", "token": TIINGO_KEY}
    if is_crypto(symbol):
        data = await _get("tiingo", "https://api.tiingo.com/tiingo/crypto/prices",
                          {**p, "tickers": s.lower()})
    elif s.startswith(("XAU", "XAG")):
        return []                                       # Tiingo FX carries no metals (HTTP 400)
    elif len(s) == 6 and s.isalpha():
        data = await _get("tiingo", f"https://api.tiingo.com/tiingo/fx/{s.lower()}/prices", p)
    elif s.isalpha() and len(s) <= 5:
        data = await _get("tiingo", f"https://api.tiingo.com/tiingo/daily/{s.lower()}/prices",
                          {"startDate": start, "token": TIINGO_KEY})
    else:
        return []
    bars = parse_tiingo(data) if data else []
    if bars:
        _cache[key] = (time.time(), bars)
    return bars


async def binance_daily(symbol: str, limit: int = 1000) -> List[Bar]:
    pair = binance_symbol(symbol)
    if not pair:
        return []
    hit = _cached(f"binance:{pair}", 6 * 3600)
    if hit is not None:
        return hit
    data = await _get("binance", "https://data-api.binance.vision/api/v3/klines",
                      {"symbol": pair, "interval": "1d", "limit": limit})
    bars = parse_binance(data) if data else []
    if bars:
        _cache[f"binance:{pair}"] = (time.time(), bars)
    return bars


async def twelvedata_daily(symbol: str) -> List[Bar]:
    """5,000 daily bars in ONE credit (~19 years) — the existing key, through the limiter."""
    hit = _cached(f"td:{_s(symbol)}", 12 * 3600)
    if hit is not None:
        return hit
    t0 = time.time()
    try:
        from services.ohlc_service import fetch_ohlc
        bars = await fetch_ohlc(symbol, "1day", 5000, broker=False)
    except Exception as e:
        record("twelvedata", False, err=f"{type(e).__name__}: {e}"[:160])
        return []
    record("twelvedata", bool(bars), (time.time() - t0) * 1000, "" if bars else "no rows")
    if bars:
        _cache[f"td:{_s(symbol)}"] = (time.time(), bars)
    return bars


async def yahoo_daily(symbol: str) -> List[Bar]:
    """Yahoo chart range=max — decades for FX (=X), gold (GC=F), indices."""
    hit = _cached(f"yahoo:{_s(symbol)}", 12 * 3600)
    if hit is not None:
        return hit
    t0 = time.time()
    try:
        from services.ohlc_service import _yahoo_ohlc
        bars = await _yahoo_ohlc(symbol, "1day", 9000)
    except Exception as e:
        record("yahoo", False, err=f"{type(e).__name__}: {e}"[:160])
        return []
    record("yahoo", bool(bars), (time.time() - t0) * 1000, "" if bars else "no rows")
    if bars:
        _cache[f"yahoo:{_s(symbol)}"] = (time.time(), bars)
    return bars


# ---- Dukascopy (Swiss bank) — LZMA-compressed binary daily candles, FX + metals ~2003→
DUKA_START = 2003


def duka_point(symbol: str) -> float:
    s = _s(symbol)
    return 1e-3 if s.endswith("JPY") or s.startswith(("XAU", "XAG")) else 1e-5


def parse_duka_candles(raw: bytes, year: int, point: float) -> List[Bar]:
    """One yearly BID_candles_day_1.bi5: LZMA, then 24-byte big-endian records
    (seconds-from-Jan-1 uint32, open, close, low, high as uint32 × point, volume float)."""
    import lzma
    import struct
    if not raw:
        return []
    try:
        data = lzma.decompress(raw)
    except lzma.LZMAError:
        return []
    start = dt.datetime(year, 1, 1, tzinfo=dt.timezone.utc)
    out = []
    for off in range(0, len(data) - 23, 24):
        t, o, c, lo, hi, vol = struct.unpack(">IIIIIf", data[off:off + 24])
        if not (o and c and lo and hi) or vol <= 0:           # weekends / holidays are empty
            continue
        d = (start + dt.timedelta(seconds=t)).date()
        out.append((d, round(o * point, 6), round(hi * point, 6), round(lo * point, 6), round(c * point, 6)))
    return out


async def dukascopy_daily(symbol: str) -> List[Bar]:
    s = _s(symbol)
    if not (len(s) == 6 and s.isalpha()):                     # FX + XAUUSD / XAGUSD
        return []
    hit = _cached(f"duka:{s}", 7 * 86400)
    if hit is not None:
        return hit
    bars: List[Bar] = []
    point = duka_point(s)
    t0 = time.time()
    try:
        async with httpx.AsyncClient(timeout=20, headers=UA) as c:
            for y in range(DUKA_START, dt.date.today().year + 1):
                r = await c.get(f"https://datafeed.dukascopy.com/datafeed/{s}/{y}/BID_candles_day_1.bi5")
                if r.status_code == 200:
                    bars += parse_duka_candles(r.content, y, point)
                elif r.status_code != 404:
                    record("dukascopy", False, err=f"HTTP {r.status_code} ({y})")
                    break
    except Exception as e:
        record("dukascopy", False, err=f"{type(e).__name__}: {e}"[:160])
        return []
    record("dukascopy", bool(bars), (time.time() - t0) * 1000, "" if bars else "no rows")
    bars = sorted(set(bars))
    if bars:
        _cache[f"duka:{s}"] = (time.time(), bars)
    return bars


DAILY_SOURCES = {"stooq": stooq_daily, "tiingo": tiingo_daily, "binance": binance_daily,
                 "twelvedata": twelvedata_daily, "yahoo": yahoo_daily, "dukascopy": dukascopy_daily}


async def daily_chain(symbol: str, min_bars: int = 200) -> Tuple[List[Bar], Optional[str]]:
    """Long daily history from the healthiest free source that has the symbol."""
    await ensure_loaded()
    names = [n for n in ranked(list(DAILY_SOURCES)) if n != "binance" or is_crypto(symbol)]
    best: Tuple[List[Bar], Optional[str]] = ([], None)
    try:
        for name in names:
            bars = await DAILY_SOURCES[name](symbol)
            if len(bars) >= min_bars:
                return bars, name
            if len(bars) > len(best[0]):
                best = (bars, name)        # keep the longest partial history as a fallback
        return best
    finally:
        await persist()


async def cross_check(symbol: str, reference: Optional[float] = None, tol_pct: float = 0.5) -> Dict:
    """Latest close from every daily feed; flags any that sits > tol_pct from the median."""
    await ensure_loaded()
    closes = {}
    for name, fn in DAILY_SOURCES.items():
        if name == "binance" and not is_crypto(symbol):
            continue
        bars = await fn(symbol)
        if bars:
            closes[name] = {"date": bars[-1][0].isoformat(), "close": bars[-1][4]}
    if reference is not None:
        closes["live"] = {"date": dt.date.today().isoformat(), "close": reference}
    vals = sorted(v["close"] for v in closes.values())
    if not vals:
        return {"symbol": symbol, "feeds": {}, "outliers": []}
    med = vals[len(vals) // 2]
    outliers = [k for k, v in closes.items() if med and abs(v["close"] / med - 1) * 100 > tol_pct]
    for k in outliers:
        record(k, False, err=f"cross-check outlier on {symbol}")
    await persist(force=True)
    return {"symbol": symbol, "median": med, "feeds": closes, "outliers": outliers}


_sent: Dict[str, Dict] = {}


async def refresh_sentiment(symbols: Sequence[str]) -> Dict:
    """Alpha Vantage (FX/crypto/stocks) + MarketAux (stocks). Few calls → run 6-hourly."""
    await ensure_loaded()
    out = {}
    av_ticks = {_s(s): AV_TICKERS.get(_s(s)) or (_s(s) if _s(s).isalpha() and len(_s(s)) <= 5 else None)
                for s in symbols}
    av_ticks = {k: v for k, v in av_ticks.items() if v}
    if AV_KEY and av_ticks:
        data = await _get("alphavantage", "https://www.alphavantage.co/query",
                          {"function": "NEWS_SENTIMENT", "tickers": ",".join(sorted(set(av_ticks.values()))),
                           "limit": 200, "apikey": AV_KEY})
        if isinstance(data, dict) and ("Note" in data or "Information" in data):
            record("alphavantage", False, err=str(data.get("Information") or data.get("Note"))[:160])
            data = None
        for sym, tick in av_ticks.items():
            r = parse_av_sentiment(data, tick) if data else None
            if r:
                out.setdefault(sym, {})["alphavantage"] = r
    stocks = [k for k in av_ticks if k.isalpha() and len(k) <= 5]
    if MARKETAUX_KEY and stocks:
        data = await _get("marketaux", "https://api.marketaux.com/v1/news/all",
                          {"symbols": ",".join(stocks), "filter_entities": "true", "language": "en",
                           "api_token": MARKETAUX_KEY})
        for sym in stocks:
            r = parse_marketaux(data, sym) if data else None
            if r:
                out.setdefault(sym, {})["marketaux"] = r
    for sym, srcs in out.items():
        sc = [v["score"] for v in srcs.values()]
        _sent[sym] = {"score": round(sum(sc) / len(sc), 3), "sources": srcs, "_t": time.time()}
    await persist(force=True)
    return {s: {k: v for k, v in d.items() if k != "_t"} for s, d in _sent.items()}


def news_sentiment(symbol: str, max_age_h: float = 12) -> Optional[Dict]:
    d = _sent.get(_s(symbol))
    return d if d and time.time() - d["_t"] < max_age_h * 3600 else None


_macro: Dict = {}


async def refresh_macro() -> Dict:
    await ensure_loaded()
    year = dt.date.today().year
    out: Dict = {}
    t = await _get("ecb", "https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A",
                   {"lastNObservations": 5, "format": "csvdata"}, as_json=False)
    if t and (r := parse_ecb_csv(t)):
        out["ecb_eurusd"] = r
    t = await _get("ecb", "https://data-api.ecb.europa.eu/service/data/FM/B.U2.EUR.4F.KR.DFR.LEV",
                   {"lastNObservations": 1, "format": "csvdata"}, as_json=False)
    if t and (r := parse_ecb_csv(t)):
        out["ecb_deposit_rate"] = r
    t = await _get("treasury", f"https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
                   f"daily-treasury-rates.csv/{year}/all",
                   {"type": "daily_treasury_yield_curve", "field_tdr_date_value": year}, as_json=False)
    if t and (r := parse_treasury_csv(t)):
        out["us_yields"] = r
    d = await _get("treasury", "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/"
                   "v2/accounting/od/debt_to_penny", {"sort": "-record_date", "page[size]": 1})
    try:
        out["us_debt"] = {"date": d["data"][0]["record_date"],
                          "total_usd": float(d["data"][0]["tot_pub_debt_out_amt"])}
    except (TypeError, KeyError, IndexError, ValueError):
        pass
    body = {"seriesid": list(BLS_SERIES), **({"registrationkey": BLS_KEY} if BLS_KEY else {})}
    d = await _get("bls", f"https://api.bls.gov/publicAPI/{'v2' if BLS_KEY else 'v1'}/timeseries/data/",
                   method="POST", body=body)
    if d and (r := parse_bls(d)):
        out["us_labour_prices"] = r
    t = await _get("cboe", "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv",
                   as_json=False)
    vix = parse_ohlc_csv(t) if t else []
    if vix:
        out["vix"] = {"date": vix[-1][0].isoformat(), "close": vix[-1][4],
                      "regime": "stress" if vix[-1][4] >= 25 else "calm" if vix[-1][4] < 16 else "normal"}
    g = await _get("coingecko", "https://api.coingecko.com/api/v3/global",
                   headers={"x-cg-demo-api-key": COINGECKO_KEY} if COINGECKO_KEY else None)
    if g and (r := parse_coingecko_global(g)):
        out["crypto_global"] = r
    if out:
        _macro.update(out, _t=time.time())
        from services import data_store
        await data_store.put("macro", {k: v for k, v in _macro.items() if k != "_t"})
    await persist(force=True)
    return {k: v for k, v in _macro.items() if k != "_t"}


def macro_cached() -> Dict:
    return {k: v for k, v in _macro.items() if k != "_t"}
