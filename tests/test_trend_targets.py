"""Trend-extension TP ladder wired into the live gold card."""

import asyncio

from services import structure_service as ss
from gold.signal import assemble_structured
from gold.intraday import format_card


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_trend_targets_fallback_projects_the_bullish_ladder():
    # No API key in test → the 4H fetch is empty, so this exercises the fallback:
    # impulse = ref_low→ref_high, anchor = entry. Bullish gold, chart numbers.
    tl = _run(ss.trend_targets("XAU/USD", "long", 4119.68,
                               ref_high=4116.95, ref_low=3964.01))
    assert tl is not None
    assert tl["side"] == "long"
    assert tl["source"] == "reference range → entry anchor"
    # measured move (1.0) ≈ the chart's "1" target ~4,267
    assert 4260 < tl["measured_move"] < 4280
    # every target is above entry and ordered outward
    prices = [t["price"] for t in tl["targets"]]
    assert prices == sorted(prices)
    assert all(p > 4119.68 for p in prices)
    assert tl["targets"][0]["label"] == "TP1"
    # pct move to each target is positive for a long
    assert all(t["pct"] > 0 for t in tl["targets"])


def test_trend_targets_short_projects_down():
    tl = _run(ss.trend_targets("XAU/USD", "short", 4000.0,
                               ref_high=4100.0, ref_low=3950.0))
    assert tl is not None and tl["side"] == "short"
    assert all(t["price"] < 4000.0 for t in tl["targets"])
    assert all(t["pct"] < 0 for t in tl["targets"])


def test_trend_targets_none_without_reference_or_data():
    # no ref range + empty fetch → nothing to project
    assert _run(ss.trend_targets("XAU/USD", "long", 4000.0)) is None
    # unusable bias
    assert _run(ss.trend_targets("XAU/USD", "flat", 4000.0,
                                 ref_high=4100, ref_low=3900)) is None


def test_format_trend_lines_shared_renderer():
    from gold.signal import format_trend_lines
    tl = {"targets": [{"label": "TP1", "ratio": 1.0, "price": 160.2, "pct": 1.6}],
          "measured_move": 160.2, "source": "4h pivots LHL"}
    out = format_trend_lines({"trend_targets": tl})
    assert "🎯 Trend TPs:" in out and "TP1 160.2" in out and "measured move" in out
    # absent → empty string (never crashes a card)
    assert format_trend_lines({}) == ""
    assert format_trend_lines({"trend_targets": {"targets": []}}) == ""


def test_forex_card_renders_ladder():
    # generic multi-pair card (EUR/USD, GBP/USD, …) surfaces the ladder
    from services.signal_engine import format_card
    sig = {"symbol": "EUR/USD", "side": "long", "mode": "continuation",
           "source": "auto", "window_label": "CBDR", "entry": 1.085,
           "stop_loss": 1.082, "targets": [{"name": "TP1", "price": 1.091, "r": 2.0}],
           "sizing": {"max_lot": 0.5, "tier": 6, "balance": 2500},
           "gate": {"allow": True, "reason": "ok"},
           "trend_targets": {"targets": [{"label": "TP1", "ratio": 1.0,
                                          "price": 1.093, "pct": 0.74}],
                             "measured_move": 1.093, "source": "4h pivots LHL"}}
    card = format_card(sig)
    assert "🎯 Trend TPs:" in card


def test_intraday_card_does_not_double_render_ladder():
    # base card + intraday extras must show the ladder exactly ONCE
    from gold.intraday import format_card as intraday_card
    sig = assemble_structured({"profile": "Bull", "bias": "long",
                               "week_high": 4116.95, "week_low": 3964.01},
                              4119.68, 4090.0, 5000.0, tier="6",
                              risk_usd=20.0, rr=(2, 3, 4))
    sig["signal"] = "LONG"
    sig["layers"] = {"session": {"quarter": 3, "phase": "distribution"},
                     "fld": {"cross": "bull"}, "weekday": None}
    sig["trend_targets"] = _run(ss.trend_targets("XAU/USD", "long", 4119.68,
                                                 ref_high=4116.95, ref_low=3964.01))
    assert intraday_card(sig).count("Trend TPs:") == 1


def test_card_renders_trend_tp_ladder():
    prof = {"profile": "Bullish Expansion", "bias": "long",
            "week_high": 4116.95, "week_low": 3964.01}
    sig = assemble_structured(prof, 4119.68, 4090.0, 5000.0,
                              tier="6", risk_usd=20.0, rr=(2, 3, 4))
    sig["signal"] = "LONG"
    sig["layers"] = {"session": {"quarter": 3, "phase": "distribution"},
                     "fld": {"cross": "bull"}, "weekday": None}
    sig["trend_targets"] = _run(ss.trend_targets("XAU/USD", "long", 4119.68,
                                                 ref_high=4116.95, ref_low=3964.01))
    card = format_card(sig)
    assert "🎯 Trend TPs:" in card
    assert "measured move" in card
    assert "TP1" in card
