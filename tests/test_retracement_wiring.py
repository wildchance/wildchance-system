"""Retracement wiring — scalp BE-at-1R guard, state summary, card build, alert dedup."""

import asyncio
import datetime as dt

import pytest

from gold import position as pos
from services import retracement_service as rsvc


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(asyncio.new_event_loop())


_NOW = dt.datetime(2026, 7, 22, 12, 0, tzinfo=dt.timezone.utc)


# --- #4 scalp BE-at-1R --------------------------------------------------------

def test_scalp_trails_to_be_at_1r():
    # sd_fade long, entry 4008 stop 3983.5 (risk 24.5), no TP printed yet.
    state = {"side": "long", "entry": 4008.0, "stop": 3983.5, "targets": [4200.0],
             "be_active": False, "trade_type": "sd_fade", "opened_at": _NOW}
    at_1r = 4008.0 + 24.5                      # +1R with no target hit
    out = pos.evaluate(state, at_1r, _NOW)
    assert out["be_active"] is True and out["stop"] == 4008.0   # trailed to entry
    assert out["tp_hit"] == 0                                   # before any TP


def test_scalp_closes_at_be_not_full_stop():
    # after arming BE, a drop back to entry closes at ~0R (not −1R at the old stop)
    state = {"side": "long", "entry": 4008.0, "stop": 3983.5, "targets": [4200.0],
             "be_active": True, "trade_type": "sd_fade", "opened_at": _NOW}
    out = pos.evaluate(state, 4008.0, _NOW)
    assert out["close"] is True and out["exit_reason"] == "BE"


def test_non_scalp_no_early_be():
    # a swing long at +1R with no TP does NOT trail early — only after TP1
    state = {"side": "long", "entry": 4008.0, "stop": 3983.5, "targets": [4200.0],
             "be_active": False, "trade_type": "swing", "opened_at": _NOW}
    out = pos.evaluate(state, 4008.0 + 24.5, _NOW)
    assert out["be_active"] is False


# --- summary + card -----------------------------------------------------------

def test_summary_compact():
    read = {"state": "LEAVE", "label": "LEAVE", "actionable": False,
            "signal": None, "retracement": 0.4, "reason": "mid"}
    s = rsvc.summary(read)
    assert s["state"] == "LEAVE" and s["actionable"] is False and s["retracement"] == 0.4


def test_sell_card_build():
    read = {"state": "SELL_OTE", "actionable": True, "signal": "SHORT",
            "entry": 4170.0, "stop": 4206.5, "trade_type": "swing",
            "targets": [{"price": 4000.0}], "reason": "sell the top"}
    card = rsvc._sell_card(read, balance=5000.0, risk_usd=20.0)
    assert card["signal"] == "SHORT" and card["entry"] == 4170.0
    assert card["lot"] > 0 and "gate" in card
    assert card["profile"] == "retracement_sell_ote"


def test_sell_card_none_without_levels():
    assert rsvc._sell_card({"state": "SELL_OTE"}, 5000, 20) is None


# --- transition alert dedup (monkeypatched, no network) -----------------------

def test_alert_fires_on_transition(monkeypatch):
    sent = {}

    async def _fake_read(**kw):
        return {"state": "SELL_OTE", "actionable": True, "display": "SELL card"}

    async def _fake_tg(text):
        sent["text"] = text
        return True

    monkeypatch.setattr(rsvc, "live_read", _fake_read)
    monkeypatch.setattr(rsvc.gold_scan, "_tg", _fake_tg)
    monkeypatch.setattr(rsvc, "_read_last", lambda: "LEAVE")
    monkeypatch.setattr(rsvc, "_write_last", lambda s: None)

    out = _run(rsvc.state_alert(notify=True))
    assert out["changed"] is True and out["sent"] is True
    assert sent["text"] == "SELL card"


def test_alert_quiet_when_unchanged(monkeypatch):
    async def _fake_read(**kw):
        return {"state": "LEAVE", "actionable": False, "display": "x"}

    async def _fake_tg(text):
        raise AssertionError("should not send when unchanged")

    monkeypatch.setattr(rsvc, "live_read", _fake_read)
    monkeypatch.setattr(rsvc.gold_scan, "_tg", _fake_tg)
    monkeypatch.setattr(rsvc, "_read_last", lambda: "LEAVE")
    monkeypatch.setattr(rsvc, "_write_last", lambda s: None)

    out = _run(rsvc.state_alert(notify=True))
    assert out["changed"] is False and out["sent"] is False
