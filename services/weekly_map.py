"""Weekly map — Monday's CBDR box projected through the week, steered by COT.

Monday's CBDR (14:00–20:00 New York, hourly highs/lows) is the week's anchor box.
Its standard-deviation ladder (cbdr.engine.sd_ladder: 0 = box high, −1 = box low,
±0.5 steps to ±4) maps where the week can travel. Direction comes from the
positioning read (COT + retail confluence for the pair) with the weekly profile as a
tiebreak. Output: the box, the bias, the next 3 levels in the bias direction (the
week's path) and the nearest levels against it (where the bias is wrong).
"""

from __future__ import annotations

import datetime as dt
from typing import Dict, List, Optional


def monday_box(by_day: Dict[dt.date, tuple], today: dt.date) -> Optional[Dict]:
    monday = today - dt.timedelta(days=today.weekday())
    box = by_day.get(monday)
    if not box:
        return None
    return {"date": monday.isoformat(), "low": box[0], "high": box[1]}


def bias_from(cot_row: Optional[Dict], weekly: Optional[Dict]) -> Dict:
    v = (cot_row or {}).get("verdict")
    conf = (cot_row or {}).get("confidence") or 0
    if v in ("LONG", "SHORT") and conf >= 60:
        return {"side": "BUY" if v == "LONG" else "SELL", "why": f"COT/retail {v} conf {conf}"
                + (" · COT agrees" if (cot_row or {}).get("cot_agrees") else "")}
    wb = (weekly or {}).get("bias")
    if wb in ("long", "short"):
        return {"side": "BUY" if wb == "long" else "SELL", "why": f"weekly profile {wb} (COT neutral)"}
    return {"side": None, "why": "no positioning edge — map only"}


def build(box: Dict, price: float, bias: Dict) -> Dict:
    from cbdr.engine import sd_ladder
    lad = sd_ladder(box["high"], box["low"])
    levels = sorted(((float(k), v) for k, v in lad["levels"].items()), key=lambda x: x[1])
    up = [{"k": k, "price": v} for k, v in levels if v > price][:3]
    down = [{"k": k, "price": v} for k, v in sorted(levels, key=lambda x: -x[1]) if v < price][:3]
    side = bias.get("side")
    path, risk = (up, down) if side == "BUY" else (down, up) if side == "SELL" else ([], [])
    pos = (price - box["high"]) / (box["high"] - box["low"]) if box["high"] > box["low"] else None
    return {"box": box, "mean": lad["mean"], "unit": lad["unit"], "price": price,
            "position_k": round(pos, 2) if pos is not None else None, "bias": bias,
            "path": path, "against": risk[:2], "up": up, "down": down}


def card(sym: str, m: Dict) -> str:
    from routes.cbdr import px
    b = m["box"]
    lines = [f"🗺 *Weekly map — {sym}* (Monday CBDR {px(sym, b['low'])}–{px(sym, b['high'])}, "
             f"mean {px(sym, m['mean'])})",
             f"Price {px(sym, m['price'])} = {m['position_k']:+g} box units from the box high"
             if m.get("position_k") is not None else f"Price {px(sym, m['price'])}",
             f"Bias: *{m['bias']['side'] or 'NEUTRAL'}* — {m['bias']['why']}"]
    if m["path"]:
        lines.append("Week's path: " + " → ".join(f"{p['k']:+g} {px(sym, p['price'])}" for p in m["path"]))
        lines.append("Wrong if it breaks: " + ", ".join(f"{p['k']:+g} {px(sym, p['price'])}" for p in m["against"]))
    else:
        lines.append("Up: " + ", ".join(f"{p['k']:+g} {px(sym, p['price'])}" for p in m["up"])
                     + " · Down: " + ", ".join(f"{p['k']:+g} {px(sym, p['price'])}" for p in m["down"]))
    return "\n".join(lines)


async def run(symbol: str, notify: bool = False) -> Dict:
    from services.auto_boxes import cbdr_by_day
    from services.ohlc_service import fetch_ohlc_raw
    today = dt.datetime.now(dt.timezone.utc).date()
    try:
        bars = await fetch_ohlc_raw(symbol, "1h", 120, timezone="America/New_York")
    except Exception:
        bars = []
    box = monday_box(cbdr_by_day(bars), today) if bars else None
    if not box:
        return {"symbol": symbol, "ok": False, "reason": "Monday's CBDR box not formed / no data"}
    price = bars[-1]["close"]
    cot_row = weekly = None
    try:
        from services.wildchance_service import get_latest_feed
        feed = await get_latest_feed() or {}
        cot_row = next((s for s in feed.get("signals") or [] if s.get("pair") == symbol), None)
    except Exception:
        pass
    try:
        from services.trade_gate import weekly_profile
        weekly = await weekly_profile(symbol)
    except Exception:
        pass
    m = build(box, price, bias_from(cot_row, weekly))
    res = {"symbol": symbol, "ok": True, **m}
    if notify:
        from services.realtime import alert_once
        res["sent"] = await alert_once(f"weekmap:{symbol}:{box['date']}", card(symbol, m), ttl_hours=24 * 6)
    return res
