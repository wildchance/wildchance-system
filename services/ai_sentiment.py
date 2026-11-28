"""AI market sentiment — headlines per asset scored by an LLM on NVIDIA's API.

Fills the flowchart's "MARKET SENTIMENT" box. For each instrument: pull the last
~48h of Google News headlines (free RSS), ask the model for a strict-JSON verdict
{score −1..+1, bias, confidence, drivers}, cache it, and (optionally) turn STRONG
readings into a Stage-0 macro-bias lock for the swing engine.

Env:
  NVIDIA_API_KEY          key from build.nvidia.com (OpenAI-compatible endpoint)
  NVIDIA_MODEL            default meta/llama-3.3-70b-instruct (3.1-8b reached end of life 2026-08-26)
  NVIDIA_FAST_MODELS      chain for many small calls (sentiment, Polymarket tagging)
  NVIDIA_DEEP_MODELS      chain for long reads (macro brief) — reasoning models first
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
NVIDIA_MODEL_SET = bool(config("NVIDIA_MODEL", default=""))          # an explicit pin goes first everywhere
NVIDIA_MODEL = config("NVIDIA_MODEL", default="meta/llama-3.3-70b-instruct")
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


# tried in order when the configured model is retired / not on this account (HTTP 404 / 400)
FALLBACK_MODELS = [m.strip() for m in str(config(
    "NVIDIA_FALLBACK_MODELS", default="meta/llama-3.3-70b-instruct,meta/llama-4-maverick-17b-128e-instruct,"
                                      "meta/llama-3.1-70b-instruct,mistralai/mistral-small-24b-instruct")).split(",") if m.strip()]
# Model chains per job (first available wins; a retired/missing model is skipped for 24 h):
#   fast — many small calls (headline sentiment, Polymarket question tagging)
#   deep — few long reads (macro brief): bigger reasoning models first
def _chain(env: str, default: str) -> List[str]:
    return [m.strip() for m in str(config(env, default=default)).split(",") if m.strip()]


MODEL_CHAINS = {
    "fast": _chain("NVIDIA_FAST_MODELS", "meta/llama-3.3-70b-instruct,openai/gpt-oss-20b,qwen/qwen3-32b,"
                                         "nvidia/nvidia-nemotron-nano-9b-v2"),
    "deep": _chain("NVIDIA_DEEP_MODELS", "openai/gpt-oss-120b,nvidia/nemotron-3-super-120b-a12b,"
                                         "deepseek-ai/deepseek-v3.1,meta/llama-3.3-70b-instruct"),
}
_dead: Dict[str, float] = {}                 # model → retry after (retired / not on this account)
DEAD_MODEL_S = 24 * 3600
LAST = {"ok": None, "status": None, "error": None, "model": None, "t": 0.0}
_down_until = {"t": 0.0}


_logged = {"key": None, "t": 0.0}
CONFIG_COOLDOWN_S = 6 * 3600      # a rejected key / unavailable model will not fix itself
TRANSIENT_COOLDOWN_S = 900        # network, 429, 5xx: try again in 15 min


def _note(ok: bool, status, error: Optional[str], model: Optional[str]) -> None:
    """Remember the outcome for /ops/ai; log a failure once per hour per distinct error."""
    import time as _t
    now = _t.time()
    LAST.update(ok=ok, status=status, error=(error or "")[:200] or None, model=model, t=now)
    if ok:
        _logged.update(key=None, t=0.0)
        return
    key = f"{status}:{(error or '')[:60]}"
    if key == _logged["key"] and now - _logged["t"] < 3600:
        return
    _logged.update(key=key, t=now)
    try:
        from services import errors
        errors.record("upstream_data", "nvidia ai", f"{status}: {error}"[:160])
    except Exception:
        pass


def models_for(role: str) -> List[str]:
    """NVIDIA_MODEL (if set) first, then the role's chain, then the generic fallbacks — no repeats."""
    import time as _t
    out: List[str] = []
    for m in ([NVIDIA_MODEL] if NVIDIA_MODEL_SET else []) + MODEL_CHAINS.get(role, []) + \
            [NVIDIA_MODEL] + FALLBACK_MODELS:
        if m and m not in out and _dead.get(m, 0) <= _t.time():
            out.append(m)
    return out


_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)


async def ask_model(messages: List[Dict], role: str = "fast") -> Optional[str]:
    """Chat completion on NVIDIA's API. Never raises; the reason for a failure is kept in LAST
    (shown at /ops/ai) and a failing API is skipped for 15 min so pages don't hang on it."""
    import time as _t
    if not NVIDIA_API_KEY:
        _note(False, "no key", "NVIDIA_API_KEY is not set on this server", None)
        return None
    if _t.time() < _down_until["t"]:
        return None
    for model in models_for(role):
        t0 = _t.time()
        try:
            async with httpx.AsyncClient(timeout=25) as c:
                r = await c.post(NVIDIA_URL, headers={"Authorization": f"Bearer {NVIDIA_API_KEY}"},
                                 json={"model": model, "messages": messages,
                                       "temperature": 0.1, "max_tokens": 300 if role == "fast" else 900})
        except Exception as e:
            _note(False, "network", f"{type(e).__name__}: {e}", model)
            _down_until["t"] = _t.time() + TRANSIENT_COOLDOWN_S
            return None
        if r.status_code == 200:
            try:
                txt = _THINK.sub("", r.json()["choices"][0]["message"]["content"] or "").strip()
            except Exception as e:
                _note(False, 200, f"unexpected reply: {e}", model)
                return None
            _note(True, 200, None, model)
            try:
                from services import ai_log
                await ai_log.record(role, model, messages, txt, (_t.time() - t0) * 1000)
            except Exception:
                pass
            return txt
        err = r.text[:200]
        if r.status_code == 410 or (r.status_code in (404, 400, 422) and "model" in err.lower()):
            _dead[model] = _t.time() + DEAD_MODEL_S
            continue                                  # retired (410 Gone) / unavailable model → try the next one
        _note(False, r.status_code, err, model)
        if r.status_code in (401, 403):
            _down_until["t"] = _t.time() + CONFIG_COOLDOWN_S
        elif r.status_code == 429 or r.status_code >= 500:
            _down_until["t"] = _t.time() + TRANSIENT_COOLDOWN_S
        return None
    _note(False, 404, "none of the configured models is available to this key", NVIDIA_MODEL)
    _down_until["t"] = _t.time() + CONFIG_COOLDOWN_S
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
