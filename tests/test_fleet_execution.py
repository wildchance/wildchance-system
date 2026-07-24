"""Per-account fleet fan-out — one signal → 5 sized, account-tagged orders."""

import asyncio

from services import trade_executor as te


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(asyncio.new_event_loop())


def _sig():
    return {"signal": "SHORT", "entry": 4059.0, "stop": 4067.0,
            "gate": {"allow": True}, "lot": 0.05,
            "targets": [{"price": 4044.0}], "trade_type": "sniper", "profile": "sniper"}


def test_fleet_accounts_default_five():
    accts = te.fleet_accounts()
    assert len(accts) == 5
    assert {a["id"] for a in accts} == {"acc1", "acc2", "acc3", "acc4", "acc5"}


def test_build_fleet_orders_sizes_per_account():
    orders = te.build_fleet_orders(_sig(), source="gold")
    assert len(orders) == 5
    # every order same entry/stop/side, tagged with its account, distinct magic
    assert all(o["side"] == "sell" and o["sl"] == 4067.0 for o in orders)
    assert {o["account"] for o in orders} == {"acc1", "acc2", "acc3", "acc4", "acc5"}
    assert len({o["magic"] for o in orders}) == 5
    # bigger-balance account → bigger lot (acc5 100k > acc1 700)
    lots = {o["account"]: o["volume"] for o in orders}
    assert lots["acc5"] >= lots["acc1"]


def test_build_fleet_orders_blocked_by_gate():
    sig = _sig()
    sig["gate"] = {"allow": False}
    assert te.build_fleet_orders(sig) == []


class _FakeDB:
    def __init__(self):
        self.rows = []
    def add(self, row):
        row.id = len(self.rows) + 1
        self.rows.append(row)
    async def commit(self):
        pass
    async def refresh(self, row):
        pass


def test_maybe_enqueue_fleet_when_enabled(monkeypatch):
    monkeypatch.setattr(te, "EXECUTION_ENABLED", True)
    monkeypatch.setattr(te, "FLEET_ENABLED", True)
    out = _run(te.maybe_enqueue(_FakeDB(), _sig(), "gold"))
    assert out and out.get("accounts") == 5 and len(out["fleet"]) == 5


def test_maybe_enqueue_single_when_fleet_off(monkeypatch):
    monkeypatch.setattr(te, "EXECUTION_ENABLED", True)
    monkeypatch.setattr(te, "FLEET_ENABLED", False)
    out = _run(te.maybe_enqueue(_FakeDB(), _sig(), "gold"))
    # single order path returns a dict with an id, not a fleet list
    assert out and "fleet" not in out
