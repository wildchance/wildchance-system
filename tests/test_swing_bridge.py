"""HTF Bollinger swing engine, CBDR confluence, broker feed (EA → app) and realtime."""

import datetime as dt
import random

import pytest

from indicators import bollinger as bb
from services import broker_feed as bf


def _spike_series(seed=3):
    random.seed(seed)
    base = [100 + random.gauss(0, 0.6) for _ in range(60)]
    return base + [103, 106, 109, 111, 108.2]


def test_bands_and_zones():
    b = bb.bands([1, 2, 3, 4, 5] * 4)
    assert round(b["mid"], 6) == 3 and b["up2"] > b["up1"] > b["mid"] > b["lo1"] > b["lo2"]
    assert bb.zone(2.1) == "EXTREME_HIGH" and bb.zone(-1.2) == "STRETCHED_LOW"
    assert bb.zone(0.3) == "CORE"


def test_reentry_after_2sigma_spike_is_a_sell_entry():
    d1 = _spike_series()
    s = bb.state(d1)
    assert s["reentry_down"] and s["prev_z"] > 2
    random.seed(9)
    w1 = [100 + random.gauss(0, 2) for _ in range(30)] + [104, 108, 110]
    r = bb.swing_setup({"W1": bb.state(w1), "D1": s, "H4": None},
                       [c + .8 for c in d1], [c - .8 for c in d1], d1)
    assert r["side"] == "SELL" and r["status"] == "ENTRY" and r["score"] >= 6
    lv = r["levels"]
    assert lv["stop"] > lv["entry"] > lv["tp1_dev1"] > lv["tp2_mean"] and lv["rr_to_mean"] > 1


def test_band_walk_is_trend_not_fade():
    up = [100 + i * 0.8 for i in range(70)]
    r = bb.swing_setup({"D1": bb.state(up)}, [c + .5 for c in up], [c - .5 for c in up], up)
    assert r["status"] == "TREND" and any("walk" in b for b in r["blockers"])


def test_cbdr_confluence_adds_score_and_limit():
    d1 = _spike_series()
    states = {"D1": bb.state(d1)}
    setup = {"side": "SELL", "score": 5, "status": "WATCH", "blockers": []}
    lv = {"+2SD": states["D1"]["up2"] + 0.1, "-2SD": 50.0}
    out = bb.cbdr_confluence(setup, lv, states, tol=0.5)
    assert out["score"] == 7 and out["status"] == "LIMIT" and out["cbdr_confluence"]
    assert bb.cbdr_confluence(setup, {"+2SD": 999.0}, states, 0.5)["score"] == 5


def test_watchlist_item_lands_in_range_tier():
    from pipeline.engine import run_audit
    item = bb.as_watchlist_item("XAUUSD", {"side": "SELL", "score": 8, "status": "ENTRY",
                                           "levels": {"rr_to_mean": 1.6, "entry": 3540,
                                                      "stop": 3600, "tp2_mean": 3400, "d1_up1": 3470}})
    out = run_audit([item], prop_mode=True)
    assert out["ranked"][0]["class"] == "RANGE" and out["ranked"][0]["chop_trap"]


@pytest.mark.parametrize("raw,want", [("XAUUSD.r", "XAUUSD"), ("GOLDm", "XAUUSD"),
                                      ("XAU/USD", "XAUUSD"), ("USTEC", "NAS100"),
                                      ("SPX", "US500"), ("WTI/USD", "USOIL"),
                                      ("BTCUSDT", "BTCUSD"), ("EURUSD.pro", "EURUSD"),
                                      ("USDJPY", "USDJPY"), ("DJI", "US30")])
def test_symbol_canon(raw, want):
    assert bf.canon(raw) == want


def test_freshness_rules():
    wed = dt.datetime(2026, 11, 25, 12, tzinfo=dt.timezone.utc)
    assert bf.is_fresh(wed - dt.timedelta(hours=2), "H1", wed)
    assert not bf.is_fresh(wed - dt.timedelta(hours=8), "H1", wed)
    sun = dt.datetime(2026, 11, 29, 20, tzinfo=dt.timezone.utc)
    assert bf.is_fresh(dt.datetime(2026, 11, 27, 21, tzinfo=dt.timezone.utc), "H1", sun)


def test_ea_push_feeds_swing_engine_end_to_end(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker
    import database.db as dbm
    import models.broker_bar_model  # noqa: F401  (registers the table)
    import routes.bridge as rb
    from main import app
    monkeypatch.setattr(rb, "TOKEN", "t0k")
    # isolated SQLite DB — other tests may point DATABASE_URL at a dead Postgres
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'feed.db'}")
    Session = sessionmaker(bind=eng, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(dbm, "AsyncSessionLocal", Session)

    async def _db():
        async with Session() as s:
            yield s
    app.dependency_overrides[dbm.get_db] = _db

    async def _mk():
        async with eng.begin() as conn:
            await conn.run_sync(dbm.Base.metadata.create_all)
    import asyncio
    loop = asyncio.new_event_loop()
    loop.run_until_complete(_mk())
    loop.close()
    now = dt.datetime.now(dt.timezone.utc).replace(minute=0, second=0, microsecond=0)
    d1 = _spike_series()
    day0 = (now - dt.timedelta(days=len(d1))).replace(hour=0)
    bars = [{"t": int((day0 + dt.timedelta(days=i + 1)).timestamp()), "o": c, "h": c + .8,
             "l": c - .8, "c": c} for i, c in enumerate(d1)]
    with TestClient(app) as c:
        assert c.post("/bridge/bars", json={"symbol": "XAGUSD.r", "timeframe": "D1",
                                            "bars": bars}).status_code == 403
        r = c.post("/bridge/bars?token=t0k", json={"symbol": "XAGUSD.r", "timeframe": "D1",
                                                   "bars": bars}).json()
        assert r["ok"] and r["symbol"] == "XAGUSD" and r["bars"] == len(d1)
        # re-push overlapping bars → upsert, not duplicates
        c.post("/bridge/bars?token=t0k", json={"symbol": "XAGUSD", "timeframe": "D1",
                                               "bars": bars[-5:]})
        st = c.get("/bridge/status").json()
        row = [s for s in st["feed"]["series"] if s["symbol"] == "XAGUSD" and s["timeframe"] == "D1"][0]
        assert row["bars"] == len(d1) and row["fresh"]
        sw = c.get("/swing/XAGUSD").json()
        assert sw["symbol"] == "XAGUSD" and sw["bands"]["D1"]["zone"] == "STRETCHED_HIGH"
        assert sw["side"] == "SELL"
        assert c.post("/bridge/account?token=t0k", json={"login": 1, "equity": 10000}).json()["ok"]
    app.dependency_overrides.clear()


def test_alert_once_dedupes(monkeypatch):
    import asyncio
    from services import realtime
    sent = []

    async def fake(text):
        sent.append(text)
    import services.telegram_service as ts
    monkeypatch.setattr(ts, "send_telegram_message", fake)
    realtime._sent.clear()
    loop = asyncio.new_event_loop()          # own loop: don't clear the global one
    try:
        assert loop.run_until_complete(realtime.alert_once("k1", "hello"))
        assert not loop.run_until_complete(realtime.alert_once("k1", "hello again"))
    finally:
        loop.close()
    assert sent == ["hello"]


def test_realtime_td_symbol_mapping():
    from services.realtime import td_symbol
    assert td_symbol("XAUUSD") == "XAU/USD" and td_symbol("US500") == "SPX"
    assert td_symbol("EURUSD") == "EUR/USD"


def test_macro_bias_demotes_counter_trend_fade(monkeypatch):
    from services import swing_service as sw
    monkeypatch.setenv("MACRO_BIAS", '{"BTCUSD": "SELL_ONLY"}')
    import importlib, decouple
    importlib.reload(decouple); importlib.reload(sw)
    assert sw.macro_bias() == {"BTCUSD": "SELL_ONLY"}
    row = sw.apply_bias({"side": "BUY", "status": "ENTRY", "blockers": []}, "SELL_ONLY")
    assert row["status"] == "WATCH" and "against macro bias SELL_ONLY" in row["blockers"]
    assert sw.apply_bias({"side": "SELL", "status": "ENTRY"}, "SELL_ONLY")["status"] == "ENTRY"
