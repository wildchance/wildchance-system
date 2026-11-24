import datetime as dt

from services import fx_rotation as fx


def test_pair_naming_follows_market_convention():
    assert fx.pair_of("EUR", "USD") == ("EUR/USD", True)
    assert fx.pair_of("JPY", "GBP") == ("GBP/JPY", False)
    assert fx.pair_of("AUD", "CHF") == ("AUD/CHF", True)
    assert fx.pair_of("CAD", "NZD") == ("NZD/CAD", False)
    assert fx.legs("USD/JPY") == ("USD", "JPY") and fx.legs("XAU/USD") == ("XAU", "USD") and fx.legs("NAS100") is None


def test_strength_from_components_and_pairings():
    feed = {"cot": {"AUD": [{"lev": x} for x in (0, -10, -20, -30)], "JPY": [{"lev": x} for x in (-5, 0, 5, 10)]},
            "retail": {"EUR/USD": {"long_pct": 80}, "USD/JPY": {"long_pct": 30}}}
    comp = fx.components({"EUR": -0.5, "GBP": 0.2, "JPY": 0.8, "AUD": -1.2, "NZD": -0.9, "CHF": 0.6, "CAD": -0.1},
                         feed, {"AUD/USD": 1.64, "USD/JPY": 3.18})
    st = fx.score(comp)
    order = list(st)
    assert order[0] in ("JPY", "CHF") and order[-1] in ("AUD", "NZD")
    p = fx.pairings(st, n=1)[0]
    assert p["strong"] == order[0] and p["weak"] == order[-1]
    assert p["side"] == ("SELL" if p["symbol"].startswith(p["weak"]) else "BUY")


def test_rotation_blocks_event_currency():
    import services.econ_calendar as ec
    now = dt.datetime(2026, 11, 25, 12, 0, tzinfo=dt.timezone.utc)
    ev = [{"title": "Core PCE", "ccy": "USD", "time_utc": "2026-11-25T13:30+00:00", "impact": "High"},
          {"title": "CPI", "ccy": "AUD", "time_utc": "2026-11-26T00:30+00:00", "impact": "High"}]
    b = fx.blocked_currencies(ev, now)
    assert list(b) == ["USD"]
    st = fx.score({"momentum": {"USD": 0.9, "EUR": 0.5, "GBP": -0.6, "JPY": 0.7, "CHF": 0.2, "CAD": 0.0,
                                "AUD": -0.8, "NZD": -0.4}})
    pairs = fx.pairings(st, exclude=tuple(b), n=8, min_gap=0.3)
    assert pairs and all("USD" not in p["symbol"] for p in pairs)


def test_exposure_cap_blocks_stacked_usd_bet():
    open_ = [{"symbol": "EUR/USD", "side": "SELL"}, {"symbol": "GBP/USD", "side": "SELL"}]
    assert fx.net_exposure(open_)["USD"] == 2
    r = fx.exposure_check("AUD/USD", "SELL", open_, cap=2)
    assert not r["allow"] and "USD long 3/2" in r["line"]
    assert fx.exposure_check("EUR/GBP", "BUY", open_, cap=2)["allow"]
    assert fx.exposure_check("NAS100", "BUY", open_, cap=2)["allow"]
