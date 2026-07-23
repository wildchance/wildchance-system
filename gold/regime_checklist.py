"""Regime-invalidation checklist (the real kernel of B12) — a 6-condition go/no-go.

Strips the "interdimensional / IDM" theatre down to what actually matters: a small
set of hard conditions that, when they flip, invalidate the standing gold regime.
Each condition is a pass/fail with a reason; the aggregate is a GREEN/AMBER/RED
verdict the report (and the operator) can read at a glance.

The six conditions (all evaluated from inputs the system already computes):
  1. DXY flip lock      — are trend longs still locked (gold not structurally bull)?
  2. Real-rate direction — rising real rates oppose a long regime.
  3. COT positioning     — stretched/crowded = distribution risk.
  4. OI / liquidity      — impaired open interest = thin tape, sharper sweeps.
  5. HTF structure       — does the fused OB bias still agree with the regime?
  6. Key level integrity — has price violated the regime's invalidation level?

Pure: pass the already-computed inputs; a service wrapper gathers them live.
"""

from __future__ import annotations

from typing import Optional


def build_checklist(regime_bias: str,
                    dxy_unlocked: Optional[bool] = None,
                    real_rate_direction: Optional[str] = None,
                    cot_zone: Optional[str] = None,
                    liquidity_state: Optional[str] = None,
                    htf_bias: Optional[str] = None,
                    price: Optional[float] = None,
                    invalidation_level: Optional[float] = None) -> dict:
    """Evaluate the 6-condition regime-invalidation checklist for ``regime_bias``
    ("long"/"short"/"neutral"). Each condition → {ok, note}; aggregate → verdict."""
    long = regime_bias == "long"
    checks = []

    # 1) DXY flip lock — for a LONG regime, trend longs must be UNLOCKED to be valid.
    if dxy_unlocked is None:
        checks.append({"id": 1, "name": "dxy_flip", "ok": None, "note": "no DXY read"})
    else:
        ok = (dxy_unlocked if long else True)
        checks.append({"id": 1, "name": "dxy_flip", "ok": ok,
                       "note": ("longs unlocked" if dxy_unlocked else
                                "longs LOCKED — a long regime is invalidated until the flip")})

    # 2) Real rates — rising real rates oppose a long regime.
    if real_rate_direction is None:
        checks.append({"id": 2, "name": "real_rates", "ok": None, "note": "no rate read"})
    else:
        rising = str(real_rate_direction).startswith("rising")
        ok = (not rising) if long else True
        checks.append({"id": 2, "name": "real_rates", "ok": ok,
                       "note": f"real rates {real_rate_direction}"
                               + (" — headwind to longs" if (rising and long) else "")})

    # 3) COT positioning — stretched = crowded/distribution risk.
    if cot_zone is None:
        checks.append({"id": 3, "name": "cot", "ok": None, "note": "no COT read"})
    else:
        ok = cot_zone != "stretched"
        checks.append({"id": 3, "name": "cot", "ok": ok,
                       "note": f"COT {cot_zone}"
                               + (" — crowded, distribution risk" if not ok else "")})

    # 4) OI / liquidity integrity.
    if liquidity_state is None:
        checks.append({"id": 4, "name": "liquidity", "ok": None, "note": "no OI read"})
    else:
        ok = liquidity_state != "impaired"
        checks.append({"id": 4, "name": "liquidity", "ok": ok,
                       "note": f"liquidity {liquidity_state}"
                               + (" — thin tape, sharper sweeps" if not ok else "")})

    # 5) HTF structure agrees with the regime.
    if htf_bias is None or regime_bias == "neutral":
        checks.append({"id": 5, "name": "htf_structure", "ok": None,
                       "note": "no HTF bias / neutral regime"})
    else:
        ok = htf_bias == regime_bias
        checks.append({"id": 5, "name": "htf_structure", "ok": ok,
                       "note": f"HTF OB bias {htf_bias}"
                               + (" — disagrees with the regime" if not ok else " — agrees")})

    # 6) Key-level integrity — price still on the right side of the invalidation level.
    if price is None or invalidation_level is None:
        checks.append({"id": 6, "name": "key_level", "ok": None,
                       "note": "no invalidation level set"})
    else:
        ok = (price > invalidation_level) if long else (price < invalidation_level)
        checks.append({"id": 6, "name": "key_level", "ok": ok,
                       "note": (f"price {round(price,2)} vs invalidation {round(invalidation_level,2)}"
                                + ("" if ok else " — VIOLATED"))})

    evaluated = [c for c in checks if c["ok"] is not None]
    failed = [c for c in evaluated if c["ok"] is False]
    n_fail = len(failed)
    if n_fail == 0 and evaluated:
        verdict = "GREEN"
    elif n_fail >= 3:
        verdict = "RED"
    elif n_fail >= 1:
        verdict = "AMBER"
    else:
        verdict = "INCONCLUSIVE"
    return {
        "regime_bias": regime_bias, "verdict": verdict,
        "checks": checks, "evaluated": len(evaluated),
        "failed": [c["name"] for c in failed], "fail_count": n_fail,
        "note": (f"regime {regime_bias.upper()} — {verdict}: "
                 + ("all conditions hold" if n_fail == 0 else
                    f"{n_fail} broken ({', '.join(c['name'] for c in failed)})")),
    }
