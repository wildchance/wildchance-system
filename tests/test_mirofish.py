"""Unit tests for the MiroFish adapter -- no live MiroFish instance required."""

import asyncio
import time

import pytest

from services.mirofish_service import parse_score, _is_fresh, _cache, CACHE_TTL


# ---------------------------------------------------------------------------
# parse_score
# ---------------------------------------------------------------------------

def test_parse_score_bullish():
    md = " ".join(["bullish", "buy", "long", "rally", "upside"] * 4 + ["bearish"])
    r = parse_score(md)
    assert r["bias"] == "bullish"
    assert r["score"] >= 65


def test_parse_score_bearish():
    md = " ".join(["bearish", "sell", "short", "decline", "breakdown"] * 4 + ["bullish"])
    r = parse_score(md)
    assert r["bias"] == "bearish"
    assert r["score"] <= 35


def test_parse_score_neutral():
    md = "bullish bearish buy sell rally decline"
    r = parse_score(md)
    assert r["bias"] == "neutral"
    assert 36 <= r["score"] <= 64


def test_parse_score_empty():
    r = parse_score("")
    assert "score" in r
    assert "bias" in r


# ---------------------------------------------------------------------------
# cache freshness
# ---------------------------------------------------------------------------

def test_cache_fresh_within_ttl():
    _cache["XX/YY"] = {"score": 50, "bias": "neutral", "cached_at": time.time()}
    assert _is_fresh("XX/YY")
    del _cache["XX/YY"]


def test_cache_stale_after_ttl():
    _cache["XX/YY"] = {"score": 50, "bias": "neutral",
                       "cached_at": time.time() - CACHE_TTL - 1}
    assert not _is_fresh("XX/YY")
    del _cache["XX/YY"]


def test_cache_miss():
    _cache.pop("MISSING/PAIR", None)
    assert not _is_fresh("MISSING/PAIR")


# ---------------------------------------------------------------------------
# score() with no MIROFISH_BASE_URL
# ---------------------------------------------------------------------------

def test_score_returns_none_when_unconfigured(monkeypatch):
    import services.mirofish_service as svc
    monkeypatch.setattr(svc, "MIROFISH_BASE_URL", None)
    result = asyncio.get_event_loop().run_until_complete(svc.score("EUR/USD"))
    assert result is None


def test_score_returns_cache_when_fresh(monkeypatch):
    import services.mirofish_service as svc
    monkeypatch.setattr(svc, "MIROFISH_BASE_URL", "http://fake-mirofish")
    entry = {"score": 72, "bias": "bullish", "cached_at": time.time()}
    svc._cache["EUR/USD"] = entry
    result = asyncio.get_event_loop().run_until_complete(svc.score("EUR/USD"))
    assert result == entry
    del svc._cache["EUR/USD"]


# ---------------------------------------------------------------------------
# status()
# ---------------------------------------------------------------------------

def test_status_not_configured(monkeypatch):
    import services.mirofish_service as svc
    monkeypatch.setattr(svc, "MIROFISH_BASE_URL", None)
    result = asyncio.get_event_loop().run_until_complete(svc.status())
    assert result["configured"] is False
    assert result["reachable"] is None
