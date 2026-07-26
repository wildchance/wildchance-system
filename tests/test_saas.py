"""SaaS wrapper — tiers, feature gating, auth path map, pricing localisation."""

import pytest

from saas import tiers as st
from saas import auth as sa


# --- tiers --------------------------------------------------------------------

def test_tier_ladder_ordered_and_capped():
    ranks = [t["rank"] for t in st.TIERS]
    assert ranks == sorted(ranks)
    assert st.RETAIL_CAP_USD == 499
    # elite is the retail cap; institutional is custom (None)
    assert st.get_tier("elite")["price_usd"] == 499
    assert st.get_tier("institutional")["price_usd"] is None


def test_tier_allows_and_features_cumulative():
    assert st.tier_allows("fleet", "pro") is True
    assert st.tier_allows("trader", "fleet") is False
    # cumulative: fleet has its own + all lower features
    assert st.has_feature("fleet", "signals") is True     # from trader
    assert st.has_feature("fleet", "optimus") is True     # its own
    assert st.has_feature("pro", "optimus") is False      # fleet-only
    assert st.account_limit("fleet") == 5


# --- path → tier gate ---------------------------------------------------------

def test_tier_for_path_longest_prefix():
    assert sa.tier_for_path("/gold/venom") == "fleet"
    assert sa.tier_for_path("/gold/kingdom-report") == "pro"
    assert sa.tier_for_path("/gold/scan") is None          # open
    assert sa.tier_for_path("/health") is None
    assert sa.tier_for_path("/execution/pending") == "fleet"


# --- signup + auth (async, monkeypatched db) ---------------------------------

class _FakeResult:
    def __init__(self, obj):
        self._obj = obj
    def scalars(self):
        class _S:
            def __init__(s, o):
                s._o = o
            def first(s):
                return s._o
        return _S(self._obj)


class _FakeDB:
    def __init__(self, existing=None):
        self._existing = existing
        self.added = []
    async def execute(self, *a, **k):
        return _FakeResult(self._existing)
    def add(self, row):
        row.id = 1
        self.added.append(row)
    async def commit(self):
        pass
    async def refresh(self, row):
        pass


def _run(coro):
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_signup_mints_key():
    from services import saas_service as svc
    out = _run(svc.signup(_FakeDB(existing=None), "a@b.com", "scout"))
    assert out["existing"] is False and out["api_key"].startswith("wc_")
    assert out["tier"] == "scout" and out["account_limit"] == 0


def test_signup_bad_tier_falls_to_scout():
    from services import saas_service as svc
    out = _run(svc.signup(_FakeDB(existing=None), "a@b.com", "nonsense"))
    assert out["tier"] == "scout"


# --- billing localisation -----------------------------------------------------

def test_checkout_localises_price():
    from gold.account_network import currency_deposits
    kes = currency_deposits(149, ["KES"])["deposits"][0]["deposit"]
    assert kes > 149        # Pro $149 in KES is a bigger local number


# --- DST / session tz offset --------------------------------------------------

def test_session_tz_offset_configurable(monkeypatch):
    import importlib
    from gold import session_tz
    monkeypatch.setenv("SESSION_TZ_OFFSET", "-5")     # broker on EST (winter)
    importlib.reload(session_tz)
    assert session_tz.tz_offset() == -5
    monkeypatch.delenv("SESSION_TZ_OFFSET", raising=False)
    importlib.reload(session_tz)
    assert session_tz.tz_offset() == -4               # default EDT
