import datetime as dt
import time

from services import trade_journal as tj


def test_classify():
    assert tj.classify(25.0, 12.0) == "win"
    assert tj.classify(-30.0, -15.0) == "loss"
    assert tj.classify(0.3, 0.2) == "breakeven"        # stop moved to entry
    assert tj.classify(-2.0, -0.6) == "breakeven"      # within 1 pip
    assert tj.classify(0.4, 8.0) == "breakeven"        # within $0.50


def test_to_row():
    now = int(time.time())
    r = tj.to_row({"ticket": 9, "symbol": "EURUSD", "side": "buy", "volume": 1, "open_price": 1.1000,
                   "close_price": 1.1050, "open_time": now - 3600, "close_time": now, "net": 500, "magic": 7})
    assert r["result"] == "win" and r["side"] == "BUY" and round(r["pips"], 1) == 50.0 and r["magic"] == 7
    assert tj.to_row({"ticket": 1}) is None


def _r(res, net, pips, sym="EURUSD", i=0):
    return {"symbol": sym, "side": "BUY", "net": net, "pips": pips, "result": res,
            "close_time": dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc) + dt.timedelta(hours=i)}


def test_summarise():
    rows = [_r("win", 100, 20, i=0), _r("win", 50, 10, i=1), _r("loss", -60, -12, "XAUUSD", 2),
            _r("breakeven", 0.2, 0.1, i=3), _r("win", 30, 6, i=4)]
    s = tj.summarise(rows)
    assert (s["trades"], s["wins"], s["losses"], s["breakeven"]) == (5, 3, 1, 1)
    assert s["win_rate_pct"] == 75.0 and s["win_rate_incl_be_pct"] == 60.0 and s["breakeven_rate_pct"] == 20.0
    assert s["profit_factor"] == 3.0 and s["max_win_streak"] == 2 and s["max_loss_streak"] == 1
    assert s["equity_curve"][-1]["equity"] == 120.2
    assert s["by_symbol"]["XAUUSD"]["losses"] == 1
    assert tj.summarise([]) == {"trades": 0}


def test_report_card_hides_usd_by_default():
    s = tj.summarise([_r("win", 100, 20), _r("breakeven", 0, 0, i=1)])
    c = tj.report_card("day", s)
    assert "1 win" in c and "1 breakeven" in c and "$" not in c


def test_journal_routes_are_private():
    from services.access import is_public
    assert not is_public("/journal/stats") and not is_public("/journal/trades")
