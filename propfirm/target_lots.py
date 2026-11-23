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
# BTC follows gold's structure: a USD pip and a per-lot contract (broker spec; most
# prop CFDs: 1 lot = 1 BTC, so $1 move = $1 per lot).
from decouple import config as _config
BTC_PIP = float(_config("BTC_PIP", default="1.0"))
# Index CFDs: pip = 0.1 point (the owner's flash cards: US30 +306.43 pts = +3,064.3 pips),
# 1 lot = INDEX_USD_PER_POINT dollars per full point. Stock CFDs: pip = $0.01,
# 1 lot = STOCK_LOT_SHARES shares. Set both to your broker's contract spec.
INDEX_USD_PER_POINT = float(_config("INDEX_USD_PER_POINT", default="1"))
STOCK_LOT_SHARES = float(_config("STOCK_LOT_SHARES", default="1"))
INDICES = ("NAS100", "US30", "SPX500", "US500", "SPX", "GER40", "DE40", "UK100", "JP225", "US2000", "DXY")


def is_index(symbol: str) -> bool:
    return symbol.upper().replace("/", "").startswith(INDICES)


def is_stock(symbol: str) -> bool:
    s = symbol.upper().replace("/", "")
    return s.isalpha() and len(s) <= 5 and not is_index(s)
BTC_CONTRACT_SIZE = float(_config("BTC_CONTRACT_SIZE", default="1"))


def pip_value_per_lot(symbol: str, price: Optional[float] = None) -> Optional[float]:
    """USD value of 1 pip for 1.00 standard lot. USD-quoted pairs = $10; USDxxx needs
    the price (10 / price × … ); crosses need a conversion → None (pass it explicitly)."""
    if is_index(symbol):
        return INDEX_USD_PER_POINT * 0.1                   # pip = 0.1 point
    if is_stock(symbol):
        return STOCK_LOT_SHARES * 0.01                     # pip = $0.01 per share
    s = symbol.upper().replace("/", "")[:6]
    if s.startswith(("BTC", "XBT")):                       # 1 lot = N coins, pip = $1
        return BTC_CONTRACT_SIZE * BTC_PIP
    if s.endswith("USD"):
        return 10.0
    if s.startswith("USD") and price:
        pip = 0.01 if s.endswith("JPY") else 0.0001
        return round(100000 * pip / price, 4)
    return None


def pip_size(symbol: str) -> Optional[float]:
    s = symbol.upper().replace("/", "")
    if is_index(s):
        return 0.1
    if is_stock(s):
        return 0.01
    if s.startswith(("XAU", "GOLD")):
        return 0.1
    if s.startswith(("BTC", "XBT")):
        return BTC_PIP
    if len(s) >= 6 and s[3:6] == "JPY":
        return 0.01
    if len(s) >= 6 and s[:6].isalpha():
        return 0.0001
    return None


def lot_for(balance: float, tp_pips: float, sl_pips: float, pv: float,
            target_usd: Optional[float] = None, max_risk_pct: float = 0.5,
            lot_step: float = 0.01, min_lot: float = 0.01) -> Dict:
    """One rung: lot so ``tp_pips`` pays the target, capped so ``sl_pips`` risks ≤ cap."""
    target = target_usd if target_usd else balance * TARGET_PCT
    cap = math.floor(balance * max_risk_pct / 100 / (sl_pips * pv) / lot_step + 1e-9) * lot_step
    want = max(min_lot, math.ceil(target / (tp_pips * pv) / lot_step - 1e-9) * lot_step)
    use = round(min(want, cap), 2) if cap >= min_lot else 0.0
    return {"lot": use, "profit": round(use * tp_pips * pv, 2), "risk": round(use * sl_pips * pv, 2),
            "capped": want > cap}


def card_lines(symbol: str, entry: float, stop: float, targets: List[Dict],
               balances: Optional[List[float]] = None, max_risk_pct: float = 0.5) -> List[str]:
    """Telegram lines: per account size, the lot for each target (owner's target method).
    targets: [{"name": "TP1", "price": 1.0925}]. Empty list when the symbol isn't FX/gold."""
    ps = pip_size(symbol)
    pv = pip_value_per_lot(symbol, entry)
    if not ps or not pv or not entry or not stop:
        return []
    sl_pips = abs(entry - stop) / ps
    if sl_pips <= 0:
        return []
    bals = balances or card_balances()
    out = [f"*Lots* (target ≈ {TARGET_PCT * 100:.2f}%/trade, SL {sl_pips:.1f} pips, risk cap {max_risk_pct}%):"]
    for b in bals:
        parts = []
        for t in targets:
            tp_pips = abs(float(t["price"]) - entry) / ps
            if tp_pips <= 0:
                continue
            r = lot_for(b, tp_pips, sl_pips, pv, max_risk_pct=max_risk_pct)
            flag = "*" if r["capped"] else ""
            parts.append(f"{t.get('name', 'TP')} {r['lot']:.2f}{flag} (+${r['profit']:g}/−${r['risk']:g})")
        min_risk = round(0.01 * sl_pips * pv, 2)          # 0.01 lot at this stop
        if parts and min_risk > b * max_risk_pct / 100:   # even the minimum lot over-risks
            out.append(f"`{b / 1000:g}k`: ⛔ skip — stop too wide (0.01 lot risks ${min_risk:g} = "
                       f"{100 * min_risk / b:.2f}% > {max_risk_pct}%)")
        elif parts:
            out.append(f"`{b / 1000:g}k`: " + " · ".join(parts))
    if any("*" in l for l in out[1:]):
        out.append("_* capped by the risk limit — pays less than the target_")
    return out if len(out) > 1 else []


def card_balances() -> List[float]:
    from decouple import config
    raw = config("LOT_CARD_BALANCES", default="2500,5000")
    try:
        return [float(x) for x in str(raw).split(",") if x.strip()] or [2500.0]
    except ValueError:
        return [2500.0]


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
