"""Polymarket prediction markets → per-asset macro sentiment (free Gamma API, no key).

Crowd probabilities on Fed moves, recession, tariffs, BTC/gold price targets… are
mapped to asset direction:
  1. RULES — transparent regex table (Fed cut ⇒ USD−, gold+, indices+, BTC+ …)
  2. NVIDIA LLM — optional, classifies relevant questions the rules don't match
Market signal = (P(yes) − 0.5)·2, weighted by log10(volume). Currency effects are
mapped to pairs (EURUSD = EUR − USD). Output −1 … +1 per asset.
"""

from __future__ import annotations

import json
import math
import re
import time
from typing import Dict, List, Optional

import httpx

GAMMA = "https://gamma-api.polymarket.com/markets"
ASSETS = ["XAUUSD", "XAGUSD", "BTCUSD", "USOIL", "US500", "NAS100", "US30", "EURUSD",
          "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD"]

# (pattern on the question, impact if YES). "USD"/"EUR"/… are currency effects.
RULES = [
    (r"\bfed\b.*\b(cut|lower|decrease)|rate cut", {"USD": -1, "XAUUSD": 1, "US500": 1, "NAS100": 1, "BTCUSD": 1}),
    (r"\bfed\b.*\b(hike|raise|increase)|rate hike", {"USD": 1, "XAUUSD": -1, "US500": -1, "NAS100": -1, "BTCUSD": -1}),
    (r"recession", {"US500": -1, "NAS100": -1, "US30": -1, "USOIL": -1, "XAUUSD": 1}),
    (r"(government )?shutdown", {"US500": -0.5, "XAUUSD": 0.5}),
    (r"tariff", {"US500": -0.5, "NAS100": -0.5, "XAUUSD": 0.5}),
    (r"\b(cpi|inflation)\b.*\b(above|higher|exceed|hot)", {"USD": 0.5, "US500": -0.5, "XAUUSD": 0.3}),
    (r"\b(bitcoin|btc)\b.*\b(above|reach|hit|ath|all[- ]time high)", {"BTCUSD": 1}),
    (r"\b(bitcoin|btc)\b.*\b(below|dip|drop|fall)", {"BTCUSD": -1}),
    (r"\bgold\b.*\b(above|reach|hit|record)", {"XAUUSD": 1}),
    (r"\bgold\b.*\b(below|drop|fall)", {"XAUUSD": -1}),
    (r"\b(oil|crude|wti)\b.*\b(above|reach|hit)|opec.*cut", {"USOIL": 1}),
    (r"\b(oil|crude|wti)\b.*\b(below|drop|fall)", {"USOIL": -1}),
    (r"\becb\b.*\bcut", {"EUR": -1}), (r"\becb\b.*\b(hike|raise)", {"EUR": 1}),
    (r"\bboe\b.*\bcut|bank of england.*cut", {"GBP": -1}),
    (r"\bboj\b.*\b(hike|raise)|bank of japan.*(hike|raise)", {"JPY": 1}),
    (r"\b(s&p|s&p 500|spx)\b.*\b(above|record|hit)", {"US500": 1}),
    (r"\bnasdaq\b.*\b(above|record|hit)", {"NAS100": 1}),
]
RELEVANT = re.compile(r"fed|fomc|rate|recession|inflation|cpi|tariff|shutdown|bitcoin|btc|"
                      r"gold|oil|crude|opec|ecb|boe|boj|s&p|nasdaq|dow|dollar|treasury|gdp", re.I)
_cache: Dict = {"t": 0.0, "data": None}


def _pairs(effects: Dict[str, float]) -> Dict[str, float]:
    """Currency effects → pair effects; direct asset effects pass through."""
    out = {k: v for k, v in effects.items() if k in ASSETS}
    for a in ASSETS:
        base, quote = a[:3], a[3:6]
        v = effects.get(base, 0) - effects.get(quote, 0)
        if a in ("XAUUSD", "XAGUSD", "BTCUSD"):          # USD-priced assets: USD− ⇒ up
            v = -effects.get("USD", 0) * 0.5
        if v:
            out[a] = out.get(a, 0) + v
    return out


def match_rules(question: str) -> Optional[Dict[str, float]]:
    q = question.lower()
    for pat, eff in RULES:
        if re.search(pat, q):
            return _pairs(eff)
    return None


def parse_market(m: Dict) -> Optional[Dict]:
    """Gamma market → {question, p_yes, volume, end}. outcomes/outcomePrices are JSON strings."""
    try:
        outcomes = json.loads(m.get("outcomes") or "[]")
        prices = [float(x) for x in json.loads(m.get("outcomePrices") or "[]")]
        if len(outcomes) != 2 or len(prices) != 2:
            return None
        i = [o.lower() for o in outcomes].index("yes")
        return {"question": m.get("question", ""), "p_yes": prices[i],
                "volume": float(m.get("volume") or m.get("volumeNum") or 0),
                "end": (m.get("endDate") or "")[:10], "slug": m.get("slug")}
    except (ValueError, TypeError):
        return None


def score_assets(markets: List[Dict]) -> Dict[str, Dict]:
    """markets: parsed + 'effects'. → {asset: {score, n, top}}."""
    acc: Dict[str, List] = {}
    for m in markets:
        eff = m.get("effects") or {}
        w = math.log10(1 + m["volume"])
        sig = (m["p_yes"] - 0.5) * 2
        for a, impact in eff.items():
            acc.setdefault(a, []).append((w * sig * impact, w * abs(impact), m))
    out = {}
    for a, rows in acc.items():
        den = sum(r[1] for r in rows)
        sc = max(-1.0, min(1.0, sum(r[0] for r in rows) / den)) if den else 0.0
        top = sorted(rows, key=lambda r: -abs(r[0]))[:3]
        out[a] = {"score": round(sc, 2), "markets": len(rows),
                  "top": [{"q": r[2]["question"][:110], "p_yes": r[2]["p_yes"]} for r in top]}
    return out


async def _llm_effects(questions: List[str]) -> List[Optional[Dict]]:
    from services import ai_sentiment as ai
    if not ai.NVIDIA_API_KEY or not questions:
        return [None] * len(questions)
    msgs = [{"role": "system", "content":
             "For each prediction-market question, give the price impact on these assets IF it "
             f"resolves YES, from -1 to 1: {', '.join(ASSETS)}. Omit unaffected assets. Reply ONLY "
             'a JSON array, one object per question in order, e.g. [{"XAUUSD":1,"USDJPY":-0.5}, {}].'},
            {"role": "user", "content": "\n".join(f"{i+1}. {q}" for i, q in enumerate(questions))}]
    txt = await ai.ask_model(msgs) or ""
    m = re.search(r"\[.*\]", txt, re.S)
    try:
        arr = json.loads(m.group(0)) if m else []
        return [({k: max(-1.0, min(1.0, float(v))) for k, v in d.items() if k in ASSETS}
                 if isinstance(d, dict) else None) for d in arr][:len(questions)] + \
               [None] * max(0, len(questions) - len(arr))
    except (ValueError, TypeError):
        return [None] * len(questions)


async def fetch(limit: int = 300) -> Dict:
    if _cache["data"] and time.time() - _cache["t"] < 1800:
        return _cache["data"]
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(GAMMA, params={"active": "true", "closed": "false", "limit": limit,
                                           "order": "volume24hr", "ascending": "false"})
        raw = r.json() if r.status_code == 200 else []
    except Exception:
        raw = []
    parsed = [p for p in (parse_market(m) for m in raw) if p and RELEVANT.search(p["question"])
              and p["volume"] >= 10000]
    unmatched = []
    for p in parsed:
        p["effects"] = match_rules(p["question"])
        if p["effects"] is None:
            unmatched.append(p)
    for p, eff in zip(unmatched[:20], await _llm_effects([u["question"] for u in unmatched[:20]])):
        p["effects"], p["by"] = eff, "llm"
    used = [p for p in parsed if p.get("effects")]
    data = {"ok": bool(raw), "markets_seen": len(raw), "relevant": len(parsed),
            "used": len(used), "assets": score_assets(used),
            "as_of": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime())}
    if raw:
        _cache.update(t=time.time(), data=data)
    return data
