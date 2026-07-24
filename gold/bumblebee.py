"""Bumblebee — intra-session sweep-and-continuity scalper (pure).

The operator's DAILY anticipation chain, codified. All hours are UTC-4 (broker):

  • ASIAN CBDR 14:00–20:00 — the 6h box forms here (the system's "cbdr" window). Its
    ±SD projections are the pre-London BUY LIMIT (−SD) and SELL LIMIT (+SD).
  • CRT 1-5-9 from 21:00 — the trend begins off how the box formed (= the 01:00 UTC
    CRT anchor), running toward the pre-London limits.
  • PRE-LONDON TRIGGER 02:00–05:00 — one limit gets triggered/swept → the day's
    high/low is SET → the DAILY bias is confirmed (sell-limit hit = short day; buy-
    limit hit = long day). This is the HTF→session bridge.
  • LONDON 00/01/02 and NEW YORK 07/08/09 — each runs RANGE (anchor 1H high/low) →
    SWEEP (one side taken) → CONTINUITY (trade toward the HTF OB, in confluence with
    the daily bias). Cheetah (250-pip) intra-session scalps.

Sweep the HIGH in a bearish context → sell toward the OB below; sweep the LOW in a
bullish context → buy toward the OB above. No sweep, or a sweep against bias = WAIT.
"""

from __future__ import annotations

from typing import List, Optional, Sequence

# Session phases in UTC-4: (range-anchor, sweep, continuity). TWO complementary sets
# — both run the same engine (anchor 1H high/low → next candle sweeps → 3rd runs the
# continuity to the HTF OB), catching different setups:
#   • AMD-aligned (anchored to the Venom window starts) — the HIGHER-TIMEFRAME setups.
#   • classic session-open sweeps (the original model) — the intraday scalps.
# On overlapping hours phase_for_hour prefers the AMD set (listed first).
SESSIONS = {
    # AMD-window-aligned → better HTF trade setups
    "asian":   {"anchor": 14, "sweep": 15, "continuity": 16},   # ACCUMULATION start
    "london":  {"anchor": 22, "sweep": 23, "continuity": 0},     # MANIPULATION start
    "newyork": {"anchor": 6,  "sweep": 7,  "continuity": 8},     # DISTRIBUTION start
    # classic session-open sweeps → intraday scalps
    "london_open": {"anchor": 0, "sweep": 1, "continuity": 2},
    "ny_open":     {"anchor": 7, "sweep": 8, "continuity": 9},
}
# The two groups, for callers that want to scan one family.
AMD_SESSIONS = ("asian", "london", "newyork")
OPEN_SESSIONS = ("london_open", "ny_open")
ASIAN_CBDR = (14, 20)          # the Asian CBDR box window (= system "cbdr" window)
CRT_START = 21                 # 1-5-9 CRT trend begins (UTC-4) = 01:00 UTC anchor
PRELONDON_TRIGGER = (2, 5)     # pre-London limits triggered → daily high/low set
ASIAN_RANGE = ASIAN_CBDR       # back-compat alias


def _hlc(bar):
    """(hour, o, h, l, c) — hour from dict 'hour'/'time' or tuple[0]."""
    if isinstance(bar, dict):
        h = bar.get("hour")
        if h is None and bar.get("time"):
            try:
                h = int(str(bar["time"])[11:13])
            except Exception:
                h = None
        return (h, float(bar["open"]), float(bar["high"]),
                float(bar["low"]), float(bar["close"]))
    return (bar[0], float(bar[1]), float(bar[2]), float(bar[3]), float(bar[4]))


def anchor_range(bars: Sequence, anchor_hour: int) -> Optional[dict]:
    """The high/low of the 1H candle at the session anchor hour (most recent)."""
    for bar in reversed(list(bars)):
        hr, o, h, l, c = _hlc(bar)
        if hr == anchor_hour:
            return {"hour": hr, "high": round(h, 2), "low": round(l, 2),
                    "mid": round((h + l) / 2, 2)}
    return None


def detect_sweep(range_hi: float, range_lo: float, bars_after: Sequence) -> dict:
    """Which side of the range got swept after the anchor, and whether price closed
    back inside (the reject that confirms the grab)."""
    swept_high = swept_low = False
    reclaim_high = reclaim_low = False
    for bar in bars_after:
        _hr, o, h, l, c = _hlc(bar)
        if h > range_hi:
            swept_high = True
            reclaim_high = c < range_hi
        if l < range_lo:
            swept_low = True
            reclaim_low = c > range_lo
    side = None
    if swept_high and not swept_low:
        side = "high"
    elif swept_low and not swept_high:
        side = "low"
    elif swept_high and swept_low:
        side = "both"            # both sides taken — wait for the decisive one
    return {"side": side, "swept_high": swept_high, "swept_low": swept_low,
            "reclaim": reclaim_high if side == "high" else reclaim_low if side == "low" else False}


def range_outcome(range_hi: float, range_lo: float, bars_after: Sequence,
                  approach_frac: float = 0.15) -> str:
    """One of the FOUR calculated outcomes at the range — the complete set price
    follows: sweep_high / sweep_low (took the liquidity) or failed_high / failed_low
    (approached the extreme but couldn't take it). 'both' / 'inside' otherwise."""
    if not bars_after:
        return "inside"
    rng = max(range_hi - range_lo, 1e-9)
    tol = rng * approach_frac
    hi = max(_hlc(b)[2] for b in bars_after)
    lo = min(_hlc(b)[3] for b in bars_after)
    swept_high, swept_low = hi > range_hi, lo < range_lo
    if swept_high and swept_low:
        return "both"
    if swept_high:
        return "sweep_high"
    if swept_low:
        return "sweep_low"
    failed_high = (range_hi - hi) <= tol      # got within tol of the high, didn't take
    failed_low = (lo - range_lo) <= tol
    if failed_high and not failed_low:
        return "failed_high"
    if failed_low and not failed_high:
        return "failed_low"
    return "inside"


# The four outcomes → (signal, kind, rationale). 2 SELL (high side), 2 BUY (low side).
OUTCOME_SETUP = {
    "sweep_high":  ("SELL", "reversal", "swept the high (buy-side grab) → reject down"),
    "failed_high": ("SELL", "continuation", "failed to take the high (lower high) → bearish"),
    "sweep_low":   ("BUY", "reversal", "swept the low (sell-side grab) → reject up"),
    "failed_low":  ("BUY", "continuation", "failed to take the low (higher low) → bullish"),
}


def outcome_call(outcome: str, htf_bias: Optional[str] = None,
                 venom: Optional[dict] = None,
                 ob_interacted: Optional[bool] = None) -> dict:
    """The 4-outcome setup call, folded with the HTF bias, the Venom AMD phase, and
    the HTF-OB gate. A sweep in a ×manipulation window with OB interaction = HIGH
    conviction; a setup with no OB interaction is held to WAIT."""
    setup = OUTCOME_SETUP.get(outcome)
    if not setup:
        return {"signal": "WAIT", "outcome": outcome,
                "note": f"{outcome} — no clean setup (both sides / inside)"}
    signal, kind, why = setup
    want = "short" if signal == "SELL" else "long"
    htf_ok = htf_bias in (want, None, "neutral")
    ob_ok = ob_interacted is not False          # None (unknown) or True passes; False blocks

    conviction, manip, aligned = "base", False, 0
    if venom:
        conf = venom.get("confluence", {}) or {}
        aligned = conf.get("timeframes_aligned", 0)
        manip = bool(conf.get("htf_manipulation_window")
                     or (venom.get("intraday", {}) or {}).get("phase") == "manipulation")
        if outcome.startswith("sweep") and manip and aligned >= 3:
            conviction = "HIGH (×manipulation sweep)"
        elif outcome.startswith("sweep") and manip:
            conviction = "elevated (manipulation sweep)"
        elif manip:
            conviction = "elevated (manipulation window)"

    fired = signal if (htf_ok and ob_ok) else "WAIT"
    reasons = []
    if not htf_ok:
        reasons.append(f"HTF bias {htf_bias} opposes")
    if not ob_ok:
        reasons.append("no HTF OB interaction — gated")
    return {
        "signal": fired, "outcome": outcome, "kind": kind, "why": why,
        "htf_confluence": htf_ok and htf_bias == want,
        "ob_interacted": ob_interacted, "conviction": conviction,
        "manipulation_window": manip, "timeframes_aligned": aligned,
        "note": (f"{outcome} → {signal} ({kind}); {why}"
                 + (f"; conviction {conviction}" if conviction != "base" else "")
                 + (f" — WAIT ({', '.join(reasons)})" if fired == "WAIT" else "")),
    }


def continuity_call(sweep_side: Optional[str], htf_bias: Optional[str]) -> dict:
    """The trade the sweep sets up, gated by HTF-OB confluence. Sweep the HIGH →
    sell toward the OB below (needs a non-bullish HTF); sweep the LOW → buy toward
    the OB above (needs a non-bearish HTF)."""
    if sweep_side == "high":
        ok = htf_bias in ("short", "neutral", None)
        return {"signal": "SELL" if ok else "WAIT",
                "confluence": htf_bias == "short",
                "note": ("swept the session HIGH (buy-side grab) → sell toward the HTF "
                         "OB below" if ok else "swept high but HTF bullish — stand down")}
    if sweep_side == "low":
        ok = htf_bias in ("long", "neutral", None)
        return {"signal": "BUY" if ok else "WAIT",
                "confluence": htf_bias == "long",
                "note": ("swept the session LOW (sell-side grab) → buy toward the HTF "
                         "OB above" if ok else "swept low but HTF bearish — stand down")}
    if sweep_side == "both":
        return {"signal": "WAIT", "confluence": False,
                "note": "both sides swept — wait for the decisive close"}
    return {"signal": "WAIT", "confluence": False, "note": "no sweep yet — await the grab"}


def cbdr_range(bars: Sequence) -> Optional[dict]:
    """The Asian CBDR box (14:00-20:00 UTC-4) high/low — the range whose ±SD are the
    pre-London buy limit (−SD) and sell limit (+SD)."""
    lo_h, hi_h = ASIAN_CBDR
    win = [_hlc(b) for b in bars if _hlc(b)[0] is not None and lo_h <= _hlc(b)[0] <= hi_h]
    if not win:
        return None
    hi = max(r[2] for r in win)
    lo = min(r[3] for r in win)
    return {"high": round(hi, 2), "low": round(lo, 2), "range": round(hi - lo, 2)}


def prelondon_daily_bias(cbdr: Optional[dict], trigger_bars: Sequence,
                         sd: float = 1.0) -> dict:
    """The DAILY bias — set by which pre-London limit (±SD of the CBDR box) gets
    triggered in the 02:00-05:00 window. Sell-limit (+SD) hit = short day; buy-limit
    (−SD) hit = long day. This is the HTF→session bridge."""
    if not cbdr:
        return {"bias": "neutral", "note": "no Asian CBDR box (14-20)"}
    rng = cbdr["high"] - cbdr["low"]
    sell_limit = round(cbdr["high"] + sd * rng, 2)      # +SD
    buy_limit = round(cbdr["low"] - sd * rng, 2)        # −SD
    lo_h, hi_h = PRELONDON_TRIGGER
    win = [_hlc(b) for b in trigger_bars
           if _hlc(b)[0] is not None and lo_h <= _hlc(b)[0] <= hi_h]
    hit_sell = any(r[2] >= sell_limit for r in win)
    hit_buy = any(r[3] <= buy_limit for r in win)
    if hit_sell and not hit_buy:
        bias, trig = "short", "sell-limit (+SD) hit"
    elif hit_buy and not hit_sell:
        bias, trig = "long", "buy-limit (−SD) hit"
    elif hit_sell and hit_buy:
        bias, trig = "neutral", "both limits swept — wait"
    else:
        bias, trig = "neutral", "no limit triggered yet"
    return {"bias": bias, "triggered": trig, "sell_limit": sell_limit,
            "buy_limit": buy_limit,
            "note": f"pre-London {trig} → daily bias {bias.upper()}"}


def asian_bias(bars: Sequence) -> dict:
    """Daily bias off the Asian CBDR box (14-20) + the pre-London (2-5) trigger."""
    cr = cbdr_range(bars)
    if not cr:
        return {"bias": "neutral", "note": "no Asian CBDR (14-20) bars"}
    db = prelondon_daily_bias(cr, bars)
    return {"bias": db["bias"], "asian_high": cr["high"], "asian_low": cr["low"],
            "cbdr_range": cr, "prelondon": db, "note": db["note"]}


def session_timeline() -> dict:
    """The daily anticipation chain — HTF box → CRT trend → pre-London trigger → sessions."""
    return {
        "asian_cbdr": "14:00-20:00 UTC-4 — box forms; ±SD = pre-London buy/sell limits",
        "crt_1_5_9": "21:00 UTC-4 (01:00 UTC anchor) — 1-5-9 trend begins toward the limits",
        "prelondon_trigger": "02:00-05:00 UTC-4 — a limit triggers → daily high/low set → daily bias",
        "london": "00/01/02 UTC-4 — range / sweep / continuity",
        "newyork": "07/08/09 UTC-4 — range / sweep / continuity",
        "note": "box → CRT trend → pre-London trigger sets the daily bias → session sweeps run to the HTF OB",
    }


def phase_for_hour(hour: int) -> Optional[dict]:
    """Which session + phase the UTC-4 hour is in (range / sweep / continuity)."""
    for name, s in SESSIONS.items():
        if hour == s["anchor"]:
            return {"session": name, "phase": "range"}
        if hour == s["sweep"]:
            return {"session": name, "phase": "sweep"}
        if hour == s["continuity"]:
            return {"session": name, "phase": "continuity"}
    if ASIAN_CBDR[0] <= hour <= ASIAN_CBDR[1]:
        return {"session": "asian_cbdr", "phase": "box_forming"}
    if hour >= CRT_START or hour < PRELONDON_TRIGGER[0]:
        return {"session": "crt", "phase": "trend_forming"}
    if PRELONDON_TRIGGER[0] <= hour <= PRELONDON_TRIGGER[1]:
        return {"session": "prelondon", "phase": "trigger"}
    return None


def bumblebee_scan(bars_1h: Sequence, now_hour: int, htf_bias: Optional[str] = None,
                   session: Optional[str] = None, ob_target: Optional[float] = None,
                   venom: Optional[dict] = None,
                   ob_interacted: Optional[bool] = None) -> dict:
    """Full Bumblebee read for the active (or given) session: the anchor range, the
    sweep, and the continuity call toward the HTF OB. Feed hour-tagged 1H bars."""
    sess = session
    if sess is None:
        ph = phase_for_hour(now_hour)
        sess = (ph or {}).get("session")
    ab = asian_bias(bars_1h)
    bias = htf_bias or (ab["bias"] if ab["bias"] != "neutral" else None)
    if sess not in SESSIONS:
        ph = phase_for_hour(now_hour) or {}
        return {"session": sess or "none", "asian_bias": ab, "htf_bias": htf_bias,
                "daily_bias": bias, "phase": ph.get("phase", "off-session"),
                "timeline": session_timeline(),
                "note": ("Asian CBDR box (14-20) → CRT (21) → pre-London trigger (2-5) "
                         "sets the daily bias; wait for the London/NY anchor")}
    cfg = SESSIONS[sess]
    rng = anchor_range(bars_1h, cfg["anchor"])
    if not rng:
        return {"session": sess, "asian_bias": ab, "htf_bias": bias,
                "note": f"no {sess} anchor ({cfg['anchor']}:00) candle yet"}
    # The sweep is the SWEEP-hour candle only — the continuity hour breaks the range
    # in the trade direction (continuation), which must not count as a second sweep.
    sweep_bars = [b for b in bars_1h if _hlc(b)[0] == cfg["sweep"]]
    if not sweep_bars:      # fall back to the bars between anchor and continuity
        sweep_bars = [b for b in bars_1h if _hlc(b)[0] is not None
                      and cfg["anchor"] < _hlc(b)[0] < cfg["continuity"]]
    sweep = detect_sweep(rng["high"], rng["low"], sweep_bars)
    call = continuity_call(sweep["side"], bias)          # legacy 2-outcome
    # The FOUR calculated outcomes (sweep/fail × high/low) folded with the Venom AMD
    # phase + the HTF-OB gate — the complete setup read.
    outcome = range_outcome(rng["high"], rng["low"], sweep_bars)
    oc = outcome_call(outcome, htf_bias=bias, venom=venom, ob_interacted=ob_interacted)
    target = ob_target
    return {
        "session": sess, "phase": (phase_for_hour(now_hour) or {}).get("phase"),
        "range": rng, "sweep": sweep, "htf_bias": bias, "asian_bias": ab,
        "continuity": call, "outcome": oc, "venom_folded": bool(venom),
        "ob_target": target,
        "scalp": "cheetah (≥250 pips) intra-session toward the HTF OB",
        "note": (f"{sess.upper()} {rng['low']}–{rng['high']}: {outcome} → {oc['signal']}"
                 + (f" · {oc['conviction']}" if oc.get("conviction", "base") != "base" else "")),
    }


def format_bumblebee(scan: dict) -> Optional[str]:
    """Telegram line when Bumblebee has a fired 4-outcome setup call."""
    oc = scan.get("outcome") or {}
    if oc.get("signal") not in ("BUY", "SELL"):
        return None
    icon = "🟢" if oc["signal"] == "BUY" else "🔴"
    rng = scan.get("range") or {}
    conv = oc.get("conviction", "base")
    tag = " ✅HTF" if oc.get("htf_confluence") else ""
    star = "  ⭐" if conv.startswith("HIGH") else ""
    return (f"🐝 *BUMBLEBEE — {scan['session'].upper()}*  {icon} {oc['signal']}{tag}{star}\n"
            f"   range {rng.get('low')}–{rng.get('high')}  ·  {oc['outcome']} ({oc.get('kind')})\n"
            f"   {oc['why']}"
            + (f"  ·  {conv}" if conv != "base" else "")
            + (f"  → OB {scan['ob_target']}" if scan.get("ob_target") else ""))
