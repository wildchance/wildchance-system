"""Gold Q3/Q4 2026 macro context — levels, standing bias, and a confluence gate.

Pure, deterministic, stdlib-only. Encodes the standing macro read from the
*Gold Q3 Macro Analysis* (World Gold Council ETF flows, CFTC COT positioning,
Trading Economics forecasts) as of 2026-07-06, so every gold signal can be gated
by the higher-timeframe thesis:

  • base case is BULLISH — "accumulate on dips toward $4,000, stop below $3,900",
    grinding back toward $4,500–4,600 into Q4 as the Fed eventually blinks;
  • the edge that pays is BUYING THE DISCOUNT PULLBACK inside the accumulation
    band, not chasing strength in premium (the −1SD buy-zone long);
  • the thesis is INVALIDATED on a weekly break/close below $3,900.

The macro bias is the week's directional ANCHOR: it damps the mid-week profile
flip (a trade only fires when the weekly profile agrees with, or is neutral to,
this bias) and it is the confluence gate the signal engine reads.

Refresh SNAPSHOT when a new macro read lands; everything downstream is derived.
"""

from __future__ import annotations

from typing import Optional

# --- standing macro snapshot (update on a fresh macro read) -----------------
SNAPSHOT = {
    "as_of": "2026-07-06",
    "instrument": "XAU/USD",
    "bias": "long",                       # base-case direction into Q3/Q4
    "conviction": "base",                 # base | bull | bear scenario in force
    "spot_ref": 4175.0,
    # Buy-the-dip accumulation band and the hard invalidation beneath it.
    "accumulation_zone": [3900.0, 4200.0],
    "invalidation": 3900.0,               # weekly close below → thesis broken
    # Key structural levels (from the macro doc's "Key Levels to Watch").
    "levels": {
        "ath": 5608.35,                   # 2026-01-27 all-time high
        "resistance": [4260.0, 4500.0],   # Q3 target / psychological
        "pivot": 4175.0,                  # must hold for bulls
        "support": [4120.0, 4000.0],      # short-term / psychological
        "major_support": 3900.0,          # 200-week SMA zone = invalidation
    },
    # Upside objectives the swing runner targets on a confirmed accumulation long.
    "targets": [4260.0, 4500.0, 4553.0],  # Q3 / psych / 12-month consensus
    "scenarios": {
        "bull": {"prob": 0.35, "range": [4800.0, 5200.0]},
        "base": {"prob": 0.45, "range": [4200.0, 4600.0]},
        "bear": {"prob": 0.20, "range": [3600.0, 4000.0]},
    },
    # Positioning colour (COT / ETF) — justification only, not a gate.
    "positioning": {
        "cot_noncomm_net": 181339,        # 2026-06-23, rebuilding off 154,260 low
        "cot_note": "specs rebuilding off the May washout; room to add vs 251k peak",
        "etf_note": "flows easing selling; 200t+ still at a loss = overhead supply",
    },
}


def macro_read() -> dict:
    """Return the standing macro snapshot (a copy-safe view)."""
    return dict(SNAPSHOT)


def macro_bias() -> str:
    """The week's directional anchor: 'long' | 'short' | 'neutral'."""
    return SNAPSHOT.get("bias", "neutral")


def in_accumulation(price: Optional[float]) -> bool:
    """Is price inside the buy-the-dip accumulation band?"""
    if price is None:
        return False
    lo, hi = SNAPSHOT["accumulation_zone"]
    return lo <= price <= hi


def invalidated(price: Optional[float]) -> bool:
    """True once price is below the hard macro invalidation (thesis broken)."""
    return price is not None and price < SNAPSHOT["invalidation"]


def macro_confluence(side: str, price: Optional[float] = None) -> dict:
    """How a proposed trade sits against the standing macro thesis.

    status:  'confirms' | 'diverges' | 'neutral'   (side vs macro bias)
    plus location colour (in the accumulation band?) and invalidation state.
    """
    bias = macro_bias()
    want = "long" if side.lower() in ("long", "buy") else "short"
    if bias in (None, "neutral"):
        status = "neutral"
    else:
        status = "confirms" if bias == want else "diverges"

    inval = invalidated(price)
    in_band = in_accumulation(price)
    if inval:
        note = f"below ${SNAPSHOT['invalidation']:.0f} — macro thesis invalidated, stand aside"
    elif status == "diverges":
        note = f"fights the macro {bias} bias"
    elif want == "long" and in_band:
        note = "long into the accumulation band — macro-aligned dip buy"
    elif status == "confirms":
        note = f"aligned with the macro {bias} bias"
    else:
        note = "no macro objection"

    return {
        "status": status,
        "bias": bias,
        "in_accumulation": in_band,
        "invalidated": inval,
        "accumulation_zone": SNAPSHOT["accumulation_zone"],
        "invalidation": SNAPSHOT["invalidation"],
        "targets": SNAPSHOT["targets"],
        "note": note,
    }
