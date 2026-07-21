"""Drone fib-recon sweep — continuous situational awareness for gold + DXY (pure).

The scout layer above the gates: it reads WHERE both instruments sit on their fib
maps at once and flags when a sniper setup is armed. It fuses —

  GOLD : HTF timeline ladder position + named OB zones + zone-to-zone pip budget
         + (optional CBDR box) the ±1/±1.5SD deviation extreme
  DXY  : dollar regime + monthly fib bands + the 2026 gold-long LOCK state

— into one recon report. A BUY setup is armed only when gold is anchored in a buy
OB at the CBDR extreme AND DXY has flipped (longs unlocked); a SELL setup is armed
when gold is anchored in a sell OB at the +SD extreme (shorts run while DXY is bid).
This is the recon feed behind the strict gate — it tells you the whole board.
"""

from __future__ import annotations

from typing import Optional

from gold import timeline as tl
from gold import zones as gz
from gold import dxy as gdxy


def _nearest_dxy_fib(price: Optional[float]) -> dict:
    """Nearest DXY structural fib level above/below a price (the drone's DXY read)."""
    if price is None:
        return {"above": None, "below": None}
    levels = {
        "pivot_102.50": gdxy.PIVOT,
        "sell_band_lo_105.17": gdxy.SELL_BAND[0], "sell_band_hi_107.49": gdxy.SELL_BAND[1],
        "ceiling_lo_104.59": gdxy.GOLD_TRIGGERS["ceiling"][0],
        "ceiling_hi_107.10": gdxy.GOLD_TRIGGERS["ceiling"][1],
        "top_114.54": gdxy.TOP,
        "discount_lo_93.31": gdxy.GOLD_TRIGGERS["last_discount"][0],
        "discount_hi_96.27": gdxy.GOLD_TRIGGERS["last_discount"][1],
        "demand_87.71": gdxy.DEMAND_SHELF[0],
    }
    above = {k: v for k, v in levels.items() if v > price}
    below = {k: v for k, v in levels.items() if v <= price}
    na = min(above.items(), key=lambda kv: kv[1] - price) if above else None
    nb = max(below.items(), key=lambda kv: kv[1]) if below else None
    return {"above": {"level": na[0], "price": na[1]} if na else None,
            "below": {"level": nb[0], "price": nb[1]} if nb else None}


def recon_sweep(gold_price: float, dxy_price: Optional[float] = None,
                box=None, rbusbis_dir: Optional[str] = None,
                b2b: Optional[dict] = None, warthog: Optional[dict] = None) -> dict:
    """Fuse the gold + DXY fib maps into one recon report with armed setups.

    ``b2b`` is an optional 4H b2b-bomber read; ``warthog`` an optional HTF sweep+OTE
    read — both, when they agree with the anchored setup, are swing confluence."""
    # --- GOLD scout ---------------------------------------------------------
    gloc = tl.locate(gold_price)
    zf = gz.zone_for(gold_price)
    budget = gz.zone_budget(gold_price)
    buy_anchor = gz.zone_anchored_entry("long", gold_price, box)
    sell_anchor = gz.zone_anchored_entry("short", gold_price, box)

    # --- DXY scout ----------------------------------------------------------
    dreg = gdxy.dollar_regime(dxy_price)
    dflip = gdxy.dxy_flip_status(dxy_price, rbusbis_dir)
    dtrig = gdxy.gold_structure_trigger(dxy_price)
    dfib = _nearest_dxy_fib(dxy_price)
    longs_unlocked = dflip["unlocked"]

    # --- fuse into armed setups --------------------------------------------
    b2b_sig = (b2b or {}).get("signal") if b2b else None
    wh_sig = (warthog or {}).get("signal") if warthog else None
    setups = []
    if buy_anchor["ok"]:
        confl = b2b_sig == "LONG"
        wh_ok = wh_sig == "LONG"
        setups.append({
            "side": "LONG", "zone": buy_anchor["ob_zone"], "cbdr_level": buy_anchor["cbdr_level"],
            "armed": bool(longs_unlocked), "b2b_confluence": confl, "warthog_confluence": wh_ok,
            "gate": ("armed — OB + deviation extreme + DXY flipped"
                     + (" + 4H b2b" if confl else "") + (" + warthog OTE" if wh_ok else "")
                     if longs_unlocked else
                     "STAGED — OB + deviation extreme, but DXY longs still LOCKED"),
        })
    if sell_anchor["ok"]:
        confl = b2b_sig == "SHORT"
        wh_ok = wh_sig == "SHORT"
        setups.append({
            "side": "SHORT", "zone": sell_anchor["ob_zone"], "cbdr_level": sell_anchor["cbdr_level"],
            "armed": True, "b2b_confluence": confl, "warthog_confluence": wh_ok,
            "gate": "armed — sell OB + deviation extreme (shorts run while DXY bid)"
                    + (" + 4H b2b" if confl else "") + (" + warthog OTE" if wh_ok else ""),
        })
    armed = any(s["armed"] for s in setups)

    return {
        "gold": {
            "price": round(gold_price, 2),
            "htf_k": gloc["k"], "htf_region": gloc["region"], "htf_bias": gloc["smaller_tf_bias"],
            "nearest_above": gloc.get("nearest_above"), "nearest_below": gloc.get("nearest_below"),
            "in_zone": zf.get("inside"), "buy_anchor": buy_anchor, "sell_anchor": sell_anchor,
            "budget": budget,
        },
        "dxy": {
            "price": dreg["price"], "regime": dreg["regime"], "phase": dreg["phase"],
            "trigger": dtrig.get("trigger"), "gold_longs": dflip["gold_longs"],
            "at_extreme": dflip["at_extreme"], "nearest_fib": dfib, "note": dflip["note"],
        },
        "b2b": b2b, "warthog": warthog,
        "setups": setups, "armed": armed,
        "note": (f"gold {gloc['region']} @ {round(gold_price, 2)} | dollar "
                 f"{dreg['regime']}/{dreg['phase']} | longs {dflip['gold_longs']} | "
                 f"{'ARMED' if armed else 'no armed setup'}"),
    }


def format_recon(sweep: dict) -> str:
    """Telegram card for a recon sweep — the drone's board read."""
    g, d = sweep["gold"], sweep["dxy"]
    lock_icon = "🔓" if d["gold_longs"] == "unlocked" else "🔒"
    lines = [
        f"🛰️ *GOLD/DXY RECON* — {'⚡ARMED' if sweep['armed'] else 'scanning'}",
        "",
        f"*GOLD* {g['price']}  ·  HTF _{g['htf_region']}_ (k={g['htf_k']})",
    ]
    b = g.get("budget") or {}
    if b.get("round_trip_bag_pips"):
        lines.append(f"  {b['note']}")
    lines.append(f"*DXY* {d['price']}  ·  {d['regime']}/{d['phase']}  {lock_icon} longs {d['gold_longs']}")
    if d.get("nearest_fib", {}).get("above") or d.get("nearest_fib", {}).get("below"):
        nf = d["nearest_fib"]
        parts = []
        if nf.get("below"):
            parts.append(f"↓{nf['below']['level']} {nf['below']['price']}")
        if nf.get("above"):
            parts.append(f"↑{nf['above']['level']} {nf['above']['price']}")
        lines.append("  DXY fib: " + "  ".join(parts))
    b2b = sweep.get("b2b")
    if b2b and b2b.get("signal") in ("LONG", "SHORT"):
        tag = f" · {b2b['anchor_session']}" if b2b.get("anchored") else ""
        lines.append(f"  💣 4H b2b: {b2b['signal']}{tag} (swept {b2b['swept']}, inval {b2b['invalidation']})")
    wh = sweep.get("warthog")
    if wh and wh.get("signal") in ("LONG", "SHORT"):
        sw = wh.get("sweep")
        swept = f"swept {sw['type']} {sw['level']}, " if sw else ""
        load = "⚡loaded" if wh.get("in_ote") and wh.get("catapult") else "await OTE"
        lines.append(f"  🐗 warthog: {wh['signal']} ({swept}{int(wh['retracement']*100)}% OTE, {load})")
    lines.append("")
    if sweep["setups"]:
        for s in sweep["setups"]:
            flag = "⚡" if s["armed"] else "⏸"
            confl = ("  +b2b✅" if s.get("b2b_confluence") else "") + \
                    ("  +🐗OTE" if s.get("warthog_confluence") else "")
            lines.append(f"{flag} *{s['side']}* {s['zone']} @ {s['cbdr_level']} — {s['gate']}{confl}")
    else:
        lines.append("_no OB+deviation anchor yet — price between zones_")
    lines.append("")
    lines.append(f"_{d['note']}_")
    return "\n".join(l for l in lines if l is not None)
