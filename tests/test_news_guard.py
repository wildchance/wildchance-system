import asyncio
import datetime as dt

from services.news_guard import (
    symbol_currencies, nfp_window, filter_high_impact, _first_friday, news_flag,
)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_symbol_currencies_fx_pair():
    assert symbol_currencies("EUR/USD") == {"EUR", "USD"}
    assert symbol_currencies("GBPUSD") == {"GBP", "USD"}
    assert symbol_currencies("USD/JPY") == {"USD", "JPY"}


def test_symbol_currencies_metals_indices_are_usd():
    assert symbol_currencies("XAU/USD") == {"USD"}
    assert symbol_currencies("NAS100") == {"USD"}


def test_first_friday_is_nfp_day():
    # June & July 2026 NFP dates
    assert _first_friday(2026, 6) == dt.date(2026, 6, 5)
    assert _first_friday(2026, 7) == dt.date(2026, 7, 3)


def test_nfp_window_flags_adjacent_day():
    assert nfp_window(dt.date(2026, 7, 2)) == "2026-07-03"   # day before NFP
    assert nfp_window(dt.date(2026, 7, 20)) is None          # quiet week


def test_news_flag_is_forward_looking_not_backward():
    # NFP for July 2026 is Fri Jul 3 (deterministic clock, no network needed).
    nfp = _first_friday(2026, 7)
    assert nfp == dt.date(2026, 7, 3)
    # day BEFORE the print → flagged (it's upcoming)
    assert _run(news_flag(dt.date(2026, 7, 2), "USD/JPY", win=1)) is not None
    # day OF the print → flagged
    assert _run(news_flag(dt.date(2026, 7, 3), "USD/JPY", win=1)) is not None
    # day AFTER the print → NOT flagged: the outcome already happened, no longer a
    # fade risk. This is the fix for the alert repeating news we already had.
    assert _run(news_flag(dt.date(2026, 7, 4), "USD/JPY", win=1)) is None
    # a quiet non-USD-adjacent window stays clean
    assert _run(news_flag(dt.date(2026, 7, 20), "EUR/GBP", win=1)) is None


def test_filter_high_impact_by_currency_and_impact():
    events = [
        {"date": "2026-07-29", "ccy": "USD", "event": "Federal Funds Rate", "impact": "High"},
        {"date": "2026-07-23", "ccy": "EUR", "event": "Main Refinancing Rate", "impact": "High"},
        {"date": "2026-07-10", "ccy": "CAD", "event": "Ivey PMI", "impact": "Medium"},
    ]
    usd = filter_high_impact(events, {"USD"})
    assert len(usd) == 1 and usd[0]["event"] == "Federal Funds Rate"
    # CAD Ivey PMI is medium and not a tier-1 keyword -> excluded
    allrows = filter_high_impact(events)
    assert {r["ccy"] for r in allrows} == {"USD", "EUR"}
