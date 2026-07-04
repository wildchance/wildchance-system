"""Gold intraday engine — weekly ICT profile × Quarterly-Theory session × Hurst FLD.

The 1+/day Tue–Fri trigger, with full justification at three fractal scales:

  DIRECTION  weekly ICT profile (built)         → the week's bias
  TIMING     daily QT session quarter           → London Q2 (Judas) sets up NY-AM
                                                   Q3 (distribution) — the trade window
  TRIGGER    Hurst FLD crossover on the cycle    → the mechanical entry confirmation
  SIZE+GATE  size_for_risk + prop-firm (built)

Pure: the service feeds it the classified profile, the live session quarter, the
FLD read, and the entry. NO TRADE (not an error) whenever a layer says wait.
"""

from __future__ import annotations

from typing import Optional

from gold.signal import assemble, format_card as _format_base


def assemble_intraday(weekly_profile: dict, session_q: dict, fld_sig: dict,
                      entry: float, balance: float, tier: str = "6",
                      risk_usd: float = 20.0, sl_pips: float = 200.0,
                      weekday_q: Optional[dict] = None,
                      require_fld: bool = True,
                      require_distribution: bool = False) -> dict:
    """Combine the three scales into one gated intraday signal."""
    bias = (weekly_profile or {}).get("bias")
    if bias not in ("long", "short"):
        return {"signal": "NO TRADE", "instrument": "XAU/USD",
                "reason": f"no weekly direction ({(weekly_profile or {}).get('profile')})",
                "layers": {"weekly": weekly_profile, "session": session_q, "fld": fld_sig}}

    # base sized card (direction from the weekly profile)
    card = assemble(weekly_profile, entry, balance, tier=tier,
                    risk_usd=risk_usd, sl_pips=sl_pips)
    card["layers"] = {"weekly": weekly_profile, "session": session_q, "fld": fld_sig,
                      "weekday": weekday_q}
    if card.get("signal") == "NO TRADE":
        return card

    reasons = [
        f"weekly {weekly_profile.get('profile')} ({bias})",
        f"session Q{session_q.get('quarter')} {session_q.get('phase')} ({session_q.get('session')})",
        f"FLD {fld_sig.get('cross') or fld_sig.get('position')}",
    ]

    # weekly QT gate — Friday is reversal / no-trade
    if weekday_q is not None and not weekday_q.get("tradeable", True):
        card["signal"] = "NO TRADE"
        card["reason"] = "weekly Q — Friday (reversal / stand aside)"
        return card

    # timing gate — need the distribution (Q3) or manipulation set-up (Q2)
    q = session_q.get("quarter")
    timing_ok = (q == 3) if require_distribution else (q in (2, 3))
    if not timing_ok:
        card["signal"] = "NO TRADE"
        card["reason"] = (f"wait — {session_q.get('phase')} quarter (Q{q}); "
                          "trade the London→NY distribution window")
        return card

    # FLD trigger — crossover (or at least position) must agree with the bias
    fld_dir = fld_sig.get("cross") or fld_sig.get("position_dir")
    fld_ok = (fld_dir == "bull" and bias == "long") or (fld_dir == "bear" and bias == "short")
    if require_fld and not fld_ok:
        card["signal"] = "NO TRADE"
        card["reason"] = f"FLD not confirming {bias} yet (FLD {fld_dir})"
        return card

    card["justification"] = "  ·  ".join(reasons)
    card["fld_confirms"] = fld_ok
    return card


def format_card(sig: dict) -> str:
    """Telegram card = the base gold card + the intraday layer line."""
    base = _format_base(sig)
    if sig.get("signal") in ("LONG", "SHORT"):
        layers = sig.get("layers", {})
        sess = layers.get("session", {})
        fld = layers.get("fld", {})
        base += (f"\n🕐 Q{sess.get('quarter')} {sess.get('phase')} · "
                 f"FLD {fld.get('cross') or fld.get('position')}"
                 f"{' ✅' if sig.get('fld_confirms') else ''}")
    return base
