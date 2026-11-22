"""Owner's backtested FX lot sheet reproduced exactly, plus the prop risk cap."""

from propfirm.target_lots import ladder, pip_value_per_lot


def _lots(out):
    return [r["lot"] for r in out["rungs"]]


def test_matches_sheet_2500():
    out = ladder(2500, "EURUSD", max_risk_pct=1.0)          # sheet risk ≤ $10 = 0.4 %
    assert out["target_usd"] == 13.0
    assert _lots(out) == [0.10, 0.07, 0.05, 0.04, 0.04, 0.03, 0.03]
    r = out["rungs"][5]
    assert r["tp_pips"] == 45.5 and r["profit_at_tp"] == 13.65 and r["risk_at_sl"] == 3.0


def test_scaling_and_5k_row():
    big = _lots(ladder(25000, "EURUSD", target_usd=130, max_risk_pct=1.0))
    assert big == [1.0, 0.67, 0.5, 0.4, 0.34, 0.29, 0.25]            # ≈ ×10 of the 2.5k row
    five = _lots(ladder(5000, "EURUSD", target_usd=25, max_risk_pct=1.0))
    assert five[0] == 0.20 and five[2] == 0.10 and five[3] == 0.08 and five[-1] == 0.05


def test_risk_cap_protects_small_accounts():
    out = ladder(2500, "EURUSD", max_risk_pct=0.3)           # cap = $7.5 → 0.07 lot max
    assert out["max_lot_by_risk"] == 0.07
    first = out["rungs"][0]
    assert first["lot"] == 0.07 and first["capped_by_risk"] and first["risk_pct"] <= 0.3


def test_pip_values():
    assert pip_value_per_lot("EURUSD") == 10.0 and pip_value_per_lot("XAUUSD") == 10.0
    assert pip_value_per_lot("USDJPY", 150.0) == 6.6667
    assert pip_value_per_lot("EURGBP") is None
    assert "error" in ladder(2500, "EURGBP")
    assert _lots(ladder(2500, "EURGBP", pip_value=12.7, max_risk_pct=1.0))[0] == 0.08


def test_route():
    from fastapi.testclient import TestClient
    from main import app
    r = TestClient(app).get("/propfirm/lots?balance=2500&max_risk_pct=1").json()
    assert [x["lot"] for x in r["rungs"]][:3] == [0.10, 0.07, 0.05]


def test_card_lines_eurusd_first_signal():
    from propfirm.target_lots import card_lines
    # the first live signal: SELL limit 1.09701, SL 1.09801 (10 pips), TP 1.09246 (45.5 pips)
    lines = card_lines("EUR/USD", 1.09701, 1.09801, [{"name": "TP", "price": 1.09246}],
                       balances=[2500, 5000])
    assert lines[0].startswith("*Lots*") and "SL 10.0 pips" in lines[0]
    assert "`2.5k`: TP 0.03 (+$13.65/−$3)" in lines[1]
    assert "`5k`: TP 0.06" in lines[2]
    assert card_lines("US500", 6000, 5950, [{"name": "TP", "price": 6100}]) == []   # not FX


def test_card_lines_jpy_and_cap():
    from propfirm.target_lots import card_lines
    lines = card_lines("USD/JPY", 150.0, 150.1, [{"name": "TP1", "price": 149.87}], balances=[2500])
    assert "SL 10.0 pips" in lines[0] and "`2.5k`: TP1" in lines[1]
    tight = card_lines("EURUSD", 1.1, 1.1002, [{"name": "TP", "price": 1.0998}], balances=[2500])
    assert "*" in tight[1] and tight[-1].startswith("_*")          # 2-pip TP → capped


def test_signal_engine_card_includes_ladder():
    from services.signal_engine import build_manual, format_card
    sig = build_manual("EUR/USD", "short", 1.09701, 1.09801, [1.09246], balance=2500)
    card = format_card(sig)
    assert "*Lots*" in card and "`2.5k`" in card
