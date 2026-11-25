"""Price POOL — intraday/daily OHLC from many free sources, so no single limit stalls us.

Order for every bar request (services.ohlc_service.time_series):
  1. EA broker feed (free, real-time, exact broker prices)          — when the EA runs
  2. TwelveData, but only if a slot is free within POOL_TD_WAIT_S (3 s)  — cache hits instant
  3. the FREE pool, best-health first (data_feeds registry scores):
       crypto     Binance klines (no key, every interval)
       FX         Yahoo chart, Tiingo FX intraday (TIINGO_API_KEY)
       indices    Yahoo (^NDX, ^DJI, ^GSPC, ^RUT) · stocks Yahoo
       gold       Binance PAXG (tokenised spot oz) / Yahoo GC=F futures — PROXIES:
                  basis-corrected to the last real spot price (broker/TwelveData,
                  ≤ 6 h old); never served uncorrected
  4. TwelveData with the full queue wait (last resort)
Responses are cached by interval (1m 50 s … 1d 12 h) and returned TwelveData-shaped,
so every engine keeps working unchanged. New sources = one fetcher in SOURCES.
"""

from __future__ import annotations

import datetime as dt
import time
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from decouple import config

POOL_TD_WAIT_S = float(config("POOL_TD_WAIT_S", default="3"))
POOL_ENABLED = str(config("PRICE_POOL_ENABLED", default="true")).lower() in ("1", "true", "yes", "on")
TTL = {"1min": 50, "5min": 120, "15min": 240, "30min": 300, "1h": 600, "2h": 1200, "4h": 1800,
       "1day": 43200, "1week": 43200}
MIN_PER = {"1min": 1, "5min": 5, "15min": 15, "30min": 30, "1h": 60, "2h": 120, "4h": 240,
           "1day": 1440, "1week": 10080}
CRYPTO = ("BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "ADA", "LTC")
YAHOO_IDX = {"NAS100": "^NDX", "US100": "^NDX", "NDX": "^NDX", "US30": "^DJI", "SPX500": "^GSPC",
             "US500": "^GSPC", "SPX": "^GSPC", "US2000": "^RUT", "DXY": "DX-Y.NYB", "XAGUSD": "SI=F",
             "USOIL": "CL=F", "WTI": "CL=F", "UKOIL": "BZ=F"}
PROXIES = {"XAUUSD": ("binance:PAXGUSDT", "kraken:PAXGUSD", "yahoo:GC=F")}
# Kraken spot FX (public, no key) — a second free FX feed independent of Yahoo
KRAKEN_FX = {"EURUSD", "GBPUSD", "USDJPY", "USDCAD", "USDCHF", "AUDUSD", "EURGBP"}
YAHOO_FX_ALIAS = {"USDCNH": "CNH=X", "USDZAR": "ZAR=X"}

_cache: Dict[str, Tuple[float, List[Dict]]] = {}
_ref: Dict[str, Tuple[float, float]] = {}          # symbol → (unix time, real spot close)


def _s(symbol: str) -> str:
    return symbol.upper().replace("/", "").replace("-", "")


def kind(symbol: str) -> str:
    s = _s(symbol)
    if s.startswith(CRYPTO):
        return "crypto"
    if s in PROXIES:
        return "proxy"
    if s in YAHOO_IDX:
        return "index"
    if len(s) == 6 and s.isalpha():
        return "fx"
    return "stock"


def note_reference(symbol: str, close: float) -> None:
    """Real spot from broker / TwelveData — anchors the proxy basis correction."""
    if close:
        _ref[_s(symbol)] = (time.time(), float(close))


# ── parsers (pure) ──────────────────────────────────────────────────────────────
def parse_yahoo(data: Dict) -> List[Dict]:
    try:
        res = data["chart"]["result"][0]
        q = res["indicators"]["quote"][0]
        out = []
        for i, t in enumerate(res["timestamp"]):
            o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
            if None in (o, h, l, c):
                continue
            out.append({"t": int(t), "open": float(o), "high": float(h), "low": float(l), "close": float(c)})
        return out
    except (KeyError, IndexError, TypeError):
        return []


def parse_binance(rows) -> List[Dict]:
    out = []
    for r in rows or []:
        try:
            out.append({"t": int(r[0]) // 1000, "open": float(r[1]), "high": float(r[2]),
                        "low": float(r[3]), "close": float(r[4])})
        except (IndexError, TypeError, ValueError):
            continue
    return out


def parse_tiingo_fx(rows) -> List[Dict]:
    out = []
    for r in rows or []:
        try:
            t = dt.datetime.fromisoformat(str(r["date"]).replace("Z", "+00:00"))
            out.append({"t": int(t.timestamp()), "open": float(r["open"]), "high": float(r["high"]),
                        "low": float(r["low"]), "close": float(r["close"])})
        except (KeyError, TypeError, ValueError):
            continue
    return out


def aggregate(bars: List[Dict], minutes: int, tz: str = "UTC") -> List[Dict]:
    """Roll 1h bars up to 4h (or any multiple) on clock boundaries in ``tz``."""
    z = ZoneInfo(tz)
    out: List[Dict] = []
    for b in sorted(bars, key=lambda x: x["t"]):
        lt = dt.datetime.fromtimestamp(b["t"], z)
        start = lt.replace(minute=0, second=0, microsecond=0, hour=(lt.hour // (minutes // 60)) * (minutes // 60))
        key = int(start.timestamp())
        if out and out[-1]["t"] == key:
            o = out[-1]
            o["high"], o["low"], o["close"] = max(o["high"], b["high"]), min(o["low"], b["low"]), b["close"]
        else:
            out.append({**b, "t": key})
    return out


def basis_correct(bars: List[Dict], ref: Optional[Tuple[float, float]], max_age_s: float = 6 * 3600,
                  now: Optional[float] = None) -> Optional[List[Dict]]:
    """Scale proxy bars so their price matches the last real spot. None if no fresh ref."""
    now = now or time.time()
    if not bars or not ref or now - ref[0] > max_age_s:
        return None
    # proxy close nearest the reference time
    near = min(bars, key=lambda b: abs(b["t"] - ref[0]))
    if not near["close"]:
        return None
    f = ref[1] / near["close"]
    return [{**b, "open": b["open"] * f, "high": b["high"] * f, "low": b["low"] * f, "close": b["close"] * f}
            for b in bars]


def to_td(bars: List[Dict], interval: str, tz: str, n: int, source: str) -> Dict:
    z = ZoneInfo(tz or "UTC")
    daily = interval in ("1day", "1week")
    vals = [{"datetime": dt.datetime.fromtimestamp(b["t"], z).strftime("%Y-%m-%d" if daily else "%Y-%m-%d %H:%M:%S"),
             "open": b["open"], "high": b["high"], "low": b["low"], "close": b["close"]}
            for b in sorted(bars, key=lambda x: x["t"])[-n:]]
    return {"values": vals[::-1], "status": "ok", "source": source}


# ── fetchers ────────────────────────────────────────────────────────────────────
async def _yahoo(ticker: str, interval: str, n: int) -> List[Dict]:
    from services.data_feeds import _get
    if interval in ("4h", "2h"):
        yi = "1h"
    else:
        yi = {"1min": "1m", "5min": "5m", "15min": "15m", "30min": "30m", "1h": "1h",
              "1day": "1d", "1week": "1wk"}.get(interval)
    if not yi:
        return []
    days = max(2, int(n * MIN_PER[interval] / 1440 * 1.6) + 2)
    cap = {"1m": 7, "5m": 59, "15m": 59, "30m": 59, "1h": 729}.get(yi)
    rng = f"{min(days, cap)}d" if cap else ("max" if days > 3650 else f"{days}d")
    data = await _get("yahoo_intraday", f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
                      {"range": rng, "interval": yi})
    return parse_yahoo(data) if data else []


async def _binance(pair: str, interval: str, n: int) -> List[Dict]:
    from services.data_feeds import _get
    bi = {"1min": "1m", "5min": "5m", "15min": "15m", "30min": "30m", "1h": "1h", "2h": "2h", "4h": "4h",
          "1day": "1d", "1week": "1w"}.get(interval)
    if not bi:
        return []
    data = await _get("binance", "https://data-api.binance.vision/api/v3/klines",
                      {"symbol": pair, "interval": bi, "limit": min(1000, max(2, n))})
    return parse_binance(data) if data else []


async def _tiingo_fx(symbol: str, interval: str, n: int) -> List[Dict]:
    from services.data_feeds import TIINGO_KEY, _get
    rf = {"1min": "1min", "5min": "5min", "15min": "15min", "30min": "30min", "1h": "1hour", "4h": "4hour",
          "1day": "1day"}.get(interval)
    if not TIINGO_KEY or not rf:
        return []
    start = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=n * MIN_PER[interval] * 1.6 + 2880)).date()
    data = await _get("tiingo_fx", f"https://api.tiingo.com/tiingo/fx/{_s(symbol).lower()}/prices",
                      {"resampleFreq": rf, "startDate": start.isoformat(), "token": TIINGO_KEY})
    return parse_tiingo_fx(data) if isinstance(data, list) else []


def parse_kraken(data) -> List[Dict]:
    """Kraken /0/public/OHLC → bars (rows: time, open, high, low, close, vwap, volume, count)."""
    res = (data or {}).get("result") or {}
    rows = next((v for k, v in res.items() if k != "last" and isinstance(v, list)), [])
    out = []
    for r in rows:
        try:
            out.append({"t": int(r[0]), "open": float(r[1]), "high": float(r[2]),
                        "low": float(r[3]), "close": float(r[4])})
        except (TypeError, ValueError, IndexError):
            continue
    return out


async def _kraken(pair: str, interval: str, n: int) -> List[Dict]:
    from services.data_feeds import _get
    ki = {"1min": 1, "5min": 5, "15min": 15, "30min": 30, "1h": 60, "4h": 240, "1day": 1440,
          "1week": 10080}.get(interval)
    if not ki:
        return []
    data = await _get("kraken", "https://api.kraken.com/0/public/OHLC", {"pair": pair, "interval": ki})
    if not data or data.get("error"):
        return []
    return parse_kraken(data)[-max(2, n):]


def plan(symbol: str) -> List[Tuple[str, str]]:
    """(source, ticker) candidates for a symbol, before health ranking."""
    s, k = _s(symbol), kind(symbol)
    if k == "crypto":
        base = next(b for b in CRYPTO if s.startswith(b))
        kr = {"BTC": "XBTUSD", "ETH": "ETHUSD", "SOL": "SOLUSD", "XRP": "XRPUSD"}.get(base)
        return [("binance", f"{base}USDT"), ("yahoo_intraday", f"{base}-USD")] + ([("kraken", kr)] if kr else [])
    if k == "proxy":
        return [tuple(x.split(":")) for x in PROXIES[s]]
    if k == "index":
        return [("yahoo_intraday", YAHOO_IDX[s])]
    if k == "fx":
        return [("yahoo_intraday", YAHOO_FX_ALIAS.get(s, f"{s}=X"))] + \
            ([("kraken", s)] if s in KRAKEN_FX else []) + [("tiingo_fx", s)]
    return [("yahoo_intraday", s)]


async def alt_values(symbol: str, interval: str, outputsize: int, timezone: str = "UTC") -> Optional[Dict]:
    """Bars from the free pool (TwelveData-shaped) or None."""
    if not POOL_ENABLED or interval not in MIN_PER:
        return None
    from services.data_feeds import ranked
    tz = timezone or "UTC"
    cands = plan(symbol)
    order = {src: i for i, src in enumerate(ranked([c[0] for c in cands]))}
    cands.sort(key=lambda c: order.get(c[0], 99))
    for src, tick in cands:
        key = f"{src}:{tick}:{interval}"
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < TTL.get(interval, 300):
            bars = hit[1]
        else:
            try:
                if src == "binance":
                    bars = await _binance(tick, interval, outputsize)
                elif src == "tiingo_fx":
                    bars = await _tiingo_fx(tick, interval, outputsize)
                elif src == "kraken":
                    bars = await _kraken(tick, interval, outputsize)
                    if interval == "2h":
                        bars = aggregate(bars, 120, tz)
                else:
                    bars = await _yahoo(tick, interval, outputsize * (4 if interval == "4h" else 2 if interval == "2h" else 1))
            except Exception:
                bars = []
            if interval in ("4h", "2h") and src == "yahoo_intraday":
                bars = aggregate(bars, MIN_PER[interval], tz)
            if bars:
                _cache[key] = (time.time(), bars)
        if not bars:
            continue
        if kind(symbol) == "proxy":
            bars = basis_correct(bars, _ref.get(_s(symbol)))
            if not bars:
                continue
        return to_td(bars, interval, tz, outputsize, f"pool:{src}")
    return None
