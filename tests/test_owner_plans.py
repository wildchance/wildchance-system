import datetime as dt

from services import council as cn
from services import owner_plans as op


def test_plans_load_activate_and_invalidate():
    d = dt.date(2026, 11, 24)
    plans = op.active("NAS100", 26755.3, d)
    assert {p["side"] for p in plans} == {"SELL", "BUY"}
    sell = next(p for p in plans if p["side"] == "SELL")
    assert (sell["low"], sell["high"]) == (28083.4, 30143.9) and sell["targets"][0] == 26178.6
    assert [p["side"] for p in op.active("NAS100", 31500.0, d)] == ["BUY"]          # sell invalidated
    assert [p["side"] for p in op.active("NAS100", 21000.0, d)] == ["SELL"]         # buy invalidated
    assert op.active("NAS100", 26755.3, dt.date(2028, 1, 2)) == []                  # expired
    assert op.at_zone(plans, 28100, 28200)[0]["side"] == "SELL"


def test_plan_agent_votes():
    plans = op.active("NAS100", 28200.0, dt.date(2026, 11, 24))
    assert cn.vote_plan(plans, "SELL", 28200.0, 80)["vote"] == 1
    assert cn.vote_plan(plans, "BUY", 28200.0, 80)["vote"] == -1                   # fights the plan
    assert cn.vote_plan(plans, "BUY", 26755.0, 80)["vote"] == 0                    # not in a zone
    c = op.as_cluster(plans[0])
    assert c["members"][0].startswith("PLAN") and c["plan"]["side"] in ("SELL", "BUY")
