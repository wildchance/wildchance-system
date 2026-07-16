"""AMD hourly-triad — range → sweep (manipulation) → reaction (distribution).

The intraday playbook: for a trigger hour H (UTC), the H candle sets the RANGE
(high/low). H+1 is the MANIPULATION — which side of the range gets swept. H+2 is
the DISTRIBUTION/reaction: if price sweeps ONE side then CLOSES back inside the
range, that sweep was a liquidity grab → fade it (turtle-soup reversal). Entry at
the H+2 reaction, stop beyond the sweep extreme, target the opposite side of the
range (extendable to a pre-London CBDR level / held toward 00:00).

Trigger hours (UTC): 14 (the 14:00 day-open triad 14/15/16), 7 (07/08/09),
0 (00/01/02) — the AMD triads you run.

Bars are dicts ``{date, hour, open, high, low, close}`` in UTC — the shape
``services.ohlc_service.fetch_hourly_raw`` returns.

  TRIGGERS                       the trigger hours (14, 7, 0)
  classify(range_bar, manip_bar) sweep outcome: high / low / both / none
  triad_signal(r, m, x, …)       the fade-the-sweep setup, or NONE
"""

from __future__ import annotations

from typing import Optional

TRIGGERS = (14, 7, 0)


def classify(range_bar: dict, manip_bar: dict) -> str:
    """Which side of the range the manipulation candle swept.

    1 of 4 probabilities: ``sweep_high`` (took buy-side liquidity above),
    ``sweep_low`` (sell-side below), ``sweep_both`` (expansion / indecision),
    ``sweep_none`` (inside — accumulation continues).
    """
    rh, rl = range_bar["high"], range_bar["low"]
    sh = manip_bar["high"] > rh
    sl = manip_bar["low"] < rl
    if sh and sl:
        return "sweep_both"
    if sh:
        return "sweep_high"
    if sl:
        return "sweep_low"
    return "sweep_none"


def triad_signal(range_bar: dict, manip_bar: dict, react_bar: dict,
                 buffer: float = 0.0, target_pips: Optional[float] = None,
                 pip: float = 0.1) -> dict:
    """Fade a one-sided sweep that closed back inside the range → LONG/SHORT/NONE.

    ``sweep_high`` + react closes back BELOW the range high → the high was a
    liquidity grab → SHORT, stop above the sweep. ``sweep_low`` + react closes
    back ABOVE the range low → LONG, mirror. ``sweep_both``/``sweep_none`` → NONE.

    Target: by default the opposite side of the range (the tight ~1:1 read). Pass
    ``target_pips`` to project a fixed distance from entry instead (e.g. 250 or
    500 pips = 25.0 / 50.0 at ``pip=0.1`` for gold) — the "hold-to-session-close"
    playbook where the reaction only confirms the reversal and you carry the trade
    toward the next session boundary for a larger, higher-R target.
    """
    rh, rl = range_bar["high"], range_bar["low"]
    outcome = classify(range_bar, manip_bar)
    entry = round(react_bar["close"], 3)
    base = {"outcome": outcome, "trigger_hour": range_bar.get("hour"),
            "date": range_bar.get("date"), "range_high": rh, "range_low": rl}
    reach = (target_pips * pip) if target_pips else None

    if outcome == "sweep_high" and react_bar["close"] < rh:
        target = round(entry - reach, 3) if reach else round(rl, 3)
        return {**base, "signal": "SHORT", "side": "short", "entry": entry,
                "stop": round(manip_bar["high"] + buffer, 3), "target": target,
                "reason": "swept the range HIGH (buy-side liquidity), closed back in — fade SHORT"}
    if outcome == "sweep_low" and react_bar["close"] > rl:
        target = round(entry + reach, 3) if reach else round(rh, 3)
        return {**base, "signal": "LONG", "side": "long", "entry": entry,
                "stop": round(manip_bar["low"] - buffer, 3), "target": target,
                "reason": "swept the range LOW (sell-side liquidity), closed back in — fade LONG"}
    return {**base, "signal": "NONE",
            "reason": (f"{outcome} — no clean sweep-reversal "
                       "(need a one-sided sweep that closes back inside the range)")}
