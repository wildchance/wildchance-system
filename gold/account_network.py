"""Copy-trade account NETWORK — upscale ladder, global prop firms, cross-border
currency tiers, and the structured daily/weekly/monthly percentage grid (pure).

Turns the 5-account fleet into a scalable network product:

  • UPSCALE LADDER — every account graduates in ×10 rungs (100 → 100,000,000) once
    it clears a minimum number of copy trades per rung (default 10). The progression
    the whole network climbs.
  • PROP FIRMS — the 5k-style prop copy-trader has its OWN logic: per-firm phase
    targets, drawdown limits, and profit splits (FundingPips / FTMO / The5ers / …)
    so prop accounts are sized and gated differently from personal accounts.
  • CURRENCY TIERS — each USD size threshold expressed in global deposit currencies
    (KES / KWD / NGN / INR / ZAR / EUR / GBP / AED / …) for cross-border reach.
  • NETWORK STRUCTURE — structured %-return targets per account-size band, on a
    daily / weekly / monthly cadence (smaller = more aggressive, larger = conserved).

Pure + deterministic. FX rates are approximate and operator-updatable via set_fx().
"""

from __future__ import annotations

from typing import List, Optional, Sequence

# --- upscale ladder ----------------------------------------------------------
UPSCALE_RUNGS = [100, 1_000, 10_000, 100_000, 1_000_000, 10_000_000, 100_000_000]
MIN_COPIES_PER_RUNG = 10


def upscale_ladder(base: float = 100.0, min_copies: int = MIN_COPIES_PER_RUNG,
                   denom: str = "USD") -> dict:
    """The ×10 upscale progression from ``base`` to the network ceiling. Each rung
    needs ``min_copies`` cleared copy trades to graduate to the next."""
    rungs, cum = [], 0
    live = [r for r in UPSCALE_RUNGS if r >= base]
    for i, size in enumerate(live):
        nxt = live[i + 1] if i + 1 < len(live) else None
        cum += min_copies if nxt else 0
        rungs.append({
            "rung": i + 1, "size": size, "next": nxt, "multiple": "×10" if nxt else "ceiling",
            "min_copies_to_graduate": min_copies if nxt else None,
            "cumulative_copies": cum,
            "note": (f"{min_copies} copy trades → graduate {size:,.0f} to {nxt:,.0f}"
                     if nxt else f"network ceiling {size:,.0f}"),
        })
    return {"base": base, "denom": denom, "min_copies_per_rung": min_copies,
            "ceiling": UPSCALE_RUNGS[-1], "rungs": rungs,
            "note": f"{len(rungs)} rungs {base:,.0f}→{UPSCALE_RUNGS[-1]:,.0f}, "
                    f"{min_copies} copies each"}


# --- global prop firms -------------------------------------------------------
# Each firm: challenge sizes, phase profit targets, drawdown caps, funded split.
PROP_FIRMS = {
    "fundingpips": {"name": "FundingPips", "phases": 2, "sizes": [5_000, 10_000, 25_000, 50_000, 100_000, 200_000],
                    "phase_targets": [0.08, 0.05], "max_daily_dd": 0.05, "max_dd": 0.10, "split": 0.80},
    "ftmo":        {"name": "FTMO", "phases": 2, "sizes": [10_000, 25_000, 50_000, 100_000, 200_000],
                    "phase_targets": [0.10, 0.05], "max_daily_dd": 0.05, "max_dd": 0.10, "split": 0.80},
    "the5ers":     {"name": "The5ers", "phases": 2, "sizes": [5_000, 20_000, 60_000, 100_000],
                    "phase_targets": [0.08, 0.05], "max_daily_dd": 0.05, "max_dd": 0.10, "split": 0.80},
    "myfundedfx":  {"name": "MyFundedFX", "phases": 1, "sizes": [5_000, 10_000, 25_000, 50_000, 100_000],
                    "phase_targets": [0.10], "max_daily_dd": 0.05, "max_dd": 0.12, "split": 0.85},
}


def prop_plan(firm: str, size: float, risk_pct: float = 1.0) -> dict:
    """Phase targets / drawdown limits / funded split + per-phase copy sizing for a
    prop account. The prop copy-trader is gated differently from personal accounts."""
    meta = PROP_FIRMS.get(firm.lower())
    if not meta:
        return {"error": f"unknown prop firm {firm}", "known": list(PROP_FIRMS)}
    phases = []
    for i, tgt in enumerate(meta["phase_targets"], start=1):
        phases.append({
            "phase": i, "profit_target_pct": tgt,
            "profit_target": round(size * tgt, 2),
            "max_daily_loss": round(size * meta["max_daily_dd"], 2),
            "max_loss": round(size * meta["max_dd"], 2),
            "risk_per_trade": round(size * risk_pct / 100.0, 2),
            "note": f"Phase {i}: make {tgt:.0%} (${size*tgt:,.0f}) within DD limits",
        })
    return {
        "firm": meta["name"], "size": size, "phases": meta["phases"],
        "phase_plan": phases, "funded_split": meta["split"],
        "max_daily_dd_pct": meta["max_daily_dd"], "max_dd_pct": meta["max_dd"],
        "funded_note": f"once funded, keep {meta['split']:.0%} of profits; "
                       f"risk {risk_pct:.1f}%/trade inside a {meta['max_dd']:.0%} total-DD cap",
    }


def prop_firms() -> dict:
    return {"firms": {k: {"name": v["name"], "sizes": v["sizes"],
                          "phases": v["phases"], "split": v["split"]}
                      for k, v in PROP_FIRMS.items()}}


# --- cross-border currency tiers ---------------------------------------------
# Approximate units per 1 USD (operator-updatable). 'cent' = MT5 cent account.
FX_RATES = {
    "USD": 1.0, "cent": 100.0, "EUR": 0.92, "GBP": 0.79, "AED": 3.67,
    "KES": 129.0, "KWD": 0.31, "NGN": 1600.0, "INR": 83.0, "ZAR": 18.5,
    "GHS": 15.0, "TZS": 2600.0, "UGX": 3800.0, "ZMW": 27.0,
}


def set_fx(rates: dict) -> dict:
    """Operator FX update (per 1 USD). Returns the merged table."""
    for k, v in (rates or {}).items():
        try:
            FX_RATES[k] = float(v)
        except (TypeError, ValueError):
            pass
    return dict(FX_RATES)


def currency_deposits(target_usd: float, currencies: Optional[Sequence[str]] = None) -> dict:
    """A USD account-size threshold expressed as local deposit amounts."""
    ccys = currencies or list(FX_RATES)
    out = []
    for c in ccys:
        rate = FX_RATES.get(c)
        if rate is None:
            continue
        out.append({"currency": c, "rate_per_usd": rate,
                    "deposit": round(target_usd * rate, 2)})
    return {"target_usd": target_usd, "deposits": out}


# --- structured D/W/M percentage grid ----------------------------------------
# Smaller accounts run more aggressive %; larger accounts protect capital.
SIZE_BANDS = [
    {"band": "micro",         "min": 100,        "max": 1_000,      "daily": 0.05,  "weekly": 0.20,  "monthly": 0.60},
    {"band": "small",         "min": 1_000,      "max": 10_000,     "daily": 0.03,  "weekly": 0.12,  "monthly": 0.40},
    {"band": "mid",           "min": 10_000,     "max": 100_000,    "daily": 0.02,  "weekly": 0.08,  "monthly": 0.25},
    {"band": "large",         "min": 100_000,    "max": 1_000_000,  "daily": 0.01,  "weekly": 0.05,  "monthly": 0.15},
    {"band": "institutional", "min": 1_000_000,  "max": 100_000_000,"daily": 0.005, "weekly": 0.025, "monthly": 0.08},
]


def band_for(size: float) -> Optional[dict]:
    for b in SIZE_BANDS:
        if b["min"] <= size < b["max"]:
            return b
    return SIZE_BANDS[-1] if size >= SIZE_BANDS[-1]["min"] else None


def structured_targets(size: float) -> dict:
    """The daily/weekly/monthly %-and-$ targets for an account of ``size``."""
    b = band_for(size)
    if not b:
        return {"error": f"size {size} below the micro floor"}
    return {"size": size, "band": b["band"],
            "daily": {"pct": b["daily"], "usd": round(size * b["daily"], 2)},
            "weekly": {"pct": b["weekly"], "usd": round(size * b["weekly"], 2)},
            "monthly": {"pct": b["monthly"], "usd": round(size * b["monthly"], 2)},
            "note": f"{b['band']} band → {b['daily']:.1%} daily / {b['weekly']:.1%} weekly "
                    f"/ {b['monthly']:.1%} monthly"}


def network_structure() -> dict:
    """The full network grid — every size band's D/W/M %-structure + upscale ladder."""
    return {"bands": SIZE_BANDS,
            "upscale": upscale_ladder(),
            "note": "structured percentages tighten as size grows — micro flips "
                    "aggressively, institutional protects capital"}


def network_report(base: float = 100.0, prop_firm: str = "fundingpips",
                   prop_size: float = 5_000.0,
                   sample_currencies: Sequence[str] = ("USD", "KES", "KWD", "NGN", "EUR")) -> dict:
    """One structured view of the copy-trade network: the upscale ladder, the D/W/M
    percentage bands, a prop-firm plan, and cross-border deposit tiers."""
    ladder = upscale_ladder(base)
    band_targets = [structured_targets(r["size"]) for r in ladder["rungs"]]
    return {
        "upscale_ladder": ladder,
        "structured_targets": band_targets,
        "prop": prop_plan(prop_firm, prop_size),
        "currency_tiers": [currency_deposits(r["size"], sample_currencies)
                           for r in ladder["rungs"][:4]],
        "note": "copy-trade web network: ×10 upscale rungs, D/W/M %-structure per "
                "band, prop accounts on their own phase logic, multi-currency deposits",
    }
