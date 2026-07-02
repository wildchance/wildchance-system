"""Trade-setup zone builder — Entry / Stop / TP1·TP2·TP3 from CBDR SD extremes.

Pure and deterministic. Turns a CBDR box (any window — Asian, London, the
13:00/01:00 reference, etc.) plus a side and a mode into a full trade plan whose
take-profits sit on the box's standard-deviation projections (now incl. the
extreme 2.5/4 SDs) and whose stop sits a half-SD beyond the protective edge.

Two modes:
  • "continuation" — price breaks the range and runs WITH the move; TPs are the
    SDs further in the trade direction, stop behind the box.
  • "reversal"     — price is fading an EXTREME SD (2.5/4) back toward the mean;
    TPs step back toward mid / the opposite SDs, stop just beyond the extreme.

R multiples are computed for every target so you can rank by reward:risk. Pairs
with the candle-range / CRT trigger: crt_to_setup() maps a 4-hour 05:00/17:00
continuation-or-reversal read into the (side, mode) this builder expects.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from cbdr.engine import CBDR
from indicators.atr import atr_targets

# Which SD multiples become TP1/TP2/TP3 in each mode.
CONT_TP_SDS = (1, 2, 4)        # run toward the far extremes
REV_TP_SDS = (1, 2, 2.5)       # step back toward the mean (measured from entry)
SL_BUFFER_SD = 0.5             # stop sits this many SDs beyond the protective edge


def _r_multiple(entry: float, stop: float, target: float) -> Optional[float]:
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    return round(abs(target - entry) / risk, 2)


def build_setup(box: CBDR, side: str, entry: float,
                mode: str = "continuation",
                atr: Optional[float] = None, atr_mult: float = 1.5,
                atr_floor_stop: bool = True,
                spike: Optional[bool] = None) -> dict:
    """Build Entry/SL/TP1-3 for a side ('long'/'short') against a CBDR box.

    ATR overlay (optional): pass ``atr`` to floor the protective stop at
    ``atr_mult * ATR`` — a narrow box can't then produce a sub-volatility stop
    that noise wicks out — and to add ATR scale-out targets. ``spike`` (from
    indicators.atr.is_spike on the entry bar) flags a >k*ATR news/expansion bar.
    """
    side = side.lower()
    rng = box.range
    if rng <= 0:
        raise ValueError("CBDR range must be positive")

    if mode == "reversal":
        # Fading an extreme back toward the mean. The stop sits just BEYOND the
        # entry (the extreme you faded); TPs step back inward toward mid/opposite.
        if side == "long":      # bought a low extreme, targeting back up
            stop = entry - SL_BUFFER_SD * rng
            tps = [box.mid, box.high, box.levels.get("+1SD", box.high + rng)]
        else:                    # shorted a high extreme, targeting back down
            stop = entry + SL_BUFFER_SD * rng
            tps = [box.mid, box.low, box.levels.get("-1SD", box.low - rng)]
    else:                        # continuation
        if side == "long":
            stop = box.low - SL_BUFFER_SD * rng
            tps = [box.levels.get(f"+{n}SD", box.high + n * rng) for n in CONT_TP_SDS]
        else:
            stop = box.high + SL_BUFFER_SD * rng
            tps = [box.levels.get(f"-{n}SD", box.low - n * rng) for n in CONT_TP_SDS]

    # ATR overlay — floor the stop at atr_mult*ATR so no market gets a
    # sub-volatility stop, and expose ATR scale-out targets. Applied BEFORE the
    # R-multiples so they reflect the (possibly widened) protective stop.
    atr_overlay = None
    if atr and atr > 0:
        atr_dist = atr_mult * atr
        floored = False
        if atr_floor_stop and abs(entry - stop) < atr_dist:
            stop = entry - atr_dist if side == "long" else entry + atr_dist
            floored = True
        atr_overlay = {
            "atr": round(atr, 6),
            "atr_mult": atr_mult,
            "stop_distance": round(atr_dist, 6),
            "stop_floored_to_atr": floored,
            "targets": atr_targets(entry, atr, "BUY" if side == "long" else "SELL"),
        }

    targets = []
    for i, tp in enumerate(tps, start=1):
        targets.append({
            "name": f"TP{i}",
            "price": round(tp, 5),
            "r": _r_multiple(entry, stop, tp),
        })

    plan = {
        "side": side,
        "mode": mode,
        "entry": round(entry, 5),
        "stop_loss": round(stop, 5),
        "risk_per_unit": round(abs(entry - stop), 5),
        "targets": targets,             # TP1, TP2, TP3 with R multiples
        "box": {"high": box.high, "mid": box.mid, "low": box.low, "range": rng},
    }
    if atr_overlay is not None:
        plan["atr"] = atr_overlay
    if spike is not None:
        plan["spike"] = spike
        if spike:
            plan["entry_warning"] = (
                "⚠️ entry bar is a >2×ATR expansion (news/spike) — avoid chasing; "
                "wait for a pullback or reduce size."
            )
    return plan


def crt_to_setup(candle_dir: str, state: str) -> Optional[Dict[str, str]]:
    """Map a CRT 4h (05:00/17:00) candle read into (side, mode) for build_setup.

    candle_dir: 'bullish' | 'bearish'  (the 4h reference candle body direction)
    state:      'continuation' | 'reversal'  (from candlerange.classify)
    """
    if state not in ("continuation", "reversal"):
        return None
    if state == "continuation":
        side = "long" if candle_dir == "bullish" else "short"
    else:  # reversal — trade against the candle's direction
        side = "short" if candle_dir == "bullish" else "long"
    return {"side": side, "mode": state}
