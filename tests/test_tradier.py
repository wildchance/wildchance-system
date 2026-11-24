import datetime as dt

from services import options_levels as ol


def test_parse_tradier_shape_and_levels():
    today = dt.date(2026, 11, 24)
    rows = [{"expiration_date": "2026-12-04", "strike": 330, "option_type": "call", "open_interest": 5000,
             "greeks": {"gamma": 0.02, "mid_iv": 0.27}},
            {"expiration_date": "2026-12-04", "strike": 340, "option_type": "call", "open_interest": 9000,
             "greeks": {"gamma": 0.01}},
            {"expiration_date": "2026-12-04", "strike": 320, "option_type": "put", "open_interest": 7000,
             "greeks": {"gamma": 0.015}},
            {"expiration_date": "2027-06-18", "strike": 300, "option_type": "put", "open_interest": 99999},
            {"bad": 1}]
    ch = ol.parse_tradier(rows, 331.0, today)
    assert set(ch["strikes"]) == {330.0, 340.0, 320.0}          # far expiry dropped
    assert ch["atm_iv"] == 0.27 and ch["days"] == 10
    lv = ol.levels(ch)
    assert lv["inventory_ceiling"] == 340.0 and lv["primary_floor"] == 320.0


def test_parse_polygon_maps_to_levels():
    today = dt.date(2026, 11, 24)
    res = [{"details": {"contract_type": "call", "expiration_date": "2026-12-04", "strike_price": 340},
            "open_interest": 9000, "greeks": {"gamma": 0.01}, "implied_volatility": 0.27,
            "underlying_asset": {"price": 331.0}},
           {"details": {"contract_type": "put", "expiration_date": "2026-12-04", "strike_price": 320},
            "open_interest": 7000, "greeks": {"gamma": 0.015}}]
    ch = ol.parse_polygon(res, today)
    lv = ol.levels(ch)
    assert ch["spot"] == 331.0 and lv["inventory_ceiling"] == 340 and lv["primary_floor"] == 320
    assert ol.parse_polygon([{"details": {}}]) == {}
