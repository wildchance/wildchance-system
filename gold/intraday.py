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

from typing import Optional, Sequence

from gold.signal import assemble, assemble_structured, format_card as _format_base
from gold import macro as gmacro
from gold import macro_cycle as gcycle
from gold.location import location_gate


def assemble_intraday(weekly_profile: dict, session_q: dict, fld_sig: dict,
                      entry: float, balance: float, tier: str = "6",
                      risk_usd: float = 20.0, sl_pips: float = 200.0,
                      weekday_q: Optional[dict] = None,
                      require_fld: bool = True,
                      require_distribution: bool = False,
                      entry_read: Optional[dict] = None,
                      ref_high: Optional[float] = None,
                      ref_low: Optional[float] = None,
                      require_macro: bool = True,
                      require_location: bool = True,
                      require_regime: bool = True,
                      loc_deep: float = 0.5,
                      stop_override: Optional[float] = None,
                      rr: Sequence[float] = (2, 3, 4),
                      trade_type: Optional[str] = None) -> dict:
    """Combine the three scales into one gated intraday signal.

    If ``entry_read`` (from gold.entry.refined_entry) has a valid BMS/OTE, the
    trade is sized off that structure entry+stop; otherwise the fixed-pip entry.

    Two gates added over the raw profile fusion:
      • MACRO ANCHOR — the weekly bias must agree with (or be neutral to) the
        standing macro bias, and price must not be below the macro invalidation.
        This is the fix for the mid-week direction flip: macro is the week's
        stable anchor, so a profile that flips against it stands aside.
      • LOCATION — a long may only enter in discount, a short only in premium
        (relative to ``ref_high``/``ref_low``). No more market-filling premium.
    """
    bias = (weekly_profile or {}).get("bias")
    if bias not in ("long", "short"):
        return {"signal": "NO TRADE", "instrument": "XAU/USD",
                "reason": f"no weekly direction ({(weekly_profile or {}).get('profile')})",
                "layers": {"weekly": weekly_profile, "session": session_q, "fld": fld_sig}}

    # --- MACRO ANCHOR (mid-week-flip damper) ------------------------------
    macro = gmacro.macro_confluence(bias, entry)
    if require_macro:
        if macro["invalidated"]:
            return {"signal": "NO TRADE", "instrument": "XAU/USD",
                    "reason": macro["note"], "macro": macro,
                    "layers": {"weekly": weekly_profile, "session": session_q, "fld": fld_sig}}
        if macro["status"] == "diverges":
            return {"signal": "NO TRADE", "instrument": "XAU/USD",
                    "reason": (f"weekly {bias} diverges from macro {macro['bias']} anchor — "
                               "stand aside (no mid-week flip)"),
                    "macro": macro,
                    "layers": {"weekly": weekly_profile, "session": session_q, "fld": fld_sig}}

    # base sized card. Precedence:
    #   1. tier stop_override — the structural stop for the trade-type tier, sized
    #      to the tier's R:R band (swing 5-8R off the weekly low, etc.);
    #   2. Wade OTE/OB structure entry, if a BMS is present;
    #   3. fixed-pip fallback.
    if stop_override is not None:
        card = assemble_structured(weekly_profile, entry, stop_override, balance,
                                   tier=tier, risk_usd=risk_usd, rr=rr)
    elif entry_read and entry_read.get("ok") and entry_read.get("entry") and entry_read.get("stop"):
        card = assemble_structured(weekly_profile, entry_read["entry"], entry_read["stop"],
                                   balance, tier=tier, risk_usd=risk_usd, rr=rr)
        card["structure"] = {k: entry_read.get(k) for k in
                             ("bms", "broke_level", "zone", "order_block", "fvg")}
    else:
        card = assemble(weekly_profile, entry, balance, tier=tier,
                        risk_usd=risk_usd, sl_pips=sl_pips, rr=rr)
    card["layers"] = {"weekly": weekly_profile, "session": session_q, "fld": fld_sig,
                      "weekday": weekday_q}
    if trade_type:
        card["trade_type"] = trade_type
    card["macro"] = macro
    if card.get("signal") == "NO TRADE":
        return card

    reasons = [
        (f"{trade_type} · " if trade_type else "") + f"weekly {weekly_profile.get('profile')} ({bias})",
        f"session Q{session_q.get('quarter')} {session_q.get('phase')} ({session_q.get('session')})",
        f"FLD {fld_sig.get('cross') or fld_sig.get('position')}",
        f"macro {macro['status']} ({macro['note']})",
    ]
    if card.get("structure"):
        reasons.append(f"BMS {card['structure']['bms']} + OTE zone {card['structure']['zone']}")

    # --- LOCATION gate — buy discount / sell premium, never chase ----------
    if require_location and ref_high is not None and ref_low is not None:
        loc = location_gate(bias, card.get("entry", entry), ref_high, ref_low, deep=loc_deep)
        card["location"] = loc
        if not loc["ok"]:
            card["signal"] = "NO TRADE"
            card["reason"] = loc["reason"]
            return card
        reasons.append(f"location {loc['zone']} (pos {loc['pos']:.2f})")

    # --- REGIME gate — dollar (DXY inverse) + COT positioning confluence ----
    # No live DXY here → the gate uses the anticipated dollar structure.
    reg = gcycle.regime_gate(bias)
    card["regime"] = reg
    if require_regime and not reg["ok"]:
        card["signal"] = "NO TRADE"
        card["reason"] = reg["reason"]
        return card
    reasons.append(f"regime {reg['status']} (dollar {reg['dollar']})")

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
        tt = sig.get("trade_type")
        if tt:
            base = base.replace("*GOLD XAU/USD", f"*[{tt.upper()}] GOLD XAU/USD", 1)
        layers = sig.get("layers", {})
        sess = layers.get("session", {})
        fld = layers.get("fld", {})
        base += (f"\n🕐 Q{sess.get('quarter')} {sess.get('phase')} · "
                 f"FLD {fld.get('cross') or fld.get('position')}"
                 f"{' ✅' if sig.get('fld_confirms') else ''}")
        st = sig.get("structure")
        if st:
            base += f"\n🏗️ BMS {st.get('bms')} · OTE {st.get('zone')}"
            if st.get("order_block"):
                base += f" · OB {st['order_block']['ob_low']}–{st['order_block']['ob_high']}"
        loc = sig.get("location")
        if loc and loc.get("pos") is not None:
            base += f"\n📍 {loc['zone']} entry (pos {loc['pos']:.2f})"
        mac = sig.get("macro")
        if mac:
            base += f"\n🌍 macro {mac['status']}"
            if mac.get("in_accumulation"):
                base += " · in accumulation band"
        reg = sig.get("regime")
        if reg:
            base += f"\n💵 dollar {reg['dollar']} · COT {reg['cot_zone']}"
    return base
