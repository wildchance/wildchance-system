"""AI market sentiment — headlines per asset scored by an LLM on NVIDIA's API.

Fills the flowchart's "MARKET SENTIMENT" box. For each instrument: pull the last
~48h of Google News headlines (free RSS), ask the model for a strict-JSON verdict
{score −1..+1, bias, confidence, drivers}, cache it, and (optionally) turn STRONG
readings into a Stage-0 macro-bias lock for the swing engine.

Env:
  NVIDIA_API_KEY          key from build.nvidia.com (OpenAI-compatible endpoint)
  NVIDIA_MODEL            default meta/llama-3.1-8b-instruct (cheap/fast)
  SENTIMENT_AUTO_BIAS     false → shown as context only; true → |score| ≥ 0.5 with
                          confidence ≥ 0.6 sets SELL_ONLY / BUY_ONLY where you have
                          NOT set MACRO_BIAS yourself (manual always wins)
  SENTIMENT_TTL_MIN       cache minutes (default 180)

Headline sentiment is noisy and easily one-sided; it's a confluence input, never a
trigger on its own — hence auto-bias is opt-in and needs a strong, confident read.
"""

from __future__ import annotations

import json
import re
import time
from typing import Dict, List, Optional

import httpx
from decouple import config

NVIDIA_API_KEY = config("NVIDIA_API_KEY", default=None)
NVIDIA_MODEL = config("NVIDIA_MODEL", default="meta/llama-3.1-8b-instruct")
NVIDIA_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
AUTO_BIAS = str(config("SENTIMENT_AUTO_BIAS", default="false")).strip().lower() in (
    "1", "true", "yes", "on")
TTL_MIN = float(config("SENTIMENT_TTL_MIN", default="180"))
STRONG_SCORE, MIN_CONF = 0.5, 0.6

# What to search for each instrument (news language, not tickers).
QUERIES = {
    "XAUUSD": "gold price", "XAGUSD": "silver price", "BTCUSD": "bitcoin price",
    "USOIL": "crude oil price OPEC", "US500": "S&P 500 stocks", "NAS100": "Nasdaq tech stocks",
    "US30": "Dow Jones", "EURUSD": "euro dollar ECB", "GBPUSD": "pound sterling Bank of England",
    "USDJPY": "yen Bank of Japan dollar", "AUDUSD": "Australian dollar RBA",
    "USDCAD": "Canadian dollar oil", "USDCHF": "Swiss franc SNB", "NZDUSD": "New Zealand dollar RBNZ",
    "GBPJPY": "pound yen", "EURJPY": "euro yen", "DXY": "US dollar index Fed",
}

_cache: Dict[str, Dict] = {}


def build_messages(symbol: str, headlines: List[str]) -> List[Dict]:
    """Prompt: strict JSON, direction for THIS instrument (e.g. USDJPY up = USD stronger)."""
    lines = "\n".join(f"- {h}" for h in headlines[:40])
    return [
        {"role": "system", "content":
            "You are a markets analyst. Judge the near-term (1-2 week) price direction of "
            "the instrument implied by the headlines. Reply with ONLY a JSON object: "
            '{"score": number from -1 (very bearish) to 1 (very bullish), '
            '"confidence": number 0-1, "drivers": [up to 3 short strings]}. '
            "If headlines are mixed, irrelevant or few, use a score near 0 and low confidence."},
        {"role": "user", "content": f"Instrument: {symbol}\nHeadlines (newest first):\n{lines}"},
    ]


def parse_verdict(text: str) -> Optional[Dict]:
    """Pull the JSON object out of a model reply; clamp and label it. None if unusable."""
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
        score = max(-1.0, min(1.0, float(d.get("score", 0))))
        conf = max(0.0, min(1.0, float(d.get("confidence", 0))))
    except (ValueError, TypeError):
        return None
    drivers = [str(x)[:120] for x in (d.get("drivers") or [])][:3]
    bias = "BULLISH" if score >= 0.2 else "BEARISH" if score <= -0.2 else "NEUTRAL"
    return {"score": round(score, 2), "confidence": round(conf, 2), "bias": bias,
            "drivers": drivers}


def lock_from(verdict: Optional[Dict]) -> Optional[str]:
    """Strong + confident verdict → macro lock; anything weaker → None (no lock)."""
    if not verdict or verdict.get("confidence", 0) < MIN_CONF:
        return None
    if verdict["score"] >= STRONG_SCORE:
        return "BUY_ONLY"
    if verdict["score"] <= -STRONG_SCORE:
        return "SELL_ONLY"
    return None


async def headlines(symbol: str) -> List[str]:
    from services.free_macro_feeds import _parse_rss_titles
    q = QUERIES.get(symbol.upper().replace("/", ""), symbol)
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True,
                                     headers={"User-Agent": "Mozilla/5.0"}) as c:
            r = await c.get("https://news.google.com/rss/search",
                            params={"q": f"{q} when:2d", "hl": "en-US", "gl": "US",
                                    "ceid": "US:en"})
        return _parse_rss_titles(r.text) if r.status_code == 200 else []
    except Exception:
        return []


async def ask_model(messages: List[Dict]) -> Optional[str]:
    if not NVIDIA_API_KEY:
        return None
    try:
        async with httpx.AsyncClient(timeout=40) as c:
            r = await c.post(NVIDIA_URL, headers={"Authorization": f"Bearer {NVIDIA_API_KEY}"},
                             json={"model": NVIDIA_MODEL, "messages": messages,
                                   "temperature": 0.1, "max_tokens": 300})
        if r.status_code != 200:
            return None
        return r.json()["choices"][0]["message"]["content"]
    except Exception:
        return None


async def score(symbol: str, force: bool = False) -> Dict:
    sym = symbol.upper().replace("/", "")
    hit = _cache.get(sym)
    if hit and not force and time.time() - hit["_t"] < TTL_MIN * 60:
        return {k: v for k, v in hit.items() if k != "_t"}
    if not NVIDIA_API_KEY:
        return {"symbol": sym, "status": "OFF", "reason": "set NVIDIA_API_KEY"}
    heads = await headlines(sym)
    if len(heads) < 3:
        return {"symbol": sym, "status": "NO_NEWS", "headlines": len(heads)}
    verdict = parse_verdict(await ask_model(build_messages(sym, heads)) or "")
    if not verdict:
        return {"symbol": sym, "status": "MODEL_ERROR", "headlines": len(heads)}
    out = {"symbol": sym, "status": "OK", **verdict, "lock": lock_from(verdict),
           "headlines": len(heads), "sample": heads[:3], "model": NVIDIA_MODEL,
           "as_of": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime())}
    _cache[sym] = {**out, "_t": time.time()}
    return out


def cached_locks() -> Dict[str, str]:
    """Auto macro locks from fresh cached verdicts (only when SENTIMENT_AUTO_BIAS)."""
    if not AUTO_BIAS:
        return {}
    now = time.time()
    return {s: v["lock"] for s, v in _cache.items()
            if v.get("lock") and now - v["_t"] < TTL_MIN * 60}
