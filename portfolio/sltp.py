"""Stop-Loss & Take-Profit Architecture — the institutional ATR model.

One risk unit is 1 ATR; targets ladder out at fixed multiples of it:

    SL  = 1 ATR
    TP1 = 2 ATR   (1:2)
    TP2 = 4 ATR   (1:4)
    TP3 = 8 ATR   (1:8)

The stop sits 1 ATR against the trade and each target sits N ATR in favour, so
the risk:reward of TP1/TP2/TP3 is exactly 2 / 4 / 8 to 1. Pip distances are
reported using each instrument's pip size (JPY crosses & metals use 0.01,
most FX uses 0.0001).

Worked example (EURJPY, the framework's): entry 184.97, stop 183.80 -> the
stop distance is 1.17 (117 pips), so ATR = 1.17 and the targets fall at
187.31 / 189.65 / 194.33 — the framework's 187.30 / 189.60 / 194.20 rounded.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict, List

# --- FROZEN ATR multiples ---------------------------------------------------
SL_ATR = 1.0
TP_ATR = (2.0, 4.0, 8.0)     # TP1, TP2, TP3

# Pip size by instrument family. JPY crosses and metals quote to 0.01.
_PIP_DEFAULT = 0.0001
_PIP_OVERRIDES = {0.01: ("JPY", "XAU", "XAG", "XPT", "XPD")}


def pip_size(symbol: str) -> float:
    """Pip size for an instrument symbol (e.g. EURJPY -> 0.01, EURUSD -> 0.0001)."""
    s = symbol.upper()
    for size, families in _PIP_OVERRIDES.items():
        if any(f in s for f in families):
            return size
    return _PIP_DEFAULT


@dataclass
class Target:
    level: int           # 1, 2, 3
    price: float
    atr_multiple: float
    pips: float
    risk_reward: float   # e.g. 2.0 for 1:2

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TradePlan:
    symbol: str
    bias: str            # "LONG" | "SHORT"
    entry: float
    atr: float
    stop: float
    risk_pips: float
    pip: float
    targets: List[Target]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["targets"] = [t.to_dict() for t in self.targets]
        return d


def _norm_bias(bias: str) -> str:
    b = bias.strip().upper()
    if b in ("LONG", "BUY"):
        return "LONG"
    if b in ("SHORT", "SELL"):
        return "SHORT"
    raise ValueError(f"bias must be LONG/SHORT (or BUY/SELL), got {bias!r}")


def build_plan(symbol: str, bias: str, entry: float, atr: float) -> TradePlan:
    """Build the full SL/TP ladder from an entry and an ATR value."""
    if atr <= 0:
        raise ValueError("atr must be positive")
    b = _norm_bias(bias)
    direction = 1.0 if b == "LONG" else -1.0
    pip = pip_size(symbol)

    stop = entry - direction * SL_ATR * atr
    risk_pips = round(abs(entry - stop) / pip, 1)

    targets: List[Target] = []
    for i, mult in enumerate(TP_ATR, start=1):
        price = entry + direction * mult * atr
        targets.append(
            Target(
                level=i,
                price=round(price, 5),
                atr_multiple=mult,
                pips=round(abs(price - entry) / pip, 1),
                risk_reward=mult / SL_ATR,
            )
        )

    return TradePlan(
        symbol=symbol.upper(),
        bias=b,
        entry=round(entry, 5),
        atr=atr,
        stop=round(stop, 5),
        risk_pips=risk_pips,
        pip=pip,
        targets=targets,
    )


def plan_from_stop(symbol: str, bias: str, entry: float, stop: float) -> TradePlan:
    """Build the ladder when you already have the stop price — the stop
    distance IS one ATR in this model, so we back out ATR = |entry - stop|."""
    atr = abs(entry - stop)
    if atr == 0:
        raise ValueError("entry and stop must differ")
    return build_plan(symbol, bias, entry, atr)
