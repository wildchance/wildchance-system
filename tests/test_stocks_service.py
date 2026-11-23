import asyncio

from services import stocks_service as ss


def test_polygon_parse_and_keyless_noop():
    data = {"results": [{"t": 1767225600000, "o": 1, "h": 2, "l": 0.5, "c": 1.5},
                        {"t": 1767139200000, "o": 1.1, "h": 2.1, "l": 0.6, "c": 1.6}]}
    bars = ss.parse_polygon(data)
    assert len(bars) == 2 and bars[0][0] < bars[1][0] and bars[1][4] == 1.5
    assert ss.is_stock("NVDA") and not ss.is_stock("EUR/USD") and not ss.is_stock("DXY")
    loop = asyncio.new_event_loop()
    try:
        if not ss.POLYGON_KEY:
            assert loop.run_until_complete(ss.daily("NVDA")) == []
        if not ss.FINNHUB_KEY:
            assert loop.run_until_complete(ss.quote("NVDA")) is None
    finally:
        loop.close()
