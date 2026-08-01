"""Live-DXY feed guard + execution-enable flag (maybe_enqueue)."""

import asyncio

from services import trade_executor as te


def _run(coro):
    """Run a coroutine on a dedicated loop, then restore a current event loop so
    later modules that use the (deprecated) asyncio.get_event_loop() still work."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(asyncio.new_event_loop())


# --- execution-enable flag ---------------------------------------------------

class _FakeDB:
    def __init__(self):
        self.added = []
    def add(self, row):
        self.added.append(row)
    async def commit(self):
        pass
    async def refresh(self, row):
        row.id = 1


def _sig():
    return {"signal": "SHORT", "entry": 4052.0, "stop": 4067.0,
            "gate": {"allow": True}, "lot": 0.05,
            "targets": [{"price": 4030.0}], "trade_type": "sniper"}


def test_maybe_enqueue_noop_when_paper(monkeypatch):
    monkeypatch.setattr(te, "EXECUTION_ENABLED", False)
    out = _run(te.maybe_enqueue(_FakeDB(), _sig(), "confirm_reject"))
    assert out is None                       # paper mode → nothing queued


def test_maybe_enqueue_routes_when_live(monkeypatch):
    monkeypatch.setattr(te, "EXECUTION_ENABLED", True)
    captured = {}

    async def fake_enqueue(db, order):
        captured["order"] = order
        return {"id": 1, "status": "pending", **order}

    monkeypatch.setattr(te, "enqueue", fake_enqueue)
    out = _run(te.maybe_enqueue(_FakeDB(), _sig(), "confirm_reject"))
    assert out is not None and out["status"] == "pending"
    assert captured["order"]["side"] == "sell"        # SHORT → sell order


def test_maybe_enqueue_scales_out_when_exit_style_partial(monkeypatch):
    monkeypatch.setattr(te, "EXECUTION_ENABLED", True)
    monkeypatch.setattr(te, "FLEET_ENABLED", False)
    queued = []

    async def fake_enqueue(db, order):
        queued.append(order)
        return {"id": len(queued), "status": "pending", **order}

    monkeypatch.setattr(te, "enqueue", fake_enqueue)
    sig = {"signal": "SHORT", "entry": 4200.0, "stop": 4212.0, "lot": 0.30,
           "kind": "limit", "gate": {"allow": True},
           "targets": [{"price": 4100.0}, {"price": 3885.0}], "exit_style": "partial"}
    out = _run(te.maybe_enqueue(_FakeDB(), sig, "optimus"))
    assert out and out["legs"] == 3 and out["group_id"]
    roles = [o["scale_role"] for o in queued]
    assert roles == ["p1", "p2", "runner"]
    runner = queued[-1]
    assert runner["be_price"] == 4200.0 and runner["be_after"] == "p1"


def test_maybe_enqueue_swallows_errors(monkeypatch):
    monkeypatch.setattr(te, "EXECUTION_ENABLED", True)

    async def boom(db, order):
        raise RuntimeError("db down")

    monkeypatch.setattr(te, "enqueue", boom)
    # must never raise — a broker-queue failure can't block the tracked open
    assert _run(te.maybe_enqueue(_FakeDB(), _sig(), "x")) is None


# --- live-DXY level guard (proxy is directional, not a level) ----------------

def test_latest_dxy_flags_real_vs_proxy(monkeypatch):
    from services import dxy_service as ds

    async def fake_fetch(interval="1h", outputsize=60):
        return [99.0, 100.0, 100.75], "DXY"
    monkeypatch.setattr(ds, "fetch_dxy", fake_fetch)
    d = _run(ds.latest_dxy())
    assert d["price"] == 100.75 and d["is_level"] is True

    async def fake_proxy(interval="1h", outputsize=60):
        return [0.92, 0.93], "proxy:1/EURUSD"
    monkeypatch.setattr(ds, "fetch_dxy", fake_proxy)
    d2 = _run(ds.latest_dxy())
    assert d2["is_level"] is False           # proxy never fed as a 110/117 level
