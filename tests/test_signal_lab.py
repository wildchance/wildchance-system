import asyncio
import random

import pytest

from services import council
from services import signal_lab as sl


def _bars(n_mk=12, days=320, seed=7):
    """Mean-reverting markets: tomorrow's return ≈ −0.5 × today's + noise."""
    rnd = random.Random(seed)
    bars = {}
    for j in range(n_mk):
        px, prev, rows = 100.0 + j, 0.0, []
        for d in range(days):
            r = -0.5 * prev + rnd.gauss(0, 0.01)
            px *= 1 + r
            prev = r
            rows.append({"d": f"2025-{1 + d // 28:02d}-{1 + d % 28:02d}" if d < 336 else str(d),
                         "open": px, "high": px * 1.004, "low": px * 0.996, "close": px})
        bars[f"M{j}"] = rows
    return bars


@pytest.fixture
def fields():
    return sl.build_panel(_bars())[2]


def test_python_is_never_executed(fields):
    for bad in ("__import__('os').system('x')", "close.__class__", "open(1)", "[1,2]",
                "lambda: 1", "close if 1 else 2", "ts_mean(close, 999)", "eval('1')"):
        with pytest.raises(sl.FormulaError):
            sl.evaluate(bad, fields)


def test_grammar_shapes(fields):
    v = sl.evaluate("rank(ts_zscore(ret, 10)) - demean(ts_return(close, 5)) * 2", fields)
    assert len(v) == len(fields["close"]) and len(v[0]) == 12
    assert v[0][0] is None and v[-1][0] is not None


def test_true_signal_accepted_noise_rejected(fields):
    good = sl.score("-ret", fields)
    assert good["accepted"] and good["direction"] == 1 and good["oos"]["ic"] > 0.1
    inverted = sl.score("ret", fields)
    assert inverted["accepted"] and inverted["direction"] == -1
    assert not sl.score("ts_mean(range / close, 20)", fields)["accepted"]
    assert "error" in sl.score("volume", fields)


def test_ai_loop_feeds_back_and_saves(monkeypatch):
    saved = {}

    async def fake_panel():
        return sl.build_panel(_bars())

    async def fake_save(new, theme):
        saved["new"] = new
        return len(new)
    monkeypatch.setattr(sl, "panel", fake_panel)
    monkeypatch.setattr(sl, "save", fake_save)
    calls = []

    async def ask(msgs):
        calls.append(msgs[-1]["content"])
        if len(calls) == 1:
            return '[{"name":"bad","formula":"close.x","idea":"oops"},{"name":"mom","formula":"ret","idea":"m"}]'
        return 'Sure: [{"name":"rev","formula":"-ret","idea":"fade yesterday"}]'
    r = asyncio.new_event_loop().run_until_complete(sl.run("reversion", rounds=2, ask=ask))
    assert r["ok"] and r["tried"] == 3
    assert "REJECTED" in calls[1] and "accepted" in calls[1]        # round 2 got the scores
    assert {s["formula"] for s in saved["new"]} == {"ret", "-ret"}


def test_spotter_tilt_and_vote(fields):
    syms = [f"M{j}" for j in range(12)]
    t = sl.tilt_from([{"formula": "-ret", "direction": 1}], fields, syms)
    assert set(t) == set(syms) and all(v["signals"] == 1 for v in t.values())
    assert {v["tilt"] for v in t.values()} <= {-1.0, 0.0, 1.0}
    assert council.vote_spotter({"tilt": 0.6, "signals": 3}, "BUY")["vote"] == 1
    assert council.vote_spotter({"tilt": 0.6, "signals": 3}, "SELL")["vote"] == -1
    assert council.vote_spotter({"tilt": 0.1, "signals": 3}, "BUY")["vote"] == 0
    assert council.vote_spotter(None, "BUY")["vote"] == 0
