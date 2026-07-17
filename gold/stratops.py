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

    score = round(sum(p.values()), 1)
    return {"score": score, "excluded": None, "parts": {k: round(v, 1) for k, v in p.items()}}


def rank(candidates: Sequence[dict]) -> List[dict]:
    """Attach a score to each candidate and return them best-first (excluded last)."""
    scored = []
    for c in candidates:
        s = score_candidate(c)
        scored.append({**c, "stratops": s})
    scored.sort(key=lambda x: x["stratops"]["score"], reverse=True)
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
        row = {"trade_type": c.get("trade_type"), "signal": c.get("signal"),
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
