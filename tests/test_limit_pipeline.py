"""Limit-order sizing, pending-limit lifecycle, and enriched plan geometry."""

import datetime as dt

from gold.limit_orders import size_limit
from gold import position as pos
from cbdr.engine import build_cbdr, prelondon_limits
from candlerange.crt import crt_setup


# --- size_limit -------------------------------------------------------------

def test_size_limit_builds_sized_card():
    order = {"side": "long", "entry": 4000, "stop": 3960, "targets": [4080, 4120],
             "trade_type": "prelondon", "reason": "buy −1SD"}
    card = size_limit(order, balance=5000, risk_usd=20)
    assert card["signal"] == "LONG" and card["stop"] == 3960 and card["kind"] == "limit"
    assert card["trade_type"] == "prelondon" and card["lot"] >= 0.01
    rrs = [t["rr"] for t in card["targets"]]
    assert rrs == [2.0, 3.0]                    # 80/40, 120/40


def test_size_limit_rejects_bad_geometry():
    assert size_limit({"side": "long", "entry": 4000, "stop": 4000}, 5000)["signal"] == "NO TRADE"
    assert size_limit({"side": "long", "entry": 4000}, 5000)["signal"] == "NO TRADE"


# --- pending-limit lifecycle ------------------------------------------------

_NOW = dt.datetime(2026, 7, 7, 12, 0, tzinfo=dt.timezone.utc)


def test_pending_long_fills_on_touch():
    st = {"side": "long", "limit_price": 4000, "expires_at": None}
    assert pos.evaluate_pending(st, 4001, _NOW)["action"] == "wait"
    assert pos.evaluate_pending(st, 3999, _NOW)["action"] == "fill"


def test_pending_short_fills_on_touch():
    st = {"side": "short", "limit_price": 4300, "expires_at": None}
    assert pos.evaluate_pending(st, 4299, _NOW)["action"] == "wait"
    assert pos.evaluate_pending(st, 4301, _NOW)["action"] == "fill"


def test_pending_cancels_after_expiry():
    st = {"side": "long", "limit_price": 4000, "expires_at": _NOW}
    a = pos.evaluate_pending(st, 4050, _NOW + dt.timedelta(minutes=1))
    assert a["action"] == "cancel"


# --- enriched plan geometry (stop + targets present) ------------------------

def test_prelondon_orders_have_stop_and_targets():
    plan = prelondon_limits(build_cbdr(4200.0, 4100.0))   # range 100
    by = {o["level"]: o for o in plan["orders"]}
    assert by["-1SD"]["stop"] == 3900.0 and by["-1SD"]["targets"] == [4150.0, 4300.0]
    assert by["+1SD"]["stop"] == 4400.0
    for o in plan["orders"]:
        assert size_limit(o, 5000)["signal"] in ("LONG", "SHORT")   # all sizeable


def test_crt_orders_have_stop_and_targets():
    s = crt_setup({"open": 100.0, "high": 112.0, "low": 98.0, "close": 110.0}, "asia")
    buy = next(o for o in s["orders"] if o["side"] == "long")
    assert buy["entry"] == 90 and buy["stop"] == 80 and buy["targets"] == [110, 120]
    assert size_limit(buy, 5000)["signal"] == "LONG"
