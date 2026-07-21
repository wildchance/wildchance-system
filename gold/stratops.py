"""STRATOPS — the theater opportunity sorter (pure).

Turns AFCENT's surveillance (every live candidate signal across the tiers/limits)
into a ranked engagement list, then allocates deployment under ARCENT's exposure
cap. Each candidate is a signal card the scans already emit; STRATOPS scores it by
confluence TOWARD THE CAMPAIGN OBJECTIVE and picks the best that fit the risk budget.

  score_candidate → 0-100 from weighted confluence
  rank            → sort candidates best-first
  allocate        → take / hold / stand-down given the exposure cap
"""

from __future__ import annotations

from typing import List, Sequence

from gold.exposure import can_open, DEFAULT_RISK_CAP_USD, DEFAULT_MAX_POSITIONS

# Per-tier confidence factors — FIT FROM THE P3 BACKTEST (2026-07-18, live H1,
# 211 intraday/intrasession trades + 5 swing):
#   intrasession GREEN  +0.41R/trade, PF 2.39, 55% win → lean in (1.415)
#   intraday     RED    −0.05R/trade, PF 0.89          → size down (0.949)
#   swing        INCONCLUSIVE (n=5)                    → neutral until sampled
# Unmeasured tiers stay neutral. Refit any time via fit_tier_factors() /
# POST /gold/stratops/fit — the factor is each tier's reflection confidence_factor.
TIER_FACTORS = {
    "intrasession": 1.415,
    "intraday": 0.949,
    "swing": 1.0,
    "crt": 1.0,
    "prelondon": 1.0,
    "sd_fade": 1.0,
    "sniper": 1.0,        # OB-zone layered limits — neutral until sampled
}


def fit_tier_factors(intraday_report: dict = None, swing_report: dict = None,
                     min_sample: int = 10) -> dict:
    """Refit TIER_FACTORS from backtest reports (the P3 loop).

    Each tier's factor becomes its scorecard's reflection confidence_factor,
    ignoring tiers below ``min_sample`` closed trades. Returns what was applied.
    """
    applied = {}
    for rep in (intraday_report, swing_report):
        if not rep:
            continue
        groups = rep.get("by_tier") or ({rep["tier"]: rep["scorecard"]} if rep.get("tier") else {})
        for tier, card in groups.items():
            if tier in TIER_FACTORS and (card.get("n") or 0) >= min_sample:
                TIER_FACTORS[tier] = round(float(card["confidence_factor"]), 3)
                applied[tier] = TIER_FACTORS[tier]
    return {"applied": applied, "tier_factors": dict(TIER_FACTORS)}


# Confluence weights (sum = 100). Objective alignment dominates — a trade that
# doesn't advance the campaign is not what we deploy for.
WEIGHTS = {
    "objective": 30,     # advances the range-to-range campaign
    "htf": 15,           # HTF timeline zone aligns
    "regime": 15,        # dollar/COT regime confirms
    "location": 12,      # discount (long) / premium (short)
    "protraction": 12,   # session sweep+reversal in the trade direction
    "liquidity": 8,      # a liquidity draw toward the objective exists
    "rr": 8,             # tier reward:risk headroom
}

# Swing-continuation + positioning confluence bonuses (added on top of the 100-point
# base, then clamped) — a 4H b2b bomber, an HTF warthog OTE, and/or options-flow
# agreement (entry on a put/call wall or the expected-move edge) each lift a
# candidate up the engagement list.
B2B_BONUS = 10
WARTHOG_BONUS = 10
OPTIONS_BONUS = 10


def score_candidate(c: dict) -> dict:
    """0-100 confluence score for one candidate signal card. Gate-blocked or news-
    blocked cards score 0 (excluded)."""
    if c.get("signal") not in ("LONG", "SHORT"):
        return {"score": 0, "excluded": "not a directional signal", "parts": {}}
    if not c.get("gate", {}).get("allow", False):
        return {"score": 0, "excluded": "prop gate blocked", "parts": {}}
    if c.get("news") and c.get("news_block"):
        return {"score": 0, "excluded": "news-blocked", "parts": {}}

    p = {}
    p["objective"] = WEIGHTS["objective"] if (c.get("campaign") or {}).get("status") == "advances" else \
        (WEIGHTS["objective"] * 0.4 if (c.get("campaign") or {}).get("status") == "neutral" else 0)
    p["htf"] = WEIGHTS["htf"] if c.get("htf_confluence") == "aligns" else \
        (WEIGHTS["htf"] * 0.4 if c.get("htf_confluence") in (None, "neutral") else 0)
    reg = (c.get("regime") or {}).get("status")
    p["regime"] = WEIGHTS["regime"] if reg == "confirms" else (WEIGHTS["regime"] * 0.5 if reg == "neutral" else 0)
    loc = (c.get("location") or {})
    p["location"] = WEIGHTS["location"] if loc.get("ok") else (WEIGHTS["location"] * 0.5 if not loc else 0)
    protr = (c.get("protraction") or {}).get("direction")
    want = "long" if c["signal"] == "LONG" else "short"
    p["protraction"] = WEIGHTS["protraction"] if protr == want else 0
    p["liquidity"] = WEIGHTS["liquidity"] if c.get("liquidity_draw") or c.get("liquidity_target") else 0
    tps = c.get("targets") or []
    best_rr = max((t.get("rr", 0) for t in tps), default=0)
    p["rr"] = min(WEIGHTS["rr"], WEIGHTS["rr"] * best_rr / 8.0)   # 8R = full marks

    # 4H b2b-bomber + HTF warthog + options-flow agreement — confluence bonuses.
    p["b2b"] = B2B_BONUS if c.get("b2b_confluence") is True else 0
    p["warthog"] = WARTHOG_BONUS if c.get("warthog_confluence") is True else 0
    p["options"] = OPTIONS_BONUS if c.get("options_confluence") is True else 0

    # Scale by the tier's MEASURED confidence factor (P3 backtest fit): a GREEN
    # tier leans in, a RED tier is discounted before allocation.
    factor = TIER_FACTORS.get(c.get("trade_type"), 1.0)
    score = round(min(100.0, sum(p.values()) * factor), 1)
    return {"score": score, "excluded": None, "tier_factor": factor,
            "b2b": c.get("b2b_confluence") is True,
            "warthog": c.get("warthog_confluence") is True,
            "options": c.get("options_confluence") is True,
            "parts": {k: round(v, 1) for k, v in p.items()}}


def rank(candidates: Sequence[dict]) -> List[dict]:
    """Attach a score to each candidate and return them best-first (excluded last).

    Each row keeps ``stratops.idx`` — the index into the ORIGINAL candidates list —
    so the deploy step can recover the full card for anything allocated."""
    scored = []
    for i, c in enumerate(candidates):
        s = score_candidate(c)
        s["idx"] = i
        scored.append({**c, "stratops": s})
    # best-first; ties break toward a b2b/warthog-confirmed continuation.
    scored.sort(key=lambda x: (x["stratops"]["score"],
                               (1 if x["stratops"].get("b2b") else 0)
                               + (1 if x["stratops"].get("warthog") else 0)),
                reverse=True)
    return scored


def allocate(candidates: Sequence[dict], positions: Sequence[dict],
             risk_cap: float = DEFAULT_RISK_CAP_USD,
             max_positions: int = DEFAULT_MAX_POSITIONS,
             min_score: float = 55.0) -> dict:
    """Rank, then deploy the best that clear ``min_score`` and fit the exposure cap.

    Returns the engagement list: take / hold / stand_down, plus the running budget.
    """
    ranked = rank(candidates)
    sim = list(positions)                       # copy; grow as we "take" candidates
    take, hold, stand_down = [], [], []
    for c in ranked:
        s = c["stratops"]["score"]
        row = {"idx": c["stratops"]["idx"],
               "trade_type": c.get("trade_type"), "signal": c.get("signal"),
               "entry": c.get("entry"), "score": s,
               "campaign": (c.get("campaign") or {}).get("status")}
        if c["stratops"]["excluded"]:
            stand_down.append({**row, "reason": c["stratops"]["excluded"]})
            continue
        if s < min_score:
            stand_down.append({**row, "reason": f"score {s} < {min_score}"})
            continue
        gate = can_open(sim, float(c.get("risk_usd") or 0.0), risk_cap, max_positions)
        if not gate["ok"]:
            hold.append({**row, "reason": gate["reason"]})
            continue
        take.append({**row, "reason": "deployed", "risk_usd": c.get("risk_usd")})
        sim.append({"status": "OPEN", "risk_usd": c.get("risk_usd") or 0.0})
    return {"objective_note": _objective_note(ranked),
            "take": take, "hold": hold, "stand_down": stand_down,
            "budget": can_open(sim, 0.0, risk_cap, max_positions)}


def _objective_note(ranked: Sequence[dict]) -> str:
    for c in ranked:
        camp = c.get("campaign") or {}
        if camp.get("objective"):
            o = camp["objective"]
            return f"campaign → {o.get('zone')} @ {o.get('price')} (~${camp.get('leg_usd')} leg)"
    return "no campaign objective set"
