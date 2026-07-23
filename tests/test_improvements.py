"""Deploy-hardening + edge-measurement improvements — pure-logic guards."""

import asyncio

import pytest

from gold import retracement as gret
from gold import options_flow as of


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(asyncio.new_event_loop())


def _bar(o, h, l, c, t="d"):
    return (t, o, h, l, c)


# --- #5 retracement backtest --------------------------------------------------

def test_backtest_retracement_shape():
    # a long descending-then-retracing series → at least runs and tallies cleanly
    bars = []
    px = 4200.0
    for i in range(40):
        bars.append(_bar(px, px + 2, px - 8, px - 6, f"d{i}"))
        px -= 6
    out = gret.backtest_retracement(bars, lookahead=5, htf_bias="short", tp_r=2.0)
    assert out["bars"] == 40 and out["lookahead"] == 5 and out["tp_r"] == 2.0
    for st in ("SELL_OTE", "SCALP_BOUNCE"):
        assert st in out and "n" in out[st] and "total_r" in out[st]
    assert isinstance(out["trades"], list)


def test_backtest_win_rate_bounds():
    bars = [_bar(4000 + i, 4000 + i + 3, 4000 + i - 3, 4000 + i + 1, f"d{i}")
            for i in range(50)]
    out = gret.backtest_retracement(bars, lookahead=4)
    for st in ("SELL_OTE", "SCALP_BOUNCE"):
        wr = out[st]["win_rate"]
        assert wr is None or (0.0 <= wr <= 1.0)


# --- #6 options feed ingest ---------------------------------------------------

def test_options_ingest_flat_and_nested(monkeypatch):
    from services import options_service as osvc
    # reset operator INPUTS
    of.INPUTS.update({"future": None, "put_wall": None, "call_wall": None,
                      "sigma": {"1": None, "2": None, "3": None},
                      "put_vol": None, "call_vol": None, "as_of": None})
    fed = osvc._ingest({"future": 4030.4, "put_wall": 3980, "call_wall": 4080,
                        "sigma1": 18, "sigma2": 36, "sigma3": 54,
                        "put_vol": 12000, "call_vol": 8000, "as_of": "2026-07-23"})
    assert fed["future"] == 4030.4 and fed["put_wall"] == 3980.0
    assert of.configured() is True
    # nested sigma form
    fed2 = osvc._ingest({"future": 4000, "sigma": {"1": 10, "2": 20, "3": 30}})
    assert fed2["sigma"]["2"] == 20.0


def test_options_refresh_noop_without_url(monkeypatch):
    from services import options_service as osvc
    monkeypatch.setattr(osvc, "OPTIONS_FEED_URL", None)
    out = _run(osvc.refresh())
    assert out["ok"] is False and out["feed_configured"] is False


# --- #7 execution reconcile ---------------------------------------------------

class _FakeDB:
    async def execute(self, *a, **k):
        class _R:
            def scalars(self):
                class _S:
                    def all(self_inner):
                        return []
                return _S()
        return _R()


def test_reconcile_in_sync_when_empty(monkeypatch):
    from services import trade_executor as te
    from services import gold_positions as gp

    async def _no_orders(db, limit=50):
        return []

    async def _no_positions(db, **k):
        return []

    monkeypatch.setattr(te, "recent", _no_orders)
    monkeypatch.setattr(gp, "list_positions", _no_positions)
    out = _run(te.reconcile(_FakeDB()))
    assert out["in_sync"] is True and out["drift"] == 0


def test_reconcile_flags_orphan_fill(monkeypatch):
    from services import trade_executor as te
    from services import gold_positions as gp

    async def _orders(db, limit=50):
        return [{"status": "filled", "source": "retracement_paper", "side": "short",
                 "created_at": None}]

    async def _positions(db, **k):
        return []                              # no OPEN position for that fill

    monkeypatch.setattr(te, "recent", _orders)
    monkeypatch.setattr(gp, "list_positions", _positions)
    out = _run(te.reconcile(_FakeDB()))
    assert out["orphan_fill_count"] == 1 and out["in_sync"] is False
