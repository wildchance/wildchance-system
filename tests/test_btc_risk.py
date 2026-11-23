from propfirm import target_lots as tl
from routes.cbdr import px
from services import trade_gate as tg


def test_btc_risk_mirrors_gold():
    assert tg.min_stop_distance("BTC/USD") == tg.MIN_SL_BTC_USD == 50.0
    assert tg.widen_stop("BTCUSD", "BUY", 61600.0, 61590.0) == 61550.0       # pushed to $50
    assert tl.pip_size("BTC/USD") == 1.0 and tl.pip_value_per_lot("BTCUSD") == 1.0
    assert tl.pip_size("XAU/USD") == 0.1 and tl.pip_value_per_lot("XAUUSD") == 10.0
    assert px("BTC/USD", 61629.4912) == "61629.49"


def test_btc_lot_card_sizes_by_usd_moves():
    lines = tl.card_lines("BTC/USD", 60000.0, 59500.0, [{"name": "TP1", "price": 61000.0}],
                          balances=[100000.0])
    assert lines and "SL 500.0 pips" in lines[0]
    assert "`100k`:" in lines[1]          # $500 stop → 0.5 % of 100k allows 1.00 lot
