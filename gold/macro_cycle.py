"""Macro-cycle regime stack for gold — BIS + FRED + WGC + CFTC COT (pure).

The institutional reference framework, hardwired: a higher-timeframe regime filter
(BIS real broad dollar, FRED real rates / Fed cycle, WGC central-bank demand) that
must be read BEFORE lower-timeframe BMS/SMS entries, plus a lower-timeframe entry
filter (COT positioning, WGC ETF flows, dollar funding). It fuses the encoded
reads from gold.macro (Q3 levels), gold.dxy (dollar structure) and
gold.purchases_audit (COT/CB/ETF) into ONE regime verdict + gold bias.

SOURCES documents each feed (what / access / frequency / lag / key series) so the
same module is the data map for the eventual live-API wiring (FRED `fredapi`, WGC
Gold Hub, CFTC weekly COT, BIS stats warehouse). INPUTS holds the current known
reads; swap them as fresh prints land — everything downstream is derived.
"""

from __future__ import annotations

from typing import List

from gold import macro as gmacro
from gold import dxy as gdxy
from gold import purchases_audit as gpa

# --- data-source map (for live wiring; also the honest provenance) ----------
SOURCES = {
    "BIS": {"provides": "real broad effective USD (RBUSBIS), OTC gold notional, dollar liquidity",
            "access": "stats.bis.org / data.bis.org / FRED:RBUSBIS",
            "frequency": "monthly EER; semi-annual OTC", "lag": "EER ~1mo; OTC ~2mo",
            "role": "HTF regime anchor — real dollar cycle + hidden leverage"},
    "FRED": {"provides": "DGS10/DFII10 (real rate), DFF (Fed funds), T10YIE, SOFR, WLTGAL (TGA), M2",
             "access": "fred.stlouisfed.org / api.stlouisfed.org (fredapi)",
             "frequency": "daily market; monthly macro", "lag": "minimal to ~2wk",
             "role": "real-rate direction + Fed reaction function + fiscal liquidity"},
    "WGC": {"provides": "central-bank net purchases, ETF holdings/flows, demand trends, CB survey",
            "access": "gold.org/goldhub/data", "frequency": "ETF weekly; CB monthly; demand quarterly",
            "lag": "ETF days; CB 1–2mo", "role": "structural demand floor + private/official divergence"},
    "CFTC": {"provides": "COT non-comm / managed-money net, open interest, commercial net",
             "access": "cftc.gov COT (Fri 3:30pm ET, Tue close)", "frequency": "weekly", "lag": "3 days",
             "role": "tactical positioning — stretched vs washed-out"},
}

# --- current known regime reads (update as prints land) ---------------------
INPUTS = {
    "as_of": "2026-07-06",
    "real_rate_direction": "rising_near_term",   # Q2 hawkish pivot; base case falls into Q4
    "fed_cycle": "hold_hawkish_risk",            # cuts priced out; one cut base-case Q4
    "cb_survey_conviction": "strong",            # ~89% expect reserves to rise
    "etf_flow_direction": "easing_outflows",     # selling pressure fading, not yet accumulation
    "dollar_funding": "loose",                   # no acute SOFR/TGA squeeze flagged
}


def _htf() -> List[dict]:
    """Higher-timeframe regime rows (read BEFORE any entry)."""
    dbias = gdxy.gold_from_dollar()
    return [
        {"step": 1, "source": "BIS/DXY", "question": "real broad dollar BMS or SMS?",
         "reading": f"dollar {dbias['dollar_regime'].upper()} ({dbias['dollar_phase']})",
         "gold": dbias["gold_bias"], "note": dbias["note"]},
        {"step": 2, "source": "FRED", "question": "real rates rising or falling?",
         "reading": INPUTS["real_rate_direction"],
         "gold": "short" if INPUTS["real_rate_direction"].startswith("rising") else "long",
         "note": "real-asset headwind while real rates rise; base case falls into Q4"},
        {"step": 3, "source": "WGC", "question": "central banks buying through dips?",
         "reading": f"conviction {INPUTS['cb_survey_conviction']}; {gpa.SNAPSHOT['cb_purchases_2026e_t']}t/yr",
         "gold": "long", "note": gpa.audit()["central_bank"]["read"]},
        {"step": 4, "source": "FRED", "question": "Fed hiking / pausing / cutting?",
         "reading": INPUTS["fed_cycle"], "gold": "neutral",
         "note": "hold with hawkish risk — neutral until the Q4 cut confirms"},
    ]


def _ltf() -> List[dict]:
    """Lower-timeframe entry rows (tactical timing)."""
    ps = gpa.positioning_state()
    return [
        {"step": 1, "source": "CFTC", "question": "positioning moderate or extreme?",
         "reading": f"non-comm {ps['net']:,} ({ps['zone']})",
         "gold": "short" if ps["zone"] == "stretched" else "long" if ps["zone"] == "net_short" else "neutral",
         "note": ps["note"]},
        {"step": 2, "source": "WGC", "question": "ETFs bleeding or accumulating?",
         "reading": INPUTS["etf_flow_direction"], "gold": "neutral",
         "note": "outflows easing + price holding = buy dips; 200t+ at a loss caps rallies"},
        {"step": 3, "source": "FRED", "question": "dollar funding tight or loose?",
         "reading": INPUTS["dollar_funding"], "gold": "long",
         "note": "loose funding = no squeeze = gold support"},
    ]


def regime_read() -> dict:
    """Fuse the stack into one gold regime verdict + bias + honest gaps."""
    htf, ltf = _htf(), _ltf()
    votes = [r["gold"] for r in htf + ltf]
    score = votes.count("long") - votes.count("short")
    if score >= 2:
        regime, bias = "accumulation", "long"
    elif score <= -2:
        regime, bias = "distribution", "short"
    else:
        regime, bias = "neutral", "neutral"

    contradictions = []
    if INPUTS["real_rate_direction"].startswith("rising") and bias == "long":
        contradictions.append("real rates rising near-term vs bullish structural bias "
                              "— expect two-way chop; favour dip entries, not breakouts")
    ps = gpa.positioning_state()
    if ps["zone"] == "stretched":
        contradictions.append("COT stretched — crowded long is a distribution risk")

    return {
        "as_of": INPUTS["as_of"],
        "regime": regime, "gold_bias": bias, "confluence_score": score,
        "htf": htf, "ltf": ltf,
        "dollar": gdxy.gold_from_dollar(),
        "macro_levels": {"accumulation_zone": gmacro.SNAPSHOT["accumulation_zone"],
                         "invalidation": gmacro.SNAPSHOT["invalidation"],
                         "targets": gmacro.SNAPSHOT["targets"]},
        "positioning": ps,
        "contradictions": contradictions,
        "data_gaps": [
            "BIS OTC gold notional / RBUSBIS not live-wired (semi-annual/monthly)",
            "FRED real-rate & Fed inputs are encoded reads, not live API pulls",
            "WGC/COT figures are the last published prints (see gold.purchases_audit)",
        ],
        "summary": (f"{regime.upper()} regime, gold {bias} — dollar "
                    f"{gdxy.gold_from_dollar()['dollar_regime'].upper()} + CB floor vs "
                    f"near-term real-rate headwind; buy the ${gmacro.SNAPSHOT['accumulation_zone'][0]:.0f}"
                    f"–{gmacro.SNAPSHOT['accumulation_zone'][1]:.0f} dip toward "
                    f"{gmacro.SNAPSHOT['targets'][0]:.0f}/{gmacro.SNAPSHOT['targets'][1]:.0f}"),
    }


def regime_gate(side: str, dxy_price: float = None) -> dict:
    """Confluence gate for a proposed gold entry vs the fused regime + dollar + COT.

    ``dxy_price`` is a *DXY* weekly close (NOT the gold price); omit it to use the
    anticipated dollar structure. Blocks only on genuine opposition: the dollar
    structure diverges, or COT is stretched against a long. Otherwise annotates
    confirms/neutral. Direction from gold.macro is enforced separately — this adds
    the dollar + positioning edge.
    """
    want = "long" if side.lower() in ("long", "buy") else "short"
    dollar = gdxy.confluence(side, dxy_price)
    ps = gpa.positioning_state()
    reasons = []
    ok = True

    if dollar == "diverges":
        ok = False
        reasons.append(f"dollar regime opposes a gold {want} (inverse correlation)")
    if want == "long" and ps["zone"] == "stretched":
        ok = False
        reasons.append("COT crowded long — distribution risk, wait for a positioning reset")
    if want == "short" and ps["zone"] == "net_short":
        ok = False
        reasons.append("COT net short — contrarian-bullish, poor location for a fresh short")

    status = "confirms" if (ok and dollar == "confirms") else "neutral" if ok else "diverges"
    return {"ok": ok, "status": status, "dollar": dollar,
            "cot_zone": ps["zone"],
            "reason": "; ".join(reasons) if reasons else f"regime does not oppose the {want}"}
