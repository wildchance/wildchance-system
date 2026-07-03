"""EdgeFinder — per-pair macro bias scoreboard (pure aggregator).

Rolls the independent read layers the system already produces into ONE signed
bias score per pair, EdgeFinder-style, with a transparent factor breakdown:

  • Retail (contrarian)  — crowded retail positioning faded (myfxbook via feed)
  • COT (institutional)  — Commitment-of-Traders net positioning, the slow layer
  • MMM weekly cycle     — Taylor day / peak-formation directional bias (optional)
  • News                 — high-impact-event caution flag (not scored, annotates)

Each factor casts a signed, weighted vote; the sum → LONG/SHORT strength. Kept
pure so it's fully testable: the service layer feeds it the wildchance signal
dict plus an optional MMM bias and news string.
"""

from __future__ import annotations

from typing import List, Optional

# retail crowding thresholds (retail_extreme = max(long%, short%))
_RETAIL_MIN = 58        # below this the crowd isn't lopsided → no signal
_RETAIL_STRONG = 65     # at/above this the contrarian vote doubles


def _dir(contrarian: Optional[str]) -> int:
    return 1 if contrarian == "long" else -1 if contrarian == "short" else 0


def _retail_factor(sig: dict) -> dict:
    ext = sig.get("retail_extreme") or 0
    d = _dir(sig.get("contrarian"))
    rl = sig.get("retail_long")
    if ext < _RETAIL_MIN or d == 0:
        return {"name": "retail", "score": 0,
                "note": f"retail {rl}%L balanced — no contrarian edge"}
    w = 2 if ext >= _RETAIL_STRONG else 1
    lbl = "long" if d > 0 else "short"
    return {"name": "retail", "score": d * w,
            "note": f"retail {rl}%L crowded → contrarian {lbl} (×{w})"}


def _cot_factor(sig: dict) -> dict:
    if sig.get("cot_agrees") is None or sig.get("cot_net") is None:
        return {"name": "cot", "score": 0, "note": "no COT data"}
    d = _dir(sig.get("contrarian"))
    if d == 0:
        return {"name": "cot", "score": 0, "note": "COT direction indeterminate"}
    cot_dir = d if sig.get("cot_agrees") else -d      # agree → same side as contrarian
    rank = sig.get("cot_rank")
    extreme = rank is not None and (rank <= 0.2 or rank >= 0.8)
    w = 2 if extreme else 1                            # COT is the dominant, slow layer
    lbl = "long" if cot_dir > 0 else "short"
    tag = " extreme" if extreme else ""
    return {"name": "cot", "score": cot_dir * w,
            "note": f"COT {lbl} (net {sig.get('cot_net')}, rank {rank}){tag} (×{w})"}


def _mmm_factor(mmm_bias: Optional[dict]) -> dict:
    if not mmm_bias:
        return {"name": "mmm", "score": 0, "note": "no MMM read"}
    s = max(-2, min(2, mmm_bias.get("score", 0)))
    return {"name": "mmm", "score": s,
            "note": f"MMM weekly-cycle {mmm_bias.get('bias', 'neutral')}"}


def agreement(score: int, side: str) -> str:
    """Does an EdgeFinder score agree with a proposed side?

    confirms | diverges | neutral — used to gate/annotate alerts.
    """
    want = 1 if side.lower() in ("long", "buy") else -1 if side.lower() in ("short", "sell") else 0
    d = 1 if score > 0 else -1 if score < 0 else 0
    if d == 0 or want == 0:
        return "neutral"
    return "confirms" if d == want else "diverges"


def label(total: int) -> str:
    if total >= 3:
        return "STRONG LONG"
    if total >= 1:
        return "LONG"
    if total <= -3:
        return "STRONG SHORT"
    if total <= -1:
        return "SHORT"
    return "NEUTRAL"


def score_pair(sig: dict, mmm_bias: Optional[dict] = None,
               news: Optional[str] = None) -> dict:
    """Aggregate a wildchance signal (+ optional MMM bias, news) into a bias row."""
    factors: List[dict] = [_retail_factor(sig), _cot_factor(sig)]
    if mmm_bias is not None:
        factors.append(_mmm_factor(mmm_bias))
    total = sum(f["score"] for f in factors)
    return {
        "pair": sig.get("pair"),
        "price": sig.get("price"),
        "bias": label(total),
        "score": total,
        "system_verdict": sig.get("verdict"),           # wildchance's own call
        "system_confidence": sig.get("confidence"),
        "retail_long": sig.get("retail_long"),
        "cot_net": sig.get("cot_net"),
        "cot_rank": sig.get("cot_rank"),
        "factors": factors,
        "news": news,                                   # caution string or None
    }
