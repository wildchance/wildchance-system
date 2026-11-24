import datetime as dt

from services import options_levels as ol
from services import sweeper as sw


def test_zones_need_two_maps_and_rank_by_closeness():
    lv = [{"price": 1.1000, "src": "daily CBDR", "label": "d +1"}, {"price": 1.1004, "src": "options", "label": "put wall"},
          {"price": 1.1100, "src": "weekly CBDR", "label": "w +2"}, {"price": 1.1102, "src": "weekly CBDR", "label": "w x"},
          {"price": 1.0950, "src": "yearly", "label": "y"}, {"price": 1.0952, "src": "daily CBDR", "label": "d -1"}]
    z = sw.zones(lv, 0.003, 1.0990)
    assert [round(x["mid"], 4) for x in z][0] == 1.1002          # nearest 2-map zone first
    assert all(len(x["sources"]) >= 2 for x in z)
    assert not any(abs(x["mid"] - 1.1101) < 1e-4 for x in z)     # same-source pair isn't a zone


def test_last_complete_box_and_ladder():
    by_day = {dt.date(2026, 11, 23): (1.0, 1.1), dt.date(2026, 11, 24): (1.2, 1.3)}
    assert sw.last_complete_box(by_day, dt.datetime(2026, 11, 24, 15)) == (1.0, 1.1)   # today still forming
    assert sw.last_complete_box(by_day, dt.datetime(2026, 11, 24, 21)) == (1.2, 1.3)
    lad = sw.ladder_levels((1.0, 1.1), "daily CBDR")
    assert len(lad) == 17 and abs(lad[0]["price"] - (1.1 - 0.4)) < 1e-9


def test_inverse_fx_and_deribit_mapping():
    lv = {"inventory_ceiling": 70.0, "primary_floor": 60.0}
    m = {x["key"]: x["price"] for x in ol.mapped(lv, 1.0, inverse=True, und_spot=65.0, live=150.0, dp=3)}
    assert m["primary_floor"] == round(150 * 65 / 70, 3)          # FXY call wall = USD/JPY floor
    assert m["inventory_ceiling"] == round(150 * 65 / 60, 3)
    rows = [{"instrument_name": "BTC-25DEC26-90000-C", "open_interest": 900, "underlying_price": 88000, "mark_iv": 55},
            {"instrument_name": "BTC-25DEC26-80000-P", "open_interest": 1200, "underlying_price": 88000, "mark_iv": 60}]
    ch = ol.parse_deribit(rows, today=dt.date(2026, 11, 24))
    assert ch["spot"] == 88000 and ch["strikes"][90000.0]["call_oi"] == 900
    lv2 = ol.levels(ch)
    assert lv2["inventory_ceiling"] == 90000.0 and lv2["primary_floor"] == 80000.0
    assert ol.underlying_for("BTC/USD") == "DERIBIT:BTC" and ol.underlying_for("USD/JPY") == "FXY"
