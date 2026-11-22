"""Z-score × COT/retail confluence → graded mean-reversion SETUPS with levels + lots.

The 1h z-score breach says *where* price is stretched; the weekly confluence (COT
positioning + Myfxbook retail, contrarian) says *which way* the bigger flow leans.

  A  CONFLUENCE  z lean == confluence verdict (LONG/SHORT, conf ≥ 70)  → full card
  B  Z-ONLY      no directional confluence (FLAT / WATCH / missing)     → full card, half size
  X  CONFLICT    z lean opposite the confluence verdict                 → no trade (flagged)

Levels from the 1h window (mean m, sd s), z = current stretch:
  entry = last close · stop = one more σ beyond the extreme (m ± (|z|+1)s → risk = 1σ)
  TP1 = back inside ±1σ (m ± s) · TP2 = the mean m   ⇒  TP1 = |z|−1 R, TP2 = |z| R
Lots = the owner's target ladder (propfirm.target_lots), capped by the risk limit.
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional

MIN_CONF_A = 70
_PAIR = {"XAU/USD": "XAU/USD"}


def grade(lean: str, conf_row: Optional[Dict]) -> str:
    v = (conf_row or {}).get("verdict")
    if v in ("LONG", "SHORT") and (conf_row.get("confidence") or 0) >= MIN_CONF_A:
        return "A" if (v == "LONG") == (lean == "BUY") else "X"
    return "B"


def levels(lean: str, last: float, mean: float, sd: float, z: float) -> Optional[Dict]:
    if not sd or sd <= 0:
        return None
    sgn = -1 if lean == "SELL" else 1                  # SELL: price above mean, target down
    stop = mean - sgn * (abs(z) + 1) * sd
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


def build(breaches: List[Dict], signals: List[Dict], cot_date: Optional[str]) -> List[Dict]:
    by_pair = {s.get("pair"): s for s in signals or []}
    out = []
    for b in breaches:
        if not (b.get("ok") and b.get("fired") and b.get("lean") and b.get("sd")):
            continue
        row = by_pair.get(b["symbol"])
        g = grade(b["lean"], row)
        lv = levels(b["lean"], float(b["last_close"]), float(b["mean"]), float(b["sd"]), float(b["z"]))
        if not lv:
            continue
        out.append({"symbol": b["symbol"], "lean": b["lean"], "z": b["z"], "fresh": b.get("fresh"),
                    "grade": g, "levels": lv,
                    "confluence": ({k: row.get(k) for k in ("verdict", "confidence", "cot_agrees",
                                                             "cot_missing", "retail_long")}
                                   if row else None),
                    "cot_as_of": cot_date})
    out.sort(key=lambda r: ("AXB".index(r["grade"]) if r["grade"] in "AXB" else 9, -abs(r["z"])))
    return out


def card(r: Dict) -> str:
    from propfirm.target_lots import card_lines
    lv, c = r["levels"], r.get("confluence") or {}
    arrow = "🔴 SELL" if r["lean"] == "SELL" else "🟢 BUY"
    tag = {"A": "🅰️ Z + COT/retail CONFLUENCE", "B": "🅱️ Z-score only (half size)"}[r["grade"]]
    lines = [f"⚡ *Z-setup — {r['symbol']}* {arrow}", tag,
             f"z {r['z']:+.2f} (1h){' · fresh' if r.get('fresh') else ''}"]
    if c:
        cot = "COT missing" if c.get("cot_missing") else \
            f"COT {'agrees' if c.get('cot_agrees') else 'disagrees'} (as of {r.get('cot_as_of') or '?'})"
        lines.append(f"Confluence: {c.get('verdict')} conf {c.get('confidence')} · retail {c.get('retail_long')}%L · {cot}")
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
    setups = build(results, feed.get("signals") or [], cot_latest(feed))
    sent = []
    if notify:
        from services.realtime import alert_once
        day = time.strftime("%Y-%m-%d", time.gmtime())
        for r in setups:
            if r["grade"] in ("A", "B") and await alert_once(
                    f"z:{r['symbol']}:{r['lean']}:{day}", card(r), ttl_hours=12):
                sent.append(r["symbol"])
    return {"z_threshold": z_threshold, "cot_as_of": cot_latest(feed),
            "setups": setups, "notified": sent,
            "conflicts": [r["symbol"] for r in setups if r["grade"] == "X"]}
