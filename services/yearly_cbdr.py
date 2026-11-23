"""Yearly CBDR — the Q1 (Jan → Mar) daily-candle range of each year, projected as
standard deviations, the owner's yearly framework:

  BOX       Q1 body range (lowest / highest daily open-or-close, Jan 1 → Mar 31).
            Levels are labelled like the TradingView fib tool: 0 = box low,
            1 = box high, every 0.5 box from −4 to 4; internal fibs 0.236 … 0.786.
  FORMING   the current year's box isn't usable until March closes — until then the
            previous years guide (in Q1 2027, use 2026, 2025 …).
  PEAK      how far each year's projection has travelled since its box closed
            (e.g. EUR/USD 2025: reached 2.5, failed to break 3).
  CONTROL   the most recent completed year whose projection still brackets price
            inside ±4 — plus every older year's position for context.
  CLUSTERS  levels from ≥2 different years within a tolerance (FX 30 pips,
            JPY 0.30, gold $15) — the high-probability limit zones.
  OTE       the dominant swing over the lookback (default ~2y): after a bull leg the
            buy OTE is the 0.618–0.786 retrace (0.705 sweet spot); after a bear leg,
            the sell OTE. A cluster inside the OTE = "golden" limit zone.
  PLAN      limit at the golden cluster, stop beyond the next yearly level (≥ the
            trade gate's minimum stop), targets = the next yearly levels in the trade
            direction (furthest = the anchor year's next untested deviation).

Bars are (date, open, high, low, close) oldest-first.
"""

from __future__ import annotations

import datetime as dt
import time
from typing import Dict, List, Optional, Sequence, Tuple

from decouple import config

DatedOHLC = Tuple[dt.date, float, float, float, float]
YEARS_BACK = int(config("YEARLY_CBDR_YEARS", default="7"))
OTE_LOOKBACK = int(config("YEARLY_OTE_LOOKBACK_BARS", default="520"))
DEVS = [x / 2 for x in range(-8, 9)]                   # −4 … +4 (TradingView fib-extension labels)
INTERNAL = [0.236, 0.382, 0.5, 0.618, 0.705, 0.786]


def _sym(symbol: str) -> str:
    return symbol.upper().replace("/", "")


def tolerance(symbol: str, price: Optional[float] = None) -> float:
    """Cluster width: FX 30 pips, JPY 0.30, gold $15; indices/crypto 0.3% of price."""
    s = _sym(symbol)
    if not (s.startswith(("XAU", "GOLD")) or s[:6].isalpha()):
        return 0.003 * price if price else 0.0
    if s.startswith(("XAU", "GOLD")):
        return 15.0
    if s[3:6] == "JPY":
        return 0.30
    if s[:6].isalpha():
        return 0.0030
    return 0.0


def _r(symbol: str, v: float) -> float:
    s = _sym(symbol)
    return round(v, 2 if s.startswith(("XAU", "GOLD")) or not s[:6].isalpha()
                 else 3 if s[3:6] == "JPY" else 5)


def label(k: float) -> str:
    """The owner's chart labels: 0 = box low, 1 = box high, 2.5 = low + 2.5 boxes, −1 = low − 1 box."""
    return f"{k:g}"


BOXES_CSV = str(config("YEARLY_BOXES_CSV", default="data/yearly_boxes.csv"))
_manual: Dict = {"mtime": None, "rows": {}}


def manual_boxes() -> Dict[Tuple[str, int], Dict]:
    """Owner-entered Q1 body ranges (TradingView, consistent feed) — they override the
    computed box. CSV: symbol,year,low,high,source; blank rows are ignored."""
    import csv
    import os
    try:
        m = os.path.getmtime(BOXES_CSV)
    except OSError:
        return {}
    if _manual["mtime"] != m:
        rows = {}
        with open(BOXES_CSV, newline="") as f:
            for r in csv.DictReader(f):
                try:
                    lo, hi = float(r["low"]), float(r["high"])
                except (TypeError, ValueError, KeyError):
                    continue
                if hi > lo:
                    rows[(_sym(r["symbol"]), int(r["year"]))] = {
                        "year": int(r["year"]), "low": lo, "high": hi, "range": hi - lo,
                        "source": (r.get("source") or "manual").strip() or "manual"}
        _manual.update(mtime=m, rows=rows)
    return _manual["rows"]


STATS_CSV = str(config("YEARLY_STATS_CSV", default="data/yearly_stats.csv"))
_stats: Dict = {"mtime": None, "rows": {}}


def history_stats(symbol: str) -> Optional[Dict]:
    """Base rates from the owner's long history (wildchance.history_import): how far a
    year's projection usually travels after its Q1 box closes."""
    import csv
    import os
    try:
        m = os.path.getmtime(STATS_CSV)
    except OSError:
        return None
    if _stats["mtime"] != m:
        rows: Dict[str, List[Dict]] = {}
        with open(STATS_CSV, newline="") as f:
            for r in csv.DictReader(f):
                try:
                    if r.get("complete") not in ("1", "True", "true"):
                        continue
                    rows.setdefault(_sym(r["symbol"]), []).append(
                        {"year": int(r["year"]), "up": float(r["peak_up"]), "down": float(r["peak_down"]),
                         "close": float(r["close_pos"])})
                except (TypeError, ValueError, KeyError):
                    continue
        _stats.update(mtime=m, rows=rows)
    ys = _stats["rows"].get(_sym(symbol))
    if not ys or len(ys) < 5:
        return None
    n = len(ys)

    def med(xs):
        xs = sorted(xs)
        return round(xs[len(xs) // 2], 2)

    reach_up = {f"{k:g}": round(100 * sum(y["up"] >= k for y in ys) / n) for k in (1.5, 2, 2.5, 3, 4)}
    reach_dn = {f"{k:g}": round(100 * sum(y["down"] <= k for y in ys) / n) for k in (-0.5, -1, -1.5, -2, -3)}
    return {"years": n, "span": f"{min(y['year'] for y in ys)}–{max(y['year'] for y in ys)}",
            "median_up": med([y["up"] for y in ys]), "median_down": med([y["down"] for y in ys]),
            "pct_reach_up": reach_up, "pct_reach_down": reach_dn,
            "pct_close_above_box": round(100 * sum(y["close"] > 1 for y in ys) / n),
            "pct_close_below_box": round(100 * sum(y["close"] < 0 for y in ys) / n)}


def box_for(symbol: str, daily: Sequence[DatedOHLC], year: int) -> Optional[Dict]:
    hit = manual_boxes().get((_sym(symbol), year))
    if hit:
        return dict(hit)
    b = q1_box(daily, year)
    if b:
        b["source"] = "computed"
    return b


def q1_box(daily: Sequence[DatedOHLC], year: int) -> Optional[Dict]:
    q1 = [b for b in daily if b[0].year == year and b[0].month <= 3]
    if len(q1) < 40:                                     # ~62 trading days in Q1
        return None
    lo = min(min(b[1], b[4]) for b in q1)
    hi = max(max(b[1], b[4]) for b in q1)
    if hi <= lo:
        return None
    return {"year": year, "low": lo, "high": hi, "range": hi - lo}


def level(box: Dict, k: float) -> float:
    return box["low"] + k * box["range"]


def position(box: Dict, price: float) -> float:
    return (price - box["low"]) / box["range"]


def peak(box: Dict, daily: Sequence[DatedOHLC]) -> Dict:
    """Furthest projection reached since the box closed (box units, half-steps)."""
    after = [b for b in daily if b[0] > dt.date(box["year"], 3, 31)]
    if not after:
        return {"up": None, "down": None}
    up = position(box, max(b[2] for b in after))
    dn = position(box, min(b[3] for b in after))
    import math
    return {"up": math.floor(up * 2) / 2, "down": math.ceil(dn * 2) / 2,
            "up_raw": round(up, 2), "down_raw": round(dn, 2)}


def mid_crosses(box: Dict, daily: Sequence[DatedOHLC]) -> int:
    """Weekly closes crossing the box mean (0.5) since the box closed — the owner's
    'price oscillates across the 2020 mean' read: the most-crossed mean is the magnet."""
    mid = level(box, 0.5)
    after = [b for b in daily if b[0] > dt.date(box["year"], 3, 31)]
    weekly, seen = [], set()
    for b in reversed(after):                                   # last close of each ISO week
        wk = b[0].isocalendar()[:2]
        if wk not in seen:
            seen.add(wk)
            weekly.append(b[4])
    sides = [c > mid for c in reversed(weekly)]
    return sum(1 for a, b in zip(sides, sides[1:]) if a != b)


def ote(daily: Sequence[DatedOHLC], lookback: int = OTE_LOOKBACK) -> Optional[Dict]:
    bars = list(daily)[-lookback:]
    if len(bars) < 20:
        return None
    hi_i = max(range(len(bars)), key=lambda i: bars[i][2])
    lo_i = min(range(len(bars)), key=lambda i: bars[i][3])
    hi, lo = bars[hi_i][2], bars[lo_i][3]
    rng = hi - lo
    if rng <= 0:
        return None
    if lo_i < hi_i:      # bull leg → buy the retrace
        return {"leg": "bull", "side": "BUY", "swing_low": lo, "swing_high": hi,
                "from": bars[lo_i][0].isoformat(), "to": bars[hi_i][0].isoformat(),
                "zone_top": hi - 0.618 * rng, "sweet": hi - 0.705 * rng, "zone_bottom": hi - 0.786 * rng}
    return {"leg": "bear", "side": "SELL", "swing_low": lo, "swing_high": hi,
            "from": bars[hi_i][0].isoformat(), "to": bars[lo_i][0].isoformat(),
            "zone_bottom": lo + 0.618 * rng, "sweet": lo + 0.705 * rng, "zone_top": lo + 0.786 * rng}


def all_levels(boxes: List[Dict]) -> List[Dict]:
    out = []
    for b in boxes:
        for k in DEVS:
            out.append({"year": b["year"], "k": k, "dev": label(k), "price": level(b, k)})
    return sorted(out, key=lambda x: x["price"])


def clusters(levels: List[Dict], tol: float) -> List[Dict]:
    """Groups of levels from ≥2 different years within ``tol`` of each other."""
    out, cur = [], []
    for lv in levels:
        if cur and lv["price"] - cur[0]["price"] > tol:
            if len({x["year"] for x in cur}) >= 2:
                out.append(cur)
            cur = []
        cur.append(lv)
    if cur and len({x["year"] for x in cur}) >= 2:
        out.append(cur)
    return [{"low": min(x["price"] for x in c), "high": max(x["price"] for x in c),
             "mid": sum(x["price"] for x in c) / len(c),
             "members": [f"{x['year']} {x['dev']}" for x in c],
             "years": sorted({x["year"] for x in c})} for c in out]


def analyse(symbol: str, daily: Sequence[DatedOHLC], today: Optional[dt.date] = None,
            years_back: int = YEARS_BACK) -> Dict:
    daily = list(daily)
    if not daily:
        return {"symbol": symbol, "ok": False, "reason": "no daily bars"}
    today = today or daily[-1][0]
    price = daily[-1][4]
    # the current year's box only counts once Q1 has closed
    last_complete = today.year if today > dt.date(today.year, 3, 31) else today.year - 1
    boxes = [b for b in (box_for(symbol, daily, y) for y in range(last_complete - years_back + 1, last_complete + 1))
             if b]
    if not boxes:
        return {"symbol": symbol, "ok": False, "reason": "not enough history for a Q1 box"}
    years = []
    for b in boxes:
        pos = position(b, price)
        years.append({"year": b["year"], "source": b.get("source"), "box_low": _r(symbol, b["low"]), "box_high": _r(symbol, b["high"]),
                      "position": round(pos, 2), "position_dev": label(round(pos * 2) / 2),
                      "peak": peak(b, daily), "mean": _r(symbol, level(b, 0.5)),
                      "mean_crosses": mid_crosses(b, daily),
                      "levels": {label(k): _r(symbol, level(b, k)) for k in DEVS},
                      "internal": {f"{f:g}": _r(symbol, level(b, f)) for f in INTERNAL}})
    magnet = max(years, key=lambda y: y["mean_crosses"]) if years else None
    control = next((y for y in reversed(years) if -4 <= y["position"] <= 4), None)
    levels = all_levels(boxes)
    cl = clusters(levels, tolerance(symbol, price))
    o = ote(daily)
    for c in cl:
        t = tolerance(symbol, price)        # overlap with the OTE (± tolerance) counts as golden
        c["in_ote"] = bool(o and c["low"] <= o["zone_top"] + t and c["high"] >= o["zone_bottom"] - t)
        c["distance"] = _r(symbol, c["mid"] - price)
        for k in ("low", "high", "mid"):
            c[k] = _r(symbol, c[k])
    res = {"symbol": symbol, "ok": True, "price": _r(symbol, price), "as_of": daily[-1][0].isoformat(),
           "forming": today.year if today <= dt.date(today.year, 3, 31) else None,
           "years": years, "control": control and control["year"],
           "magnet": magnet and magnet["mean_crosses"] >= 6 and
           {"year": magnet["year"], "mean": magnet["mean"], "crosses": magnet["mean_crosses"],
            "side": "above" if price > magnet["mean"] else "below"},
           "clusters": cl,
           "ote": o and {k: (_r(symbol, v) if isinstance(v, float) else v) for k, v in o.items()},
           "plan": plan(symbol, price, levels, cl, o),
           "history": history_stats(symbol)}
    return res


def plan(symbol: str, price: float, levels: List[Dict], cl: List[Dict], o: Optional[Dict]) -> Optional[Dict]:
    """Limit order at the nearest golden cluster (inside the OTE, on the right side of price)."""
    if not o:
        return None
    side = o["side"]
    cand = [c for c in cl if c["in_ote"] and (c["mid"] < price if side == "BUY" else c["mid"] > price)]
    if not cand:
        return {"side": side, "status": "no golden zone",
                "note": "no multi-year cluster inside the OTE on the right side of price"}
    c = min(cand, key=lambda c: abs(c["mid"] - price))
    from services.trade_gate import min_stop_distance, widen_stop
    tol = tolerance(symbol, price)
    entry = c["high"] if side == "BUY" else c["low"]
    if side == "BUY":
        below = [l["price"] for l in levels if l["price"] < c["low"] - tol / 3]
        stop = (below[-1] if below else c["low"]) - tol / 3
        ups = [l for l in levels if l["price"] > entry + max(tol, min_stop_distance(symbol))]
    else:
        above = [l["price"] for l in levels if l["price"] > c["high"] + tol / 3]
        stop = (above[0] if above else c["high"]) + tol / 3
        ups = sorted([l for l in levels if l["price"] < entry - max(tol, min_stop_distance(symbol))],
                     key=lambda l: -l["price"])
    stop = widen_stop(symbol, side, entry, stop)
    risk = abs(entry - stop)
    from services.trade_gate import MIN_RR
    tps, last = [], entry
    for l in ups:                       # TP1 ≥ MIN_RR, each next TP ≥ 1R further on
        move = abs(l["price"] - entry)
        if move < MIN_RR * risk or (tps and abs(l["price"] - last) < risk):
            continue
        tps.append({"name": f"TP{len(tps) + 1}", "price": _r(symbol, l["price"]),
                    "level": f"{l['year']} {l['dev']}", "rr": round(move / risk, 2)})
        last = l["price"]
        if len(tps) == 3:
            break
    far = 4 if side == "BUY" else -4
    by_year = {l["year"]: l for l in levels if l["k"] == far}
    objectives = [{"level": f"{y} {label(far)}", "price": _r(symbol, by_year[y]["price"])}
                  for y in c["years"] if y in by_year
                  and (not tps or (by_year[y]["price"] - tps[-1]["price"]) * (1 if side == "BUY" else -1) > 0)]
    return {"side": side, "status": "limit", "entry": _r(symbol, entry), "stop": _r(symbol, stop),
            "zone": [c["low"], c["high"]], "confluence": c["members"], "targets": tps,
            "objectives": objectives,
            "ote": [_r(symbol, o["zone_bottom"]), _r(symbol, o["zone_top"])]}


def card(r: Dict) -> str:
    from routes.cbdr import px
    s = r["symbol"]
    lines = [f"📅 *Yearly CBDR — {s}* (Q1 body boxes, {len(r['years'])}y)",
             f"Price {px(s, r['price'])} · range in control: *{r['control']}*"
             + (f" · {r['forming']} box still forming (Jan–Mar)" if r.get("forming") else "")]
    for y in r["years"]:
        pk = y["peak"]
        pk_txt = (f" · peak {label(pk['up'])} / {label(pk['down'])}" if pk.get("up") is not None else "")
        src = " ✍️" if y.get("source") not in (None, "computed") else ""
        lines.append(f"`{y['year']}`{src} box {px(s, y['box_low'])}–{px(s, y['box_high'])} · "
                     f"now at {y['position_dev']}{pk_txt}")
    mg = r.get("magnet")
    if mg:
        lines.append(f"🧲 magnet: {mg['year']} mean {px(s, mg['mean'])} — crossed {mg['crosses']}× "
                     f"on weekly closes; price is {mg['side']}")
    h = r.get("history")
    if h:
        lines.append(f"📚 {h['years']}y history ({h['span']}): median run {h['median_up']:g} / "
                     f"{h['median_down']:g} · reach 2.5 in {h['pct_reach_up']['2.5']}% · "
                     f"reach 4 in {h['pct_reach_up']['4']}% · reach −1 in {h['pct_reach_down']['-1']}%")
    o = r.get("ote")
    if o:
        lines.append(f"OTE ({o['leg']} leg {o['from']}→{o['to']}): {px(s, o['zone_bottom'])}–"
                     f"{px(s, o['zone_top'])} (0.705 {px(s, o['sweet'])})")
    near = sorted(r["clusters"], key=lambda c: abs(c["distance"]))[:3]
    for c in near:
        lines.append(f"{'✨' if c['in_ote'] else '•'} {px(s, c['low'])}–{px(s, c['high'])}: "
                     + ", ".join(c["members"]))
    p = r.get("plan")
    if p and p.get("status") == "limit":
        lines.append(f"\n🎯 *{p['side']} LIMIT* {px(s, p['entry'])} · SL {px(s, p['stop'])}")
        lines.append("   " + " · ".join(f"{t['name']} {px(s, t['price'])} ({t['level']}, {t['rr']}R)"
                                         for t in p["targets"]))
        if p.get("objectives"):
            lines.append("   yearly objective: " + " · ".join(f"{o['level']} {px(s, o['price'])}"
                                                            for o in p["objectives"]))
        try:
            from propfirm.target_lots import card_lines
            lines += card_lines(s, p["entry"], p["stop"], p["targets"])
        except Exception:
            pass
        g = p.get("gate")
        if g and not g["allow"]:
            lines.append("⚠️ gate: " + "; ".join(g["reasons"]))
    elif p:
        lines.append(f"🎯 {p['side']} bias — {p['note']}")
    lines.append("_Swing framework — place limits only with the weekly profile + COT in agreement._")
    return "\n".join(lines)


_cache: Dict[str, Dict] = {}


async def run(symbol: str, years_back: int = YEARS_BACK) -> Dict:
    hit = _cache.get(f"{symbol}:{years_back}")
    if hit and time.time() - hit["_t"] < 6 * 3600:
        return hit["r"]
    from services.ohlc_service import fetch_ohlc
    from services import trade_gate as tg
    n = min(5000, 262 * (years_back + 1) + 60)
    daily = await fetch_ohlc(symbol, "1day", n, broker=False)
    if not daily:                       # indices (NAS100, US30, SPX) aren't on TwelveData free
        try:
            from services.ohlc_service import _yahoo_ohlc
            daily = await _yahoo_ohlc(symbol, "1day", n)
        except Exception:
            daily = []
    r = analyse(symbol, daily, years_back=years_back)
    p = r.get("plan") if r.get("ok") else None
    if p and p.get("status") == "limit":
        weekly = await tg.weekly_profile(symbol)
        g = tg.evaluate(symbol, p["side"], p["entry"], p["stop"], [t["price"] for t in p["targets"]],
                        weekly=weekly)
        g["reasons"] = [x for x in g["reasons"] if "CBDR box forming" not in x]   # limits rest
        g["allow"] = not g["reasons"]
        p["gate"] = g
    if r.get("ok"):
        _cache[f"{symbol}:{years_back}"] = {"r": r, "_t": time.time()}
    return r


async def alert(symbols: List[str], years_back: int = YEARS_BACK, notify: bool = True) -> Dict:
    from services.realtime import alert_once
    out, sent = [], []
    week = time.strftime("%G-W%V", time.gmtime())
    for s in symbols:
        r = await run(s, years_back)
        out.append(r)
        if notify and r.get("ok") and await alert_once(f"yearly:{s}:{week}", card(r), ttl_hours=24 * 7):
            sent.append(s)
    return {"results": out, "notified": sent}
