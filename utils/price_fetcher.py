"""Centralized price fetching with Redis cache (Polygon preferred) + fallbacks.
Updated to support real-time streaming while keeping your original logic."""
import re
from typing import Optional
import httpx
import aioredis
from decouple import config
from utils.logger import logger  # Replace with print() if no logger

# Config
TWELVEDATA_KEY = config("TWELVEDATA_API_KEY", default=None)
REDIS_URL = config("REDIS_URL", default="redis://localhost:6379")

BASE_URL_TWELVE = "https://api.twelvedata.com/price"
BASE_URL_FRANKFURTER = "https://api.frankfurter.app/latest"


async def get_redis():
    """Get Redis connection"""
    try:
        return await aioredis.from_url(REDIS_URL, decode_responses=True)
    except Exception:
        return None


async def get_latest_price(symbol: str) -> Optional[float]:
    """
    Primary: Try Redis cache (from Polygon stream)
    Fallback: Your original TwelveData + Frankfurter + Metals logic
    """
    # 1. Try Redis cache first (real-time from Polygon)
    redis = await get_redis()
    if redis:
        try:
            price_str = await redis.get(f"tick:{symbol}")
            if price_str:
                await redis.close()
                return float(price_str)
        except Exception:
            pass
        finally:
            if redis:
                await redis.close()

    # 2. Fallback to your original methods
    return await get_fallback_price(symbol)


async def get_fallback_price(symbol: str) -> Optional[float]:
    """Your original logic — kept intact"""
    pair_clean = symbol.strip().upper().replace("/", "")

    # TwelveData
    price = await _twelvedata_price(pair_clean)
    if price is not None:
        return price

    # Frankfurter (FX)
    if re.fullmatch(r"[A-Z]{6}", pair_clean):
        base, quote = pair_clean[:3], pair_clean[3:]
        price = await _frankfurter_price(base, quote)
        if price is not None:
            return price

    # Metals
    if pair_clean.startswith("XAU") or pair_clean.startswith("XAG"):
        return await _metals_price(pair_clean)

    return None


# ==================== Your Original Helpers (Unchanged) ====================

async def _frankfurter_price(base: str, quote: str) -> Optional[float]:
    url = f"{BASE_URL_FRANKFURTER}?from={base}&to={quote}"
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return None
            data = resp.json()
            return float(data["rates"].get(quote))
    except Exception:
        return None


async def _twelvedata_price(symbol: str) -> Optional[float]:
    if not TWELVEDATA_KEY:
        return None
    params = {"symbol": symbol, "apikey": TWELVEDATA_KEY}
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(BASE_URL_TWELVE, params=params)
            data = r.json()
            if "price" in data:
                return float(data["price"])
    except Exception:
        return None
    return None


async def _metals_price(pair_clean: str) -> Optional[float]:
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get("https://api.metals.live/v1/spot")
            arr = r.json()
            for item in arr:
                if isinstance(item, dict):
                    sym = item.get("symbol") or item.get("metal") or item.get("pair")
                    price = item.get("price") or item.get("last") or item.get("value")
                    if sym and sym.upper() == pair_clean:
                        return float(price)
    except Exception:
        return None
    return None


# Convenience wrapper
async def get_latest_price_or_fallback(symbol: str, default: float = 0.0) -> float:
    price = await get_latest_price(symbol)
    return price if price is not None else default
