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
    # Live RBUSBIS-implied gold bias (long/short/neutral) once refreshed; None →
    # fall back to the anticipated DXY structure in gold.dxy.
    "dollar_gold_bias": None,
    "dollar_rbusbis_dir": None,
}


def feed_inputs(real_rate_direction: str = None, fed_cycle: str = None,
                cb_survey_conviction: str = None, etf_flow_direction: str = None,
                dollar_funding: str = None, as_of: str = None) -> dict:
    """Operator-feed the macro regime inputs from an audit report (WGC part)."""
    for k, v in (("real_rate_direction", real_rate_direction), ("fed_cycle", fed_cycle),
                 ("cb_survey_conviction", cb_survey_conviction),
                 ("etf_flow_direction", etf_flow_direction), ("dollar_funding", dollar_funding)):
        if v is not None:
            INPUTS[k] = v
    if as_of:
        INPUTS["as_of"] = as_of
    return dict(INPUTS)


def price_inelastic_demand() -> dict:
    """Score how price-INELASTIC the structural gold demand is (B8, 0-1).

    Central banks that buy THROUGH drawdowns + strong reserve-survey conviction +
    ETF outflows fading = demand that doesn't chase price = a firm structural floor
    (buy dips, hold). Low score = demand is price-sensitive (no floor)."""
    conv = INPUTS.get("cb_survey_conviction")
    etf = INPUTS.get("etf_flow_direction")
    conv_map = {"strong": 0.5, "moderate": 0.3, "weak": 0.1}
    etf_map = {"accumulation": 0.4, "easing_outflows": 0.25, "outflows": 0.0}
    score = min(1.0, conv_map.get(conv, 0.2) + etf_map.get(etf, 0.15))
    band = "high" if score >= 0.6 else "moderate" if score >= 0.35 else "low"
    return {"score": round(score, 2), "band": band,
            "cb_conviction": conv, "etf_flow": etf,
            "note": (f"price-inelastic demand {band} ({score:.2f}) — "
                     + {"high": "firm structural floor: buy dips, hold",
                        "moderate": "partial floor",
                        "low": "demand is price-sensitive — no reliable floor"}[band])}


def dollar_gold_bias() -> str:
    """The dollar-implied gold bias — live RBUSBIS when refreshed, else the
    anticipated DXY structure."""
    live = INPUTS.get("dollar_gold_bias")
    if live in ("long", "short", "neutral"):
        return live
    return gdxy.gold_from_dollar()["gold_bias"]


def _dollar_confluence(side: str) -> str:
    want = "long" if side.lower() in ("long", "buy") else "short"
    gb = dollar_gold_bias()
    return "confirms" if gb == want else "diverges" if gb in ("long", "short") else "neutral"


def _htf() -> List[dict]:
    """Higher-timeframe regime rows (read BEFORE any entry)."""
    dbias = gdxy.gold_from_dollar()
    live = INPUTS.get("dollar_gold_bias") in ("long", "short", "neutral")
    d_gold = dollar_gold_bias()
    d_src = (f"RBUSBIS live ({INPUTS.get('dollar_rbusbis_dir')})" if live
             else f"DXY anticipated ({dbias['dollar_phase']})")
    return [
        {"step": 1, "source": "BIS/FRED RBUSBIS" if live else "DXY", "question": "real broad dollar rising or falling?",
         "reading": d_src, "gold": d_gold,
         "note": f"dollar→gold inverse ({d_src})"},
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
    liq = gpa.liquidity_state()
    if liq["state"] == "impaired":
        contradictions.append(f"open interest {liq['vs_peak_pct']}% below peak — thin tape: "
                              "widen stops, size down, expect sharper sweeps")

    return {
        "as_of": INPUTS["as_of"],
        "regime": regime, "gold_bias": bias, "confluence_score": score,
        "htf": htf, "ltf": ltf,
        "dollar": gdxy.gold_from_dollar(),
        "macro_levels": {"accumulation_zone": gmacro.SNAPSHOT["accumulation_zone"],
                         "invalidation": gmacro.SNAPSHOT["invalidation"],
                         "targets": gmacro.SNAPSHOT["targets"]},
        "positioning": ps,
        "liquidity": liq,
        "gold_long_lock": gdxy.dxy_flip_status(),
        "fused_verdict": (
            f"{'ACCUMULATION floor' if bias == 'long' else 'DISTRIBUTION' if bias == 'short' else 'NEUTRAL'}"
            f" ({regime}) over a "
            + ("near-term HEADWIND (real rates up / dollar bid) — "
               if INPUTS['real_rate_direction'].startswith('rising') else "")
            + f"gold longs {gdxy.dxy_flip_status()['gold_longs'].upper()} until DXY flips; "
            + (f"thin tape ({liq['vs_peak_pct']}% OI) → widen stops" if liq["state"] == "impaired"
               else "normal depth")),
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


async def refresh_inputs() -> dict:
    """Pull live FRED (real rate / Fed) + CFTC (gold COT) and update the regime
    inputs in place. Each feed degrades independently — a missing key/failure just
    leaves that input on its encoded value. Returns what was applied + the regime.
    """
    from services import fred_service as fred, cftc_service as cftc
    from gold import purchases_audit as gpa

    applied = {}
    rr = await fred.real_rate_read()
    if rr:
        INPUTS["real_rate_direction"] = ("rising_near_term" if rr["direction"] == "rising"
                                         else "falling" if rr["direction"] == "falling"
                                         else "flat")
        applied["real_rate"] = rr
    ff = await fred.fed_funds_read()
    if ff:
        INPUTS["fed_cycle"] = {"hiking": "hiking", "cutting": "cutting",
                               "hold": "hold_hawkish_risk"}[ff["cycle"]]
        applied["fed_funds"] = ff
    usd = await fred.dollar_read()
    if usd:
        INPUTS["dollar_gold_bias"] = usd["gold"]          # live RBUSBIS → gold bias
        INPUTS["dollar_rbusbis_dir"] = usd["direction"]
        applied["dollar_rbusbis"] = usd
    cot = await cftc.gold_cot()
    if cot:
        gpa.SNAPSHOT["cot_noncomm_net"] = cot["noncomm_net"]
        gpa.SNAPSHOT["cot_open_interest"] = cot["open_interest"]
        applied["cot"] = cot
    if applied:
        INPUTS["as_of"] = "live-refresh"
    return {"applied": applied, "sources_live": list(applied), "regime": regime_read()}


def regime_gate(side: str, dxy_price: float = None, strict: bool = False) -> dict:
    """Confluence gate for a proposed gold entry vs the fused regime + dollar + COT.

    ``dxy_price`` is a *DXY* weekly close (NOT the gold price); omit it to use the
    anticipated dollar structure. Blocks on genuine opposition: the dollar
    structure diverges, or COT is stretched against a long. Otherwise annotates
    confirms/neutral. Direction from gold.macro is enforced separately — this adds
    the dollar + positioning edge.

    ``strict`` enforces the 2026 rule: a gold LONG is not merely 'not opposed' but
    requires the dollar to actively CONFIRM bearish (the DXY flip). Until DXY flips,
    strict mode blocks fresh longs — shorts / range-fades are unaffected. This is
    the gate that removes the premature-long bleed the P3 backtest exposed.
    """
    want = "long" if side.lower() in ("long", "buy") else "short"
    # Prefer the live RBUSBIS-implied bias; fall back to the DXY structure.
    dollar = _dollar_confluence(side) if INPUTS.get("dollar_gold_bias") else gdxy.confluence(side, dxy_price)
    ps = gpa.positioning_state()
    # Monthly DXY fib-band structural trigger (only fires when a DXY price is given).
    trig = gdxy.gold_structure_trigger(dxy_price)
    # The 2026 gold-long lock (dollar flipped bearish yet?).
    flip = gdxy.dxy_flip_status(dxy_price, INPUTS.get("dollar_rbusbis_dir"))
    reasons = []
    ok = True

    if strict:
        # 2026 rule: the DXY FLIP is the authority and supersedes the legacy inverse
        # read (which calls a bouncing-but-bid dollar 'buy the gold dip'). A gold
        # long is LOCKED until the dollar actually flips bearish; shorts / range-
        # fades are the play while it is still bid, so they are not blocked here.
        if want == "long" and not flip["unlocked"]:
            ok = False
            reasons.append(f"strict: gold long LOCKED until DXY flips bearish — {flip['note']}")
    else:
        if dollar == "diverges":
            ok = False
            reasons.append(f"dollar regime opposes a gold {want} (inverse correlation)")

    # COT positioning objections apply in both modes.
    if want == "long" and ps["zone"] == "stretched":
        ok = False
        reasons.append("COT crowded long — distribution risk, wait for a positioning reset")
    if want == "short" and ps["zone"] == "net_short":
        ok = False
        reasons.append("COT net short — contrarian-bullish, poor location for a fresh short")
    # A DXY ceiling trigger opposes fresh longs; a last-discount trigger opposes
    # shorts. In strict mode a CONFIRMED flip supersedes the ceiling opposition —
    # the roll-over off the extreme is exactly when gold longs turn on.
    if trig.get("trigger") and trig["gold_bias"] != want:
        if not (strict and want == "long" and flip["unlocked"]):
            ok = False
            reasons.append(f"DXY structure trigger ({trig['trigger']}) opposes the {want}: {trig['note']}")

    # A confirming max-strength trigger — or, in strict mode, a real DXY flip —
    # upgrades the read to 'confirms'.
    confirmed = dollar == "confirms" or (trig.get("gold_bias") == want and trig.get("strength") in ("max", "cap"))
    if strict and want == "long" and flip["unlocked"]:
        confirmed = True

    status = "confirms" if (ok and confirmed) else "neutral" if ok else "diverges"
    return {"ok": ok, "status": status, "dollar": dollar,
            "cot_zone": ps["zone"], "dxy_trigger": trig.get("trigger"),
            "dxy_flip": flip,
            "reason": "; ".join(reasons) if reasons else f"regime does not oppose the {want}"}
