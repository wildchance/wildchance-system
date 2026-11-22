"""WildChance DNB Probability & Ladder pipeline — the 4-step watchlist audit.

  STEP 1  Probability & CHOP-TRAP filter → Adjusted Expectancy Score (AES ≈ "True
          DNB %") + Breakeven-Protected Survival Rate (BPSR ≈ "AH +0.5 %"); ADX<20,
          range-bound or high-fakeout setups are [CHOP-TRAP] and ranked below every
          trending setup. Ranked R01 (highest conviction) … R_N (highest entropy).
  STEP 2  Classification: ANCHOR (top-8 trending, R:R ≥ 1.8) · RANGE (demoted chop,
          fades only) · WATCH (trending but R:R < 1.8) · OUTLIER (bottom 20%, cut).
  STEP 3  Session / news / lunar overlay on survivors: [CLUSTER-RISK] when a currency
          is shared by ≥ 2 survivors, [NEWS] for the 13 tier-1 releases, [LUNAR-SHIFT].
  STEP 4  ±1σ ladder + prop-firm sizing: compact (8–13) 8 core → +1 fade → +1 runner;
          full (15–17) 10 → 11 → 12 → 13. Prop mode PAUSES news setups (no straddles
          around red news — most firms restrict it) and caps concurrent positions.

Every input field is optional except symbol/side; missing data is reported, never
invented. Probabilities are the caller's (backtest win-rate, model, etc.).
"""

from __future__ import annotations

import math
import re
from typing import Dict, Iterable, List, Optional

# The 13 releases from the WildChance flow chart (CHECK NEWS column) + central banks.
NEWS_EVENTS = ("PPI", "CPI", "JOLTS", "UNEMPLOYMENT CLAIMS", "UNEMPLOYMENT RATE", "NFP",
               "AVERAGE HOURLY EARNINGS", "RETAIL SALES", "ISM", "INDUSTRIAL PRODUCTION",
               "CONSUMER SPENDING", "CONSUMER CONFIDENCE", "GDP", "FOMC", "RATE DECISION")

MIN_RR_ANCHOR = 1.8
MIN_ADX = 20.0
MAX_ANCHORS = 8
OUTLIER_FRACTION = 0.20

_CCY = {"XAU", "XAG", "USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF", "CNH"}
_INDEX_CCY = {"US30": "USD", "US500": "USD", "SPX": "USD", "NAS100": "USD", "NDX": "USD",
              "US100": "USD", "DXY": "USD", "USOIL": "USD", "WTI": "USD", "BTC": "USD",
              "GER40": "EUR", "DE40": "EUR", "UK100": "GBP", "JP225": "JPY"}


def currencies(symbol: str) -> List[str]:
    s = re.sub(r"[^A-Z0-9]", "", (symbol or "").upper())
    if s in _INDEX_CCY:
        return [_INDEX_CCY[s]]
    if len(s) >= 6 and s[:3] in _CCY and s[3:6] in _CCY:
        return [s[:3], s[3:6]]
    for k, v in _INDEX_CCY.items():
        if s.startswith(k):
            return [v]
    return []


def _side(v: str) -> int:
    v = (v or "").upper()
    return 1 if v in ("BUY", "LONG") else -1 if v in ("SELL", "SHORT") else 0


def score_setup(s: Dict) -> Dict:
    """STEP 1 for one setup. Returns AES (R per trade, net of costs), BPSR, chop flag."""
    p = s.get("win_rate")
    rr = s.get("rr")
    missing = [k for k in ("win_rate", "rr", "adx") if s.get(k) is None]
    p = 0.40 if p is None else float(p)
    rr = 1.0 if rr is None else float(rr)
    cost_r = float(s.get("cost_r", 0.0))                # spread+commission+swap in R
    adx = s.get("adx")
    raw_exp = p * rr - (1 - p) - cost_r                 # expectancy in R
    trend = 1.0 if adx is None else max(0.5, min(float(adx), 40.0) / 25.0)
    aes = raw_exp * trend
    be_rescue = float(s.get("be_rescue", 0.2))          # share of losers saved at BE after +1R
    bpsr = p + (1 - p) * be_rescue
    chop_reasons = []
    if adx is not None and float(adx) < MIN_ADX:
        chop_reasons.append(f"ADX {float(adx):.1f} < {MIN_ADX:.0f}")
    if s.get("range_bound"):
        chop_reasons.append("range-bound")
    if s.get("fakeout_prob") is not None and float(s["fakeout_prob"]) > 0.5:
        chop_reasons.append(f"fakeout {float(s['fakeout_prob']):.0%}")
    return {"aes_r": round(aes, 3), "expectancy_r": round(raw_exp, 3),
            "bpsr": round(bpsr, 3), "chop_trap": bool(chop_reasons),
            "chop_reasons": chop_reasons, "missing": missing}


def _news_hits(s: Dict) -> List[str]:
    out = []
    for ev in s.get("news", []) or []:
        name = (ev if isinstance(ev, str) else ev.get("event", "")).upper()
        for key in NEWS_EVENTS:
            if key in name:
                out.append(key)
                break
    return sorted(set(out))


def _ladder(entry: Optional[float], stop: Optional[float], sigma: Optional[float],
            side: int, runner: bool) -> Dict:
    if entry is None or sigma is None or side == 0:
        return {"note": "send entry + sigma (expected 1σ move, e.g. ATR) for the ladder"}
    entry, sigma = float(entry), float(sigma)
    out = {"tp_core_1sigma": round(entry + side * sigma, 5)}
    if runner:
        out["tp_runner_3sigma"] = round(entry + side * 3 * sigma, 5)
    if stop is not None:
        risk = abs(entry - float(stop))
        if risk > 0:
            out["tp1_1R"] = round(entry + side * risk, 5)
            out["core_rr"] = round(sigma / risk, 2)
            if sigma / risk < MIN_RR_ANCHOR:
                out["warning"] = f"±1σ target is only {sigma / risk:.2f}R (< {MIN_RR_ANCHOR})"
    return out


def run_audit(watchlist: List[Dict], risk_pct: float = 0.5, max_concurrent: int = 3,
              prop_mode: bool = True, lunar: Optional[Dict] = None,
              use_lunar: bool = False, macro_bias: str = "BOTH") -> Dict:
    """Run all 4 steps over a watchlist.

    watchlist items: {symbol, side, win_rate, rr, adx, cost_r, range_bound,
    fakeout_prob, news:[...], entry, stop, sigma, session}.
    lunar: a ``lunar.lunar_state`` dict (only used when ``use_lunar``).
    macro_bias: BOTH | BUY_ONLY | SELL_ONLY (Stage-0 COT/currency-meter lock).
    """
    rows = []
    for raw in watchlist:
        s = dict(raw)
        sc = score_setup(s)
        rows.append({"symbol": s.get("symbol"), "side": (s.get("side") or "").upper(),
                     **sc, "_in": s})

    # STEP 1 — rank: trending first by AES, chop-traps after (also by AES).
    rows.sort(key=lambda r: (r["chop_trap"], -r["aes_r"]))
    n = len(rows)
    for i, r in enumerate(rows, start=1):
        r["rank"] = f"R{i:02d}"

    # STEP 2 — classify.
    n_out = math.floor(n * OUTLIER_FRACTION) if n >= 5 else 0
    anchors = 0
    for i, r in enumerate(rows):
        rr = r["_in"].get("rr")
        if i >= n - n_out or r["aes_r"] <= 0:
            r["class"] = "OUTLIER"
        elif r["chop_trap"]:
            r["class"] = "RANGE"
        elif rr is not None and float(rr) >= MIN_RR_ANCHOR and anchors < MAX_ANCHORS:
            r["class"] = "ANCHOR"
            anchors += 1
        else:
            r["class"] = "WATCH"

    # Stage-0 macro lock + optional lunar gate.
    bias = (macro_bias or "BOTH").upper()
    for r in rows:
        r["flags"] = []
        side = _side(r["side"])
        if bias == "BUY_ONLY" and side < 0 or bias == "SELL_ONLY" and side > 0:
            r["flags"].append("[MACRO-LOCK]")
        if use_lunar and lunar:
            want = 1 if lunar.get("allowed_direction") == "BUY" else -1
            if side and side != want and r["class"] != "RANGE":
                r["flags"].append("[LUNAR-MISMATCH]")
            if lunar.get("in_observation_window"):
                r["flags"].append("[LUNAR-SHIFT]")

    # STEP 3 — clusters + news on survivors.
    survivors = [r for r in rows if r["class"] != "OUTLIER"]
    ccy_count: Dict[str, int] = {}
    for r in survivors:
        for c in currencies(r["symbol"] or ""):
            ccy_count[c] = ccy_count.get(c, 0) + 1
    for r in survivors:
        shared = [c for c in currencies(r["symbol"] or "") if ccy_count.get(c, 0) >= 2]
        if shared:
            r["flags"].append("[CLUSTER-RISK]")
            r["cluster_ccy"] = shared
        hits = _news_hits(r["_in"])
        if hits:
            r["flags"].append("[NEWS]")
            r["news_hits"] = hits

    # STEP 4 — ladder + sizing + execution queue.
    mode = ("compact" if 8 <= len(survivors) <= 13 else
            "full" if len(survivors) >= 15 else "small")
    tradable = [r for r in survivors if r["class"] in ("ANCHOR", "RANGE", "WATCH")
                and "[MACRO-LOCK]" not in r["flags"]
                and "[LUNAR-MISMATCH]" not in r["flags"]]
    paused = []
    if prop_mode:
        paused = [r for r in tradable if "[NEWS]" in r["flags"]]
        tradable = [r for r in tradable if "[NEWS]" not in r["flags"]]
    core_n = 8 if mode == "compact" else 10 if mode == "full" else len(tradable)
    core = [r for r in tradable if r["class"] == "ANCHOR"][:core_n]
    fades = [r for r in tradable if r["class"] == "RANGE"]
    ladder: List[Dict] = [{"tier": "CORE", "rank": r["rank"]} for r in core]
    if mode == "compact":
        if fades:
            ladder.append({"tier": "+1 CONTRARIAN FADE", "rank": fades[0]["rank"]})
        if core:
            ladder.append({"tier": "+1 RUNNER (±3σ)", "rank": core[0]["rank"]})
    elif mode == "full":
        extra = [r for r in tradable if r not in core][:3]
        for k, r in enumerate(extra, start=11):
            ladder.append({"tier": f"SCALE → {k}", "rank": r["rank"]})
        if not prop_mode:
            news = [r for r in survivors if "[NEWS]" in r["flags"]][:2]
            for r in news:
                ladder.append({"tier": "BRACKET / STRADDLE (news)", "rank": r["rank"]})
    runner_rank = core[0]["rank"] if core else None

    in_ladder = {x["rank"] for x in ladder}
    paused_ids = {id(r) for r in paused}
    queue_pos = 0
    for r in rows:
        s = r.pop("_in")
        mult = 1.0
        if "[LUNAR-SHIFT]" in r["flags"]:
            mult *= 0.5
        if r["class"] == "RANGE":
            mult *= 0.5                                  # fades get half size
        if "[CLUSTER-RISK]" in r["flags"]:
            mult *= 0.5                                  # correlated exposure
        r["risk_pct"] = round(risk_pct * mult, 3) if r["rank"] in in_ladder else 0.0
        r["targets"] = _ladder(s.get("entry"), s.get("stop"), s.get("sigma"),
                               _side(r["side"]), r["rank"] == runner_rank)
        if id(r) in paused_ids:
            r["action"] = "PAUSE (red news — flat)"
        elif r["rank"] in in_ladder:
            queue_pos += 1
            r["action"] = "EXECUTE" if queue_pos <= max_concurrent else f"QUEUE #{queue_pos - max_concurrent}"
        elif r["class"] == "OUTLIER":
            r["action"] = "CUT"
        else:
            r["action"] = "SKIP"

    live_risk = sum(r["risk_pct"] for r in rows if r["action"] == "EXECUTE")
    return {
        "mode": mode, "setups": n, "survivors": len(survivors),
        "anchors": anchors, "risk_pct_base": risk_pct, "max_concurrent": max_concurrent,
        "prop_mode": prop_mode, "macro_bias": bias, "lunar_used": bool(use_lunar and lunar),
        "open_risk_pct_if_all_execute": round(live_risk, 3),
        "ladder": ladder, "ranked": rows,
    }


def calibration(records: Iterable[Dict]) -> Dict:
    """Weekly calibration: Brier score, win rate, expectancy.

    records: [{prob: 0..1 forecast, won: bool, r: realised R (optional)}]."""
    recs = [r for r in records if r.get("prob") is not None and r.get("won") is not None]
    if not recs:
        return {"n": 0}
    brier = sum((float(r["prob"]) - (1.0 if r["won"] else 0.0)) ** 2 for r in recs) / len(recs)
    wins = sum(1 for r in recs if r["won"])
    rs = [float(r["r"]) for r in recs if r.get("r") is not None]
    mean_p = sum(float(r["prob"]) for r in recs) / len(recs)
    return {"n": len(recs), "brier": round(brier, 4),
            "brier_vs_coinflip": round(0.25 - brier, 4),
            "win_rate": round(wins / len(recs), 3), "mean_forecast": round(mean_p, 3),
            "overconfident": mean_p - wins / len(recs) > 0.05,
            "expectancy_r": round(sum(rs) / len(rs), 3) if rs else None}
