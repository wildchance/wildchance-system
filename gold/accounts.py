"""Five-account fleet + copy-trade fan-out (pure).

Links the five strategy accounts to ONE gold signal and sizes each to its own
balance/denomination — the same entry/stop/targets, five different lots. The five:

  acc1  cent flipper      500-pip, 10 lot-doubling runs        (gold.flip_ladders)
  acc2  prop 5k sim       6/12/18% challenge phases            (gold.risk_engine)
  acc3  middle compound   [500,500,1500]-pip cycles            (gold.flip_ladders)
  acc4  10x compound      4 runs, lot ×10 each, 1500-pip:
                          700 → 167,350 (0.01·0.10·1.00·10.00)  ← new
  acc5  full-trend layer  2500-pip trend with retracement layering (100k)  ← new

All copy-tradeable across cent / USD / KES / KWD — same structure, different unit.
`fleet_plan()` returns every account's growth ladder; `copy_fanout(signal, accounts)`
sizes one live signal to every linked account.
"""

from __future__ import annotations

from typing import List, Optional, Sequence

from gold.risk_engine import (GOLD_PIP, GOLD_USD_PER_POINT, size_for_risk,
                              lot_ladder, phase_plan, _round_lot, MIN_LOT)
from gold import flip_ladders as fl


def _run_gain(lot: float, pips: float) -> float:
    """USD gain of a ``pips``-pip run at ``lot`` (gold contract)."""
    return round(lot * (pips * GOLD_PIP) * GOLD_USD_PER_POINT, 2)


# --- acc4: stepped compounding (0.05 start, 2× then 10× the account) ---------

def compound_stepped(deposit: float = 750.0, lots=(0.05, 0.10, 1.0, 10.0),
                     pips: int = 1500, denom: str = "USD") -> dict:
    """4 × 1500-pip runs at the FIXED lot sequence 0.05 → 0.10 → 1.0 → 10.0.

    Run 1 doubles the stake, run 2 doubles again (build capital), then runs 3-4 step
    the lot to 1.0 and 10.0 for the 1000%-class compounding. Deposit ~750 (700-1000)
    → ~168,000 cumulative. Only the ENTRY lot was moved 0.01→0.05 to give the early
    runs room; the 0.10/1.0/10.0 tail is unchanged.
    """
    rows, balance = [], float(deposit)
    for i, lot in enumerate(lots, 1):
        gain = _run_gain(lot, pips)
        prev = balance
        balance = round(balance + gain, 2)
        rows.append({"run": i, "lot": round(lot, 2), "pips": pips, "gain": gain,
                     "target_pct": round(gain / prev * 100) if prev else None,
                     "balance": balance})
    return {"account": "acc4", "strategy": "compound_stepped", "denom": denom,
            "deposit": deposit, "runs": len(lots), "pip_target": pips,
            "start_lot": lots[0], "lots": list(lots), "final_balance": balance,
            "multiple": round(balance / deposit, 1) if deposit else None,
            "run_targets_pct": [r["target_pct"] for r in rows], "ladder": rows,
            "note": (f"{len(lots)} × {pips}-pip runs, lots {list(lots)}: "
                     f"{denom} {deposit:g} → {balance:,.0f}")}


# --- acc5: full-trend 2500-pip retracement layering -------------------------

def trend_layer_plan(anchor: float, direction: str = "long", range_pips: int = 2500,
                     cap_pips: int = 1450, layers: int = 6, base_lot: float = 0.02,
                     scale: float = 2.2, leverage: str = "1:2000/3000",
                     denom: str = "USD") -> dict:
    """Layer INTO a 2500-pip trend on the retracements, then RIDE to full TP.

    Entries scale-in over the FIRST ``cap_pips`` of the move (the handwritten ladder
    that stops ~3630/3632); once the cap is filled the position holds untouched to
    the full ``range_pips`` target. Deeper layers carry a bigger lot (high leverage
    ~1:2000/3000), so the average entry sits well inside the discount and the whole
    stack rides the last leg to target with the account grown in good risk flow.
    """
    long = direction.lower() in ("long", "buy")
    span = range_pips * GOLD_PIP                       # 2500 pips → $250 move
    cap = cap_pips * GOLD_PIP
    target = round(anchor + (span if long else -span), 2)
    step = cap / max(1, layers - 1)                    # layers fill only within the cap
    rows, lot, total_lot = [], base_lot, 0.0
    for i in range(1, layers + 1):
        entry = round(anchor - step * (i - 1) if long else anchor + step * (i - 1), 2)
        lot_i = round(lot, 2)
        total_lot = _round_lot(total_lot + lot_i)
        rows.append({"layer": i, "entry": entry, "lot": lot_i,
                     "pips_to_target": round(abs(target - entry) / GOLD_PIP)})
        lot = lot * scale
    cap_price = rows[-1]["entry"]
    avg_entry = round(sum(r["entry"] * r["lot"] for r in rows) / max(total_lot, 1e-9), 2)
    est_gain = _run_gain(total_lot, round(abs(target - avg_entry) / GOLD_PIP))
    return {"account": "acc5", "strategy": "trend_layer_2500", "denom": denom,
            "direction": "long" if long else "short", "anchor": anchor,
            "range_pips": range_pips, "cap_pips": cap_pips, "cap_price": cap_price,
            "target": target, "layers": layers, "leverage": leverage,
            "avg_entry": avg_entry, "total_lot": total_lot, "est_gain_usd": est_gain,
            "orders": rows,
            "note": f"{layers}-layer scale-in capped at {cap_price} (first {cap_pips} pips), "
                    f"then rides to target {target} (avg {avg_entry}, {total_lot} lot, {leverage})"}


# --- the fleet registry ------------------------------------------------------

FLEET = {
    "acc1": {"strategy": "cent_flipper", "desc": "500-pip · 10 lot-doubling runs",
             "default_deposit": 700},
    "acc2": {"strategy": "prop_5k_sim", "desc": "prop 5k challenge · 6/12/18% phases",
             "default_deposit": 5000},
    "acc3": {"strategy": "middle_compound", "desc": "[500,500,1500]-pip cycles",
             "default_deposit": 5000},
    "acc4": {"strategy": "compound_stepped", "desc": "4×1500-pip · 0.05 start · 2× then 10×",
             "default_deposit": 750},
    "acc5": {"strategy": "trend_layer_2500", "desc": "full-trend 2500-pip layering",
             "default_deposit": 100000},
}

DENOMINATIONS = ("cent", "USD", "KES", "KWD")


def account_plan(acc_id: str, deposit: Optional[float] = None, denom: str = "USD",
                 anchor: float = 4000.0) -> dict:
    """The growth/layer plan for one account."""
    meta = FLEET.get(acc_id)
    if not meta:
        return {"error": f"unknown account {acc_id}"}
    dep = deposit if deposit is not None else meta["default_deposit"]
    strat = meta["strategy"]
    if strat == "cent_flipper":
        plan = {"account": acc_id, **fl.cent_flipper(dep, 10, denom=denom)}
    elif strat == "prop_5k_sim":
        plan = {"account": acc_id, "denom": denom, "deposit": dep,
                "phases": phase_plan(dep), "lots": lot_ladder(dep)}
    elif strat == "middle_compound":
        plan = {"account": acc_id, **fl.middle_ladder(dep, cycles=4, denom=denom)}
    elif strat == "compound_stepped":
        plan = compound_stepped(dep, denom=denom)
    elif strat == "trend_layer_2500":
        plan = trend_layer_plan(anchor, denom=denom)
    else:
        return {"error": f"no plan for {strat}"}
    plan["strategy"] = strat          # normalise across all account types
    return plan


def fleet_plan(denom: str = "USD", anchor: float = 4000.0) -> dict:
    """Every account's plan (defaults), for the copy-trade dashboard."""
    return {"denom": denom,
            "accounts": {aid: account_plan(aid, denom=denom, anchor=anchor) for aid in FLEET}}


def copy_fanout(signal: dict, accounts: Sequence[dict]) -> dict:
    """Fan ONE gold signal out to N linked accounts, sized per account.

    ``signal`` = {signal/side, entry, stop, targets}. ``accounts`` =
    [{id, balance, denom, risk_pct}]. Same entry/stop/targets; per-account lot from
    its balance × risk (min 0.01, capped by the prop max_lot). This is the copy-
    trade mirror — one master signal, five (or N) sized children."""
    side = signal.get("side") or ("long" if signal.get("signal") == "LONG" else "short")
    entry, stop = signal.get("entry"), signal.get("stop")
    if entry is None or stop is None:
        return {"ok": False, "reason": "signal missing entry/stop"}
    dist = abs(entry - stop)
    out = []
    for a in accounts:
        bal = float(a.get("balance") or 0)
        risk_pct = float(a.get("risk_pct") or 1.0) / 100.0
        risk_usd = round(bal * risk_pct, 2)
        want = size_for_risk(entry, stop, risk_usd)
        lot = max(MIN_LOT, min(want, lot_ladder(bal)["max_lot"]))
        out.append({"account": a.get("id"), "denom": a.get("denom", "USD"),
                    "balance": bal, "side": side, "entry": round(entry, 2),
                    "stop": round(stop, 2), "lot": lot,
                    "risk_usd": round(lot * dist * GOLD_USD_PER_POINT, 2),
                    "strategy": FLEET.get(a.get("id"), {}).get("strategy")})
    return {"ok": True, "side": side, "entry": round(entry, 2), "stop": round(stop, 2),
            "targets": signal.get("targets", []), "fanout": out,
            "note": f"copied to {len(out)} accounts"}
