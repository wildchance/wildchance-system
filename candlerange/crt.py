"""1-5-9 Candle Range Theory (CRT) — pure, deterministic, stdlib-only.

The "159" model on the 1-hour chart:

  • RANGE candle  at 1 o'clock (01:00 / 13:00 UTC) — its open→close body is the
    range; standard-deviation levels project outward from it (like a CBDR box).
  • CONFIRMATION  at 5 o'clock (05:00 / 17:00) — price returning to a −dev buy
    limit or a +dev sell limit is the entry trigger; the 05:00 leg catches the
    Asian session, the 17:00 leg the New York session.
  • TARGET        at 9 o'clock (09:00 / 21:00) — the completion / take-profit.

So the 1am candle arms Asian-session limits confirmed at 5am; the 1pm candle arms
NY-session limits confirmed at 5pm. Deviations are whole/half multiples of the
range-candle body — a −1 dev buy limit, +1/+3 dev sell limits, etc.
"""

from __future__ import annotations

from typing import Dict, Optional, Sequence, Tuple

# Deviation multiples projected from the 1-o'clock range-candle body.
CRT_DEVIATIONS: Tuple[float, ...] = (0.5, 1.0, 1.5, 2.0, 2.5, 3.0)

# Which 1-o'clock candle feeds which session, and its 5/9 confirmation/target hours.
SESSIONS = {
    "asia": {"range_hour": 1, "confirm_hour": 5, "target_hour": 9, "label": "Asian (1-5-9)"},
    "ny":   {"range_hour": 13, "confirm_hour": 17, "target_hour": 21, "label": "New York (1-5-9)"},
}


def session_for_hour(hour: int) -> Optional[str]:
    """Map a UTC hour to the CRT session whose confirmation window it is in.

    The 05:00 read works the Asian model (01:00 range candle); 17:00 works the NY
    model (13:00 range candle). Accept the range hour too for setup-only calls.
    """
    if hour in (1, 5, 9):
        return "asia"
    if hour in (13, 17, 21):
        return "ny"
    return None


def crt_levels(candle: dict, deviations: Sequence[float] = CRT_DEVIATIONS) -> Dict[str, float]:
    """Deviation levels projected from a range candle's open→close body.

    −n dev sits below the body low (buy-limit side); +n dev above the body high
    (sell-limit side). Body, not wick, per the "open and close range".
    """
    o, c = float(candle["open"]), float(candle["close"])
    lo, hi = min(o, c), max(o, c)
    rng = hi - lo
    levels: Dict[str, float] = {"body_low": round(lo, 2), "body_high": round(hi, 2),
                                "range": round(rng, 2)}
    for d in deviations:
        levels[f"+{d:g}"] = round(hi + d * rng, 2)
        levels[f"-{d:g}"] = round(lo - d * rng, 2)
    return levels


def crt_setup(candle: dict, session: str,
              buy_devs: Sequence[float] = (1.0,),
              sell_devs: Sequence[float] = (1.0, 3.0)) -> dict:
    """Arm the CRT limit ladder for a session from its 1-o'clock range candle.

    Buy limits at the −dev levels (discount), sell limits at the +dev levels
    (premium / extreme). Returns the levels, the limit orders, and the confirm /
    target hours.
    """
    meta = SESSIONS.get(session)
    if meta is None:
        return {"ok": False, "reason": f"unknown CRT session '{session}'"}
    lv = crt_levels(candle)
    if lv["range"] <= 0:
        return {"ok": False, "reason": "flat range candle — no CRT range"}
    orders = []
    for d in buy_devs:
        orders.append({"side": "long", "kind": "limit", "dev": -d,
                       "entry": lv[f"-{d:g}"], "reason": f"buy limit at −{d:g} dev"})
    for d in sell_devs:
        orders.append({"side": "short", "kind": "limit", "dev": d,
                       "entry": lv[f"+{d:g}"], "reason": f"sell limit at +{d:g} dev"})
    return {"ok": True, "session": session, "label": meta["label"],
            "range_hour": meta["range_hour"], "confirm_hour": meta["confirm_hour"],
            "target_hour": meta["target_hour"], "levels": lv, "orders": orders}


def crt_confirm(setup: dict, price: float, tol_frac: float = 0.10) -> dict:
    """At the 5-o'clock candle, is price at a CRT limit → entry confirmation?

    A hit is price within ``tol_frac`` of the range of a limit level. Returns the
    triggered order (nearest hit) or a no-trigger note.
    """
    if not setup.get("ok"):
        return {"confirmed": False, "reason": setup.get("reason", "no setup")}
    rng = setup["levels"]["range"]
    tol = tol_frac * rng
    hit = None
    for o in setup["orders"]:
        dist = abs(price - o["entry"])
        if dist <= tol and (hit is None or dist < hit["distance"]):
            hit = {**o, "distance": round(dist, 2)}
    if hit is None:
        return {"confirmed": False, "price": price,
                "reason": "price not at a CRT limit yet — no confirmation"}
    return {"confirmed": True, "price": price, "order": hit,
            "note": f"price at {hit['dev']:+g} dev — {hit['side']} confirmation "
                    f"(target at {setup['target_hour']:02d}:00)"}
