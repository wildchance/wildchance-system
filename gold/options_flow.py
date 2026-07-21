"""Gold options-flow confluence — put/call walls + the expected-move envelope (pure).

The gold futures-options positioning the COT feed can't see, turned into confluence:

  • EXPECTED-MOVE envelope — the market-implied 1σ/2σ/3σ bands around the future
    price (a market-priced cross-check on our CBDR deviations).
  • PUT / CALL WALLS — the heaviest-volume strikes: the put wall is dealer-defended
    SUPPORT, the call wall dealer-defended RESISTANCE (why price rejects there).
  • PUT/CALL SKEW — the directional pressure of the day's flow.

Operator-fed (like the WGC slot): there is no free options API, so INPUTS starts
empty and everything degrades to 'no data / neutral' until fed via set_inputs() /
POST /gold/options. Once fed, a SELL that lands on the call wall AND the 2σ top is
institutional-grade confluence; a BUY on the put wall is a defended long.
"""

from __future__ import annotations

from typing import Optional

from gold.risk_engine import GOLD_PIP

# Operator-fed snapshot — fill from the options board at session open. All None =
# not fed yet → confluence returns neutral and nothing downstream is affected.
INPUTS = {
    "as_of": None,
    "future": None,          # the reference future price (e.g. 4030.4)
    "put_wall": None,        # heaviest-put strike — dealer SUPPORT
    "call_wall": None,       # heaviest-call strike — dealer RESISTANCE
    "sigma": {"1": None, "2": None, "3": None},   # expected-move HALF-widths ($)
    "put_vol": None,
    "call_vol": None,
}

DEFAULT_TOL = 3.0            # a level within $3 (30 pips) counts as "at" a wall/band


def set_inputs(future: float = None, put_wall: float = None, call_wall: float = None,
               sigma1: float = None, sigma2: float = None, sigma3: float = None,
               put_vol: float = None, call_vol: float = None,
               as_of: str = None) -> dict:
    """Feed the options snapshot (any subset). Returns the current INPUTS."""
    if future is not None:
        INPUTS["future"] = float(future)
    if put_wall is not None:
        INPUTS["put_wall"] = float(put_wall)
    if call_wall is not None:
        INPUTS["call_wall"] = float(call_wall)
    for k, v in (("1", sigma1), ("2", sigma2), ("3", sigma3)):
        if v is not None:
            INPUTS["sigma"][k] = float(v)
    if put_vol is not None:
        INPUTS["put_vol"] = float(put_vol)
    if call_vol is not None:
        INPUTS["call_vol"] = float(call_vol)
    INPUTS["as_of"] = as_of or "operator-fed"
    return dict(INPUTS)


def configured() -> bool:
    return INPUTS.get("future") is not None


def expected_move() -> Optional[dict]:
    """The 1σ/2σ/3σ bands around the future, or None if not fed."""
    fut = INPUTS.get("future")
    if fut is None:
        return None
    sig = INPUTS["sigma"]
    bands = {}
    for k in ("1", "2", "3"):
        s = sig.get(k)
        if s is not None:
            bands[f"{k}sigma"] = {"up": round(fut + s, 2), "down": round(fut - s, 2),
                                  "half_width": s}
    return {"future": fut, "bands": bands}


def walls() -> dict:
    return {"put_wall": INPUTS.get("put_wall"), "call_wall": INPUTS.get("call_wall")}


def flow_bias() -> dict:
    """Put/call volume skew → the day's directional pressure."""
    pv, cv = INPUTS.get("put_vol"), INPUTS.get("call_vol")
    if pv is None or cv is None or (pv + cv) == 0:
        return {"skew": None, "bias": "neutral", "note": "no put/call volume fed"}
    ratio = round(pv / cv, 2) if cv else None
    # heavy PUT buying = downside hedging / bearish pressure; heavy CALL = bullish.
    bias = "bearish" if pv > cv * 1.15 else "bullish" if cv > pv * 1.15 else "balanced"
    return {"put_vol": pv, "call_vol": cv, "put_call_ratio": ratio, "bias": bias,
            "note": f"{'put' if bias=='bearish' else 'call' if bias=='bullish' else 'balanced'}-heavy flow"}


def _near(a: float, b: Optional[float], tol: float) -> bool:
    return b is not None and abs(a - b) <= tol


def confluence(side: str, level: float, tol: float = DEFAULT_TOL) -> dict:
    """Does an entry ``level`` align with the options positioning?

    SELL confirms at the call wall or the 2σ/3σ upside band (resistance) and opposes
    into the put wall (support). BUY is the mirror. Neutral when not fed."""
    if not configured():
        return {"status": "neutral", "reason": "options flow not fed"}
    w = walls()
    em = expected_move() or {"bands": {}}
    up2 = (em["bands"].get("2sigma") or {}).get("up")
    up3 = (em["bands"].get("3sigma") or {}).get("up")
    dn2 = (em["bands"].get("2sigma") or {}).get("down")
    dn3 = (em["bands"].get("3sigma") or {}).get("down")
    sell = side.lower() in ("short", "sell")

    reasons = []
    if sell:
        at_res = _near(level, w["call_wall"], tol) or _near(level, up2, tol) or _near(level, up3, tol)
        at_sup = _near(level, w["put_wall"], tol)
        if _near(level, w["call_wall"], tol):
            reasons.append("at the call wall (resistance)")
        if _near(level, up2, tol) or _near(level, up3, tol):
            reasons.append("at the 2σ/3σ expected-move top")
        status = "confirms" if at_res else "opposes" if at_sup else "neutral"
    else:
        at_sup = _near(level, w["put_wall"], tol) or _near(level, dn2, tol) or _near(level, dn3, tol)
        at_res = _near(level, w["call_wall"], tol)
        if _near(level, w["put_wall"], tol):
            reasons.append("at the put wall (support)")
        if _near(level, dn2, tol) or _near(level, dn3, tol):
            reasons.append("at the 2σ/3σ expected-move bottom")
        status = "confirms" if at_sup else "opposes" if at_res else "neutral"
    return {"status": status, "level": round(level, 2),
            "reason": "; ".join(reasons) if reasons else f"{level} not at an options level"}


def snapshot() -> dict:
    """Full options-flow read for the recon board / endpoint."""
    return {"configured": configured(), "as_of": INPUTS.get("as_of"),
            "walls": walls(), "expected_move": expected_move(), "flow": flow_bias()}


def format_options(side: Optional[str] = None, level: Optional[float] = None) -> Optional[str]:
    """Telegram line for the options-flow read, or None if not fed."""
    if not configured():
        return None
    w = walls()
    fb = flow_bias()
    line = (f"📊 *Options flow* (fut {INPUTS['future']}) — "
            f"put wall {w['put_wall']} / call wall {w['call_wall']}  ·  {fb['bias']}")
    if side and level is not None:
        c = confluence(side, level)
        line += f"\n   {side.upper()} @ {level}: {c['status']} ({c['reason']})"
    return line
