"""Signal card — the shareable WILDCHANCE card math."""

from gold import signal_card as sc


def test_matches_the_graphic():
    # the exact numbers on the branded card: entry 4000, SL 3975, TP 4110
    c = sc.build_signal_card(entry=4000, stop=3975, tp=4110)
    assert c["side"] == "BUY"
    assert c["risk_points"] == 25.0 and c["reward_points"] == 110.0
    assert round(c["risk_pct"], 2) == 0.63
    assert round(c["reward_pct"], 2) == 2.75
    assert round(c["risk_reward"], 2) == 4.40
    assert c["potential_profit_pct"] == c["reward_pct"]
    assert c["probability"] == "HIGH PROBABILITY SETUP"
    assert c["valid_geometry"] is True


def test_side_inferred_and_sell_geometry():
    c = sc.build_signal_card(entry=4094, stop=4110, tp=4035)   # the 15M OB sell
    assert c["side"] == "SELL" and c["valid_geometry"] is True
    assert c["reward_points"] == 59.0 and c["risk_points"] == 16.0


def test_bad_geometry_flagged_not_crashed():
    c = sc.build_signal_card(entry=4000, stop=4050, tp=4110, side="BUY")  # stop above entry
    assert c["valid_geometry"] is False and c["warnings"]


def test_render_url_and_telegram_text():
    c = sc.build_signal_card(entry=4000, stop=3975, tp=4110)
    assert "signal_card.html" in c["render_url"] and "entry=4000" in c["render_url"]
    txt = sc.format_card_telegram(c)
    assert "XAUUSD BUY" in txt and "4110" in txt and "1 : 4.40" in txt


def test_probability_tiers():
    assert sc.build_signal_card(4000, 3990, 4015)["probability"] == "MODERATE SETUP"  # rr 1.5
    assert sc.build_signal_card(4000, 3990, 4025)["probability"] == "GOOD SETUP"       # rr 2.5
