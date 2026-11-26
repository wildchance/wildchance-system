import datetime as dt

from services import council, owner_plans

DAY = dt.date(2026, 11, 27)


def test_btc_plans_loaded_and_vote():
    sells = [p for p in owner_plans.active("BTC/USD", 79000, today=DAY) if p["side"] == "SELL"]
    buys = [p for p in owner_plans.active("BTCUSD", 45500, today=DAY) if p["side"] == "BUY"]
    assert sells and sells[0]["low"] == 77200 and sells[0]["targets"] == [60760, 50000, 46307]
    assert buys and buys[0]["invalidation"] == 40535
    assert council.vote_plan(owner_plans.active("BTCUSD", 79000, today=DAY), "SELL", 79000)["vote"] == 1
    assert council.vote_plan(owner_plans.active("BTCUSD", 79000, today=DAY), "BUY", 79000)["vote"] == -1
    assert council.vote_plan(owner_plans.active("BTCUSD", 60760, today=DAY), "BUY", 60760)["vote"] == 0
    assert not [p for p in owner_plans.active("BTCUSD", 90000, today=DAY) if p["side"] == "SELL"]   # invalidated
    assert not [p for p in owner_plans.active("BTCUSD", 40000, today=DAY) if p["side"] == "BUY"]
