import asyncio
import datetime as dt

from services import council as cn
from services import yearly_cbdr as yc

GOLD = {"years": 18, "span": "2008–2025", "median_up": 1.77, "median_down": 0.01,
        "pct_reach_up": {"1.5": 50, "2": 39, "2.5": 33, "3": 28, "4": 17},
        "pct_reach_down": {"-0.5": 28, "-1": 11, "-1.5": 11, "-2": 11, "-3": 0},
        "pct_close_above_box": 50, "pct_close_below_box": 22, "source": "auto-twelvedata"}


def test_reach_pct_from_live_gold_rates():
    assert yc.reach_pct(GOLD, 0.5) == 100
    assert yc.reach_pct(GOLD, 2.3) == 33          # next threshold at or beyond → 2.5
    assert yc.reach_pct(GOLD, 3.9) == 17 and yc.reach_pct(GOLD, 5.0) == 8
    assert yc.reach_pct(GOLD, -1.2) == 11 and yc.reach_pct(GOLD, -3.5) == 0
    assert yc.reach_pct(None, 2.0) is None
    assert yc.lean(GOLD) == "BUY"                 # gold: long-term bullish


def test_base_rates_agent_blocks_only_position_trades_against_the_lean():
    assert cn.vote_base_rates(GOLD, "BUY")["vote"] == 1
    assert cn.vote_base_rates(GOLD, "SELL", "swing")["vote"] == 0      # tactical shorts allowed
    assert cn.vote_base_rates(GOLD, "SELL", "position")["vote"] == -1  # no position shorts in gold
    assert cn.vote_base_rates(None, "BUY")["vote"] == 0


def test_position_targets_carry_historical_reach():
    from services import sniper
    r = {"years": [{"year": 2026, "box_low": 1000.0, "box_high": 1100.0}], "history": GOLD,
         "clusters": [{"mid": 1350.0, "members": ["a"]}, {"mid": 1480.0, "members": ["b"]}]}
    p = sniper.position_targets(r, "BUY", 1000.0, 990.0)
    assert [x["hist"] for x in p] == [17, 8]      # k 3.5 → next step 4.0 (17 %); k 4.8 → 17/2


def test_feed_health_and_base_rates_persist(tmp_path, monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker
    import database.db as dbm
    import models.data_cache_model  # noqa: F401
    from services import data_feeds as df, data_store
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'c.db'}")
    monkeypatch.setattr(dbm, "AsyncSessionLocal", sessionmaker(bind=eng, class_=AsyncSession,
                                                               expire_on_commit=False))
    loop = asyncio.new_event_loop()
    try:
        async def go():
            async with eng.begin() as c:
                await c.run_sync(dbm.Base.metadata.create_all)
            df._health.clear()
            df.record("stooq", True, 50)
            await df.persist(force=True)
            df._health.clear()                       # simulate a Render restart
            df._persist["loaded"] = False
            await df.ensure_loaded()
            assert df._health["stooq"]["ok"] == 1
            await data_store.put("baserates:XAUUSD", GOLD)
            v, at = await data_store.get("baserates:XAUUSD")
            assert v["years"] == 18 and at is not None
        loop.run_until_complete(go())
    finally:
        loop.close()
