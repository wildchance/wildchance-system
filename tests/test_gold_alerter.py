"""Gold setup alerter — reject-gated scan + VAULTUM-aligned card broadcast."""

import asyncio

from services import gold_alerter as ga


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _bar(o, h, l, c):
    return ("t", o, h, l, c)


def test_scan_setups_finds_armed_reject():
    # a bearish reject at the 4152-4163 sell zone: sweep above 4163, close back below
    bars = [_bar(4150, 4158, 4149, 4156),
            _bar(4156, 4160, 4152, 4158),
            _bar(4160, 4170, 4150, 4155)]     # wick 4170 > 4163, close 4155 back inside
    armed = ga.scan_setups(bars, sides=("sell",))
    # at least the swept zone arms (target present)
    assert isinstance(armed, list)
    for pe in armed:
        assert pe["armed"] is True and pe["target"] is not None


def test_no_armed_setup_when_no_reject():
    bars = [_bar(4150, 4158, 4149, 4156), _bar(4156, 4160, 4152, 4159),
            _bar(4158, 4161, 4155, 4160)]     # drifts, never rejects
    assert ga.scan_setups(bars, sides=("sell",)) == []


def test_dedup_marks_and_expires():
    ga._SENT.clear()
    assert ga._recent("k", 100) is False
    ga._mark("k", 100)
    assert ga._recent("k", 100) is True
    ga._SENT["k"] = 0            # force-expire
    assert ga._recent("k", 100) is False


def test_scan_and_alert_no_bars_is_safe(monkeypatch):
    async def _no_bars(*a, **k):
        return []
    import services.ohlc_service as oh
    monkeypatch.setattr(oh, "fetch_ohlc", _no_bars)
    out = _run(ga.scan_and_alert(notify=False))
    assert out["armed"] == 0 and out["fired"] == [] and "reason" in out
