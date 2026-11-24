import asyncio
import datetime as dt

from services import options_levels as ol
from services import weekly_map as wm


def test_weekly_map_path_follows_cot():
    box = {"date": "2026-11-23", "low": 3926.1, "high": 3989.3}
    long_ = wm.build(box, 3952.5, wm.bias_from({"verdict": "LONG", "confidence": 88, "cot_agrees": True}, None))
    assert long_["bias"]["side"] == "BUY" and [p["price"] for p in long_["path"]][0] > 3952.5
    assert long_["against"][0]["price"] < 3952.5
    short = wm.build(box, 3952.5, wm.bias_from(None, {"bias": "short"}))
    assert short["bias"]["side"] == "SELL" and short["path"][0]["price"] < 3952.5
    flat = wm.build(box, 3952.5, wm.bias_from({"verdict": "WATCH", "confidence": 50}, None))
    assert flat["bias"]["side"] is None and flat["path"] == []
    assert "Weekly map — XAU/USD" in wm.card("XAU/USD", long_)
    assert wm.monday_box({dt.date(2026, 11, 23): (3926.1, 3989.3)}, dt.date(2026, 11, 25))["low"] == 3926.1


def _chain():
    opts = []
    for k, coi, poi in ((350, 100, 4000), (355, 300, 2500), (360, 900, 600), (365, 5000, 100), (370, 2000, 50)):
        opts.append({"option": f"GLD261218C{int(k * 1000):08d}", "open_interest": coi, "gamma": 0.02, "iv": 0.22})
        opts.append({"option": f"GLD261218P{int(k * 1000):08d}", "open_interest": poi, "gamma": 0.02, "iv": 0.24})
    return {"data": {"current_price": 361.0, "options": opts}}


def test_options_levels_from_chain():
    ch = ol.parse_chain(_chain(), today=dt.date(2026, 11, 24))
    assert ch["spot"] == 361.0 and ch["strikes"][365.0]["call_oi"] == 5000
    lv = ol.levels(ch)
    assert lv["inventory_ceiling"] == 365.0 and lv["hedge_wall"] == 370.0
    assert lv["primary_floor"] == 350.0 and lv["absorption_shelf"] == 355.0
    assert lv["closest_strike"] == 360.0 and lv["gamma_flip"] is not None
    assert lv["exhaustion_low"] < 361.0 < lv["exhaustion_high"]
    m = ol.mapped(lv, 3952.5 / 361.0)                       # GLD → XAU/USD
    ceiling = next(x for x in m if x["key"] == "inventory_ceiling")
    assert abs(ceiling["price"] - 365 * 3952.5 / 361) < 0.01
    assert ol.underlying_for("XAU/USD") == "GLD" and ol.underlying_for("NAS100") == "_NDX"


def test_failed_telegram_send_is_retried(monkeypatch):
    from services import realtime, telegram_service
    calls = []

    async def fail(text):
        calls.append(text)
        return False
    monkeypatch.setattr(telegram_service, "send_telegram_message", fail)
    loop = asyncio.new_event_loop()
    try:
        assert loop.run_until_complete(realtime.alert_once("k:retry", "card")) is False
        assert loop.run_until_complete(realtime.alert_once("k:retry", "card")) is False   # tried again
    finally:
        loop.close()
    assert len(calls) == 2 and "k:retry" not in realtime._sent
