"""Tests for the Wildchance Confluence Engine scraper's pure logic.

These cover the offline, deterministic parts of wildchance/wildchance_scraper.py
(number parsing, COT state, the signal verdict engine, NFP scoring). The
network fetchers are intentionally not exercised — they all fall back to
snapshots, which the integration smoke run in the audit already covers.

Stdlib-only, matching the project's existing pytest suite.
"""

import sys
from pathlib import Path

import pytest

# The engine lives in the sibling wildchance/ folder, not on the default path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "wildchance"))

from wildchance_scraper import (  # noqa: E402
    WATCH,
    _num,
    base_code,
    cot_state,
    evaluate,
    pct_rank,
    score_nfp,
)


# ---------------- _num: ForexFactory value parsing ----------------
def test_num_parses_jobs_and_units():
    assert _num("172K") == 172
    assert _num("1.2M") == 1200          # M = 1000 * K
    assert _num("4.3%") == 4.3
    assert _num("-17.7K") == -17.7
    assert _num("1,200") == 1200         # thousands separator stripped


def test_num_blanks_are_none():
    assert _num("") is None
    assert _num("-") is None
    assert _num(None) is None
    assert _num("not-a-number") is None


# ---------------- pct_rank ----------------
def test_pct_rank():
    assert pct_rank([10, 20, 30], 30) == 1.0
    assert pct_rank([10, 20, 30], 10) == pytest.approx(1 / 3)
    assert pct_rank([], 5) == 0.5        # empty -> neutral


# ---------------- base_code / WATCH wiring ----------------
def test_base_code_and_usdjpy_sign_inverts():
    assert base_code("USD/JPY") == "JPY"
    assert base_code("XAU/USD") == "XAU"
    assert base_code("unknown") is None
    # USD-quote pairs invert: a long-JPY COT means USD/JPY goes DOWN.
    assert WATCH["USD/JPY"]["sign"] == -1
    assert WATCH["EUR/USD"]["sign"] == +1


# ---------------- cot_state ----------------
def test_cot_state_computes_net_delta_rank_dir():
    series = [{"lev": -30}, {"lev": -20}, {"lev": -10}]
    c = cot_state({"EUR": series}, "EUR")
    assert c["net"] == -10
    assert c["dwk"] == 10               # last - prev = -10 - (-20)
    assert c["rank"] == 1.0             # -10 is the top of the range
    assert c["dir"] == -1              # net < 0 -> short bias


def test_cot_state_missing_is_none():
    assert cot_state({}, "EUR") is None


# ---------------- evaluate: the verdict engine ----------------
def test_evaluate_flat_inside_band():
    sig = evaluate("EUR/USD", 1.15, {"long_pct": 52, "short_pct": 48}, {})
    assert sig["verdict"] == "FLAT"
    assert sig["confidence"] == 42


def test_evaluate_contrarian_short_without_cot():
    # Crowd heavily long, no COT to confirm -> contrarian SHORT (matches the
    # XAU/USD line from the audit smoke run: SHORT, conf 86).
    sig = evaluate("XAU/USD", 4325.0, {"long_pct": 73, "short_pct": 27}, {})
    assert sig["verdict"] == "SHORT"
    assert sig["confidence"] == 86
    assert sig["cot_net"] is None


def test_evaluate_short_confirmed_by_cot_gets_rank_bonus():
    # Crowd long, COT also short on EUR (sign +1) -> they agree -> SHORT,
    # with the COT-rank conviction bonus folded into confidence.
    cot = {"EUR": [{"lev": -30}, {"lev": -20}, {"lev": -10}]}
    sig = evaluate("EUR/USD", 1.15, {"long_pct": 60, "short_pct": 40}, cot)
    assert sig["verdict"] == "SHORT"
    assert sig["cot_agrees"] is True
    assert sig["cot_net"] == -10
    assert sig["cot_dwk"] == 10
    assert sig["cot_rank"] == 1.0
    assert sig["confidence"] == 86      # 50 + (60-50)*1.6 + |1.0-0.5|*40


def test_evaluate_watch_when_cot_disagrees():
    # Crowd long (contrarian short) but COT is long on EUR -> conflict -> WATCH.
    cot = {"EUR": [{"lev": 10}, {"lev": 20}]}
    sig = evaluate("EUR/USD", 1.15, {"long_pct": 70, "short_pct": 30}, cot)
    assert sig["verdict"] == "WATCH"
    assert sig["cot_agrees"] is False
    assert sig["confidence"] == 47


# ---------------- score_nfp ----------------
def test_score_nfp_hot_print_is_usd_up():
    events = [{"event": "Non-Farm Employment Change", "date": "2026-06-05",
               "actual": 172, "forecast": 85, "previous": 179}]
    nfp = score_nfp(events, [])
    assert nfp["surprise_k"] == 87
    assert nfp["usd_direction"] == "up"
    assert nfp["beat_pct"] == 102       # round(87/85*100)


def test_score_nfp_picks_latest_and_handles_empty():
    assert score_nfp([], []) == {}
    events = [
        {"event": "Non-Farm Employment Change", "date": "2026-05-01",
         "actual": 100, "forecast": 120, "previous": 90},
        {"event": "Non-Farm Employment Change", "date": "2026-06-05",
         "actual": 50, "forecast": 90, "previous": 100},
    ]
    nfp = score_nfp(events, [])
    assert nfp["date"] == "2026-06-05"  # latest by date
    assert nfp["surprise_k"] == -40
    assert nfp["usd_direction"] == "down"
