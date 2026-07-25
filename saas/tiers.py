"""SaaS tier ladder — the pricing model, feature gates, and limits (pure).

Retail is capped at $499/mo (Elite); Institutional is custom/uncapped. Billing is
usage-metered on LINKED ACCOUNTS + branch depth, not seats. Cross-border: prices are
USD anchors — localise with gold.account_network.currency_deposits for PPP pricing.
"""

from __future__ import annotations

from typing import List, Optional

# Ordered low → high. `rank` gates access; `price_usd` is the monthly retail anchor.
TIERS = [
    {"key": "scout",         "name": "Scout (Free)",   "rank": 0, "price_usd": 0,
     "accounts": 0, "features": ["digest", "dxy_lock", "regime"],
     "blurb": "daily Kingdom digest (delayed), DXY lock state, read-only regime"},
    {"key": "trader",        "name": "Trader",         "rank": 1, "price_usd": 49,
     "accounts": 1, "features": ["signals", "retracement", "zones", "recon", "scorecard"],
     "blurb": "live signals, retracement/zones/recon alerts, 1 linked account, scorecard"},
    {"key": "pro",           "name": "Pro",            "rank": 2, "price_usd": 149,
     "accounts": 3, "features": ["kingdom", "volatility", "intermarket", "trap", "backtest"],
     "blurb": "+ full Kingdom report, volatility/intermarket/trap, 3 accounts, backtests"},
    {"key": "fleet",         "name": "Fleet",          "rank": 3, "price_usd": 349,
     "accounts": 5, "features": ["fleet", "prop", "network", "mt5_bridge", "optimus", "bumblebee", "venom"],
     "blurb": "+ 5-account fan-out, prop plans, network D/W/M grid, MT5 bridge"},
    {"key": "elite",         "name": "Elite",          "rank": 4, "price_usd": 499,
     "accounts": 10, "features": ["all_branches", "priority_alerts", "api", "custom_cbdr"],
     "blurb": "+ all 14 branches, priority alerts, custom CBDR automation, API access"},
    {"key": "institutional", "name": "Institutional",  "rank": 5, "price_usd": None,
     "accounts": 999, "features": ["white_label", "multi_asset", "dedicated_feeds", "sla", "seats"],
     "blurb": "custom (uncapped) — white-label, multi-asset, dedicated feeds, SLA, seats"},
]

_BY_KEY = {t["key"]: t for t in TIERS}
RETAIL_CAP_USD = 499


def get_tier(key: str) -> Optional[dict]:
    return _BY_KEY.get((key or "").lower())


def rank_of(key: str) -> int:
    t = get_tier(key)
    return t["rank"] if t else -1


def tier_allows(user_tier: str, min_tier: str) -> bool:
    """Does ``user_tier`` meet or exceed ``min_tier``?"""
    return rank_of(user_tier) >= rank_of(min_tier)


def has_feature(user_tier: str, feature: str) -> bool:
    """Cumulative — a tier has its own features plus every lower tier's."""
    ur = rank_of(user_tier)
    if ur < 0:
        return False
    for t in TIERS:
        if t["rank"] <= ur and feature in t["features"]:
            return True
    return False


def account_limit(user_tier: str) -> int:
    t = get_tier(user_tier)
    return t["accounts"] if t else 0


def catalog() -> List[dict]:
    return [{k: t[k] for k in ("key", "name", "rank", "price_usd", "accounts", "blurb")}
            for t in TIERS]
