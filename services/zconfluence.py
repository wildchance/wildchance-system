"""Z-score × COT/retail confluence → graded mean-reversion SETUPS with levels + lots.

The 1h z-score breach says *where* price is stretched; the weekly confluence (COT
positioning + Myfxbook retail, contrarian) says *which way* the bigger flow leans.

  A  CONFLUENCE  z lean == confluence verdict (conf ≥ 70) AND COT agrees (not missing)
                 AND the ICT weekly profile confirms                    → alert (default)
  B  PARTIAL     no conflict, but something is missing (COT absent, weekly neutral)
                                                                        → listed, not sent
  X  CONFLICT    z lean opposite the confluence verdict, COT, or the weekly profile
                                                                        → no trade
Every alert also passes services.trade_gate: no trades while the CBDR box forms
(14:00–20:00 NY) or in the thin Sunday open, stop ≥ the owner's 10 pips (gold $3),
furthest target ≥ 1.3R. ZC_NOTIFY_GRADES (default "A") chooses what is sent.

Levels from the 1h window (mean m, sd s), z = current stretch:
  entry = last close · stop = one more σ beyond the extreme (m ± (|z|+1)s → risk = 1σ)
  TP1 = back inside ±1σ (m ± s) · TP2 = the mean m   ⇒  TP1 = |z|−1 R, TP2 = |z| R
Lots = the owner's target ladder (propfirm.target_lots), capped by the risk limit.
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional

from decouple import config

from services import trade_gate as tg

MIN_CONF_A = 70
NOTIFY_GRADES = set(str(config("ZC_NOTIFY_GRADES", default="A")).replace(" ", "").split(","))
_PAIR = {"XAU/USD": "XAU/USD"}


def cot_missing(row: Optional[Dict]) -> bool:
    return bool(row) and (bool(row.get("cot_missing")) or row.get("cot_net") is None)


def grade(lean: str, conf_row: Optional[Dict], weekly: str = "neutral") -> str:
    """A only when EVERYTHING agrees; X on any opposition; B otherwise."""
    row = conf_row or {}
    v = row.get("verdict")
    want = "LONG" if lean == "BUY" else "SHORT"
    strong = v in ("LONG", "SHORT") and (row.get("confidence") or 0) >= MIN_CONF_A
    if (strong and v != want) or weekly == "diverges":
        return "X"
    if strong and not cot_missing(row) and row.get("cot_agrees") is False:
        return "X"                                   # COT present and opposing
    if strong and v == want and not cot_missing(row) and row.get("cot_agrees") \
            and weekly == "confirms":
        return "A"
    return "B"


def levels(lean: str, last: float, mean: float, sd: float, z: float,
           symbol: str = "") -> Optional[Dict]:
    if not sd or sd <= 0:
        return None
    sgn = -1 if lean == "SELL" else 1                  # SELL: price above mean, target down
    stop = mean - sgn * (abs(z) + 1) * sd
    if symbol:                                         # never a noise stop (owner: ≥ 10 pips)
        stop = tg.widen_stop(symbol, lean, last, stop)
    tp1 = mean - sgn * sd
    tp2 = mean
    risk = abs(last - stop)
    if risk <= 0 or (tp1 - last) * sgn <= 0:           # already back inside ±1σ
        tp1 = None
    def rr(tp):
        return round(abs(tp - last) / risk, 2) if tp is not None else None
    return {"entry": round(last, 5), "stop": round(stop, 5),
            "tp1": round(tp1, 5) if tp1 is not None else None, "tp2": round(tp2, 5),
            "r_tp1": rr(tp1), "r_tp2": rr(tp2)}


def build(breaches: List[Dict], signals: List[Dict], cot_date: Optional[str],
          weekly: Optional[Dict[str, Dict]] = None, now=None,
          locks: Optional[Dict[str, str]] = None) -> List[Dict]:
    by_pair = {s.get("pair"): s for s in signals or []}
    weekly = weekly or {}
    out = []
    for b in breaches:
        if not (b.get("ok") and b.get("fired") and b.get("lean") and b.get("sd")):
            continue
        row = by_pair.get(b["symbol"])
        wread = weekly.get(b["symbol"])
        wv = tg.weekly_verdict(wread, b["lean"])
        g = grade(b["lean"], row, wv)
        lock = (locks or {}).get(b["symbol"].replace("/", ""))
        if lock and ((lock == "BUY_ONLY" and b["lean"] == "SELL") or
                     (lock == "SELL_ONLY" and b["lean"] == "BUY")):
            g = "X"                                    # against the Stage-0 macro lock
        lv = levels(b["lean"], float(b["last_close"]), float(b["mean"]), float(b["sd"]),
                    float(b["z"]), b["symbol"])
        if not lv:
            continue
        gate = tg.evaluate(b["symbol"], b["lean"], lv["entry"], lv["stop"],
                           [lv["tp1"], lv["tp2"]], weekly=wread, now=now)
        out.append({"symbol": b["symbol"], "lean": b["lean"], "z": b["z"], "fresh": b.get("fresh"),
                    "grade": g, "levels": lv, "gate": gate,
                    "weekly": {"verdict": wv, **({k: wread.get(k) for k in ("bias", "profile", "reason")}
                                                if wread else {})},
                    "confluence": ({k: row.get(k) for k in ("verdict", "confidence", "cot_agrees",
                                                             "retail_long")}
                                   | {"cot_missing": cot_missing(row)} if row else None),
                    "cot_as_of": cot_date})
    out.sort(key=lambda r: ("ABX".index(r["grade"]), -abs(r["z"])))
    return out


def sendable(r: Dict) -> bool:
    return r["grade"] in NOTIFY_GRADES and r["gate"]["allow"]


def card(r: Dict) -> str:
    from propfirm.target_lots import card_lines
    lv, c = r["levels"], r.get("confluence") or {}
    arrow = "🔴 SELL" if r["lean"] == "SELL" else "🟢 BUY"
    tag = {"A": "🅰️ Z + COT + retail + weekly profile ALIGNED",
           "B": "🅱️ partial confluence (half size)", "X": "✖️ conflict"}[r["grade"]]
    lines = [f"⚡ *Z-setup — {r['symbol']}* {arrow}", tag,
             f"z {r['z']:+.2f} (1h){' · fresh' if r.get('fresh') else ''}"]
    if c:
        cot = "COT missing" if c.get("cot_missing") else \
            f"COT {'agrees' if c.get('cot_agrees') else 'disagrees'} (as of {r.get('cot_as_of') or '?'})"
        lines.append(f"Confluence: {c.get('verdict')} conf {c.get('confidence')} · retail {c.get('retail_long')}%L · {cot}")
    w = r.get("weekly") or {}
    if w.get("bias"):
        lines.append(f"Weekly profile: {w['bias']} ({w.get('profile')}) → {w.get('verdict')}")
    lines += ["", f"Entry: `{lv['entry']}`", f"Stop:  `{lv['stop']}`  (1σ beyond the extreme)"]
    tps = []
    if lv["tp1"] is not None:
        lines.append(f"TP1:   `{lv['tp1']}`  (R {lv['r_tp1']}) back inside ±1σ")
        tps.append({"name": "TP1", "price": lv["tp1"]})
    lines.append(f"TP2:   `{lv['tp2']}`  (R {lv['r_tp2']}) the 1h mean")
    tps.append({"name": "TP2", "price": lv["tp2"]})
    lots = card_lines(r["symbol"], lv["entry"], lv["stop"], tps,
                      max_risk_pct=0.5 if r["grade"] == "A" else 0.25)
    if lots:
        lines += [""] + lots
    return "\n".join(lines)


async def scan(z_threshold: float = 2.0, notify: bool = False, max_age_s: int = 900) -> Dict:
    from services import intraday_service as isvc
    from services.wildchance_service import get_latest_feed, cot_latest
    last = isvc.LAST_SCAN
    if last["results"] and time.time() - last["t"] < max_age_s and last["z"] == z_threshold:
        results = last["results"]                       # reuse the hourly /intraday/scan
    else:
        results = await isvc.scan(z_threshold=z_threshold)
    feed = await get_latest_feed() or {}
    fired = sorted({b["symbol"] for b in results if b.get("ok") and b.get("fired")})
    weekly = {sym: await tg.weekly_profile(sym) for sym in fired}
    from services.swing_service import macro_bias
    setups = build(results, feed.get("signals") or [], cot_latest(feed), weekly,
                   locks=macro_bias())
    sent = []
    if notify:
        from services.realtime import alert_once
        day = time.strftime("%Y-%m-%d", time.gmtime())
        for r in setups:
            if sendable(r) and await alert_once(
                    f"z:{r['symbol']}:{r['lean']}:{day}", card(r), ttl_hours=12):
                sent.append(r["symbol"])
    return {"z_threshold": z_threshold, "cot_as_of": cot_latest(feed),
            "notify_grades": sorted(NOTIFY_GRADES), "setups": setups, "notified": sent,
            "held": [{"symbol": r["symbol"], "grade": r["grade"], "why": r["gate"]["reasons"]}
                     for r in setups if not sendable(r)],
            "conflicts": [r["symbol"] for r in setups if r["grade"] == "X"]}
