"""Target-based lot ladder for currencies (the owner's backtested sheet).

FX pips pay little at small size, so instead of "risk X %" the lot is chosen so the
chosen take-profit rung pays the daily/weekly TARGET, with a fixed 100-point
(10-pip) stop. Sheet (EURUSD, 0.01 lot: SL −$1.00 / 100 pts):

  TP rung (R = USD at 0.01)   1.3   1.95  2.6   3.25  3.9   4.55  5.2   (13 … 52 pips)
  2.5k  target ≈ $13          0.10  0.07  0.05  0.04  0.04  0.03  0.03
  5k    target ≈ $25          0.20  0.14  0.10  0.08  0.07  0.06  0.05
  scaling: 25k ×10 · 250k ×100 · 50k ×20 · 500k ×200 (of the 2.5k / 5k rows)

lot = ceil(target_usd / (tp_pips × pip_value_per_lot)) to the lot step — rounded UP so
the rung actually reaches the target (matches the sheet) — then CAPPED so the stop
never risks more than ``max_risk_pct`` of the account (prop-firm guard, default 0.5 %).
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional

RUNGS_R = (1.3, 1.95, 2.6, 3.25, 3.9, 4.55, 5.2)     # multiples of 0.65R
TARGET_PCT = 0.52 / 100                                # $13 on 2.5k (sheet); 5k ≈ $25-26


def pip_value_per_lot(symbol: str, price: Optional[float] = None) -> Optional[float]:
    """USD value of 1 pip for 1.00 standard lot. USD-quoted pairs = $10; USDxxx needs
    the price (10 / price × … ); crosses need a conversion → None (pass it explicitly)."""
    s = symbol.upper().replace("/", "")[:6]
    if s.endswith("USD"):
        return 10.0
    if s.startswith("USD") and price:
        pip = 0.01 if s.endswith("JPY") else 0.0001
        return round(100000 * pip / price, 4)
    return None


def ladder(balance: float, symbol: str = "EURUSD", sl_pips: float = 10.0,
           target_usd: Optional[float] = None, price: Optional[float] = None,
           pip_value: Optional[float] = None, max_risk_pct: float = 0.5,
           lot_step: float = 0.01, min_lot: float = 0.01) -> Dict:
    pv = pip_value or pip_value_per_lot(symbol, price)
    if not pv:
        return {"error": f"pip value for {symbol} unknown — pass pip_value (USD per pip per lot)"}
    target = round(target_usd if target_usd else balance * TARGET_PCT, 2)
    risk_cap_usd = balance * max_risk_pct / 100
    cap_lot = math.floor(risk_cap_usd / (sl_pips * pv) / lot_step + 1e-9) * lot_step
    rows: List[Dict] = []
    for r in RUNGS_R:
        tp_pips = round(sl_pips * r, 2)
        raw = target / (tp_pips * pv)
        lot = max(min_lot, math.ceil(raw / lot_step - 1e-9) * lot_step)
        capped = lot > cap_lot
        use = round(min(lot, cap_lot), 2) if cap_lot >= min_lot else 0.0
        rows.append({"tp_r": r, "tp_pips": tp_pips, "lot": use,
                     "profit_at_tp": round(use * tp_pips * pv, 2),
                     "risk_at_sl": round(use * sl_pips * pv, 2),
                     "risk_pct": round(100 * use * sl_pips * pv / balance, 3),
                     "capped_by_risk": capped})
    return {"balance": balance, "symbol": symbol.upper(), "sl_pips": sl_pips,
            "pip_value_per_lot": pv, "target_usd": target,
            "max_risk_pct": max_risk_pct, "max_lot_by_risk": round(cap_lot, 2),
            "rungs": rows,
            "note": ("lot rounded UP so the rung pays the target, then capped so the 10-pip "
                     "stop risks ≤ max_risk_pct; a capped rung pays less than the target")}
