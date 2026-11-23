import asyncio

from services import flash_card as fc
from routes.cbdr import px


def test_pips_match_owner_samples():
    assert fc.pips("US30", "BUY", 42512.0, 42818.43) == 3064.3
    assert fc.pips("EUR/JPY", "SELL", 164.52, 163.981) == 53.9
    assert fc.pips("XAU/USD", "BUY", 4000.0, 4025.0) == 250.0          # cheetah = 250 pips
    assert fc.pips("BTC/USD", "SELL", 61000.0, 60500.0) == 500.0
    assert fc.pips("EUR/USD", "BUY", 1.1, 1.09) < 0


def test_render_png_and_losers_not_posted():
    png = fc.render("US30", "BUY", 42512.0, 42818.43, qr_url="https://t.me/x")
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 10_000
    loop = asyncio.new_event_loop()
    try:
        r = loop.run_until_complete(fc.post("EUR/USD", "BUY", 1.10, 1.09))
    finally:
        loop.close()
    assert r == {"sent": False, "reason": "not a winning trade"}
    assert "US30 BUY +3,064.3 pips" in fc.caption("US30", "BUY", 42512.0, 42818.43)


def test_prelondon_card_formats_prices():
    # the grey-zone line used to print raw floats (3501.0123300000005)
    vals = [3501.0123300000005, 3467.075320000001]
    assert "–".join(px("XAU/USD", v) for v in vals) == "3501.01–3467.08"


def test_telegram_float_noise_is_tidied():
    from services.signal_feed import tidy_numbers, _tidy_request
    import httpx, json
    t = "Grey buy 3501.0123300000005, 3467.075320000001 · sell 3704.6343899999997 · EURUSD 1.09902 · JPY 151.81900000001"
    out = tidy_numbers(t)
    assert out == "Grey buy 3501.01, 3467.08 · sell 3704.63 · EURUSD 1.09902 · JPY 151.819"
    assert tidy_numbers("1.10131999999999 and 0.5") == "1.10132 and 0.5"
    req = httpx.Request("POST", "https://api.telegram.org/botX/sendMessage",
                        json={"chat_id": 1, "text": "sell 3704.6343899999997"})
    new = _tidy_request(req)
    assert json.loads(new.content)["text"] == "sell 3704.63" and json.loads(new.content)["chat_id"] == 1


def test_head_requests_on_public_pages():
    from fastapi.testclient import TestClient
    import main
    c = TestClient(main.app)
    for path in ("/", "/pricing", "/members", "/status"):
        assert c.head(path).status_code == 200, path
