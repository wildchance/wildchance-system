from gold.signal import assemble, format_card


def _profile(bias="long", name="Classic Tuesday Low"):
    return {"bias": bias, "profile": name, "profile_id": 1,
            "reason": "Tuesday low into discount", "zone": "discount"}


def test_no_trade_on_neutral_profile():
    s = assemble({"bias": "neutral", "profile": "Seek and Destroy", "reason": "avoid"},
                 4325, 5000)
    assert s["signal"] == "NO TRADE"


def test_long_signal_sizing_and_targets():
    s = assemble(_profile("long"), 4325.0, 5000.0, risk_usd=20.0, sl_pips=200.0)
    assert s["signal"] == "LONG"
    assert s["stop"] == 4305.0                     # 200 pips × $0.10 = $20 below
    assert s["lot"] == 0.01                         # $20 risk / ($20 move × $100) = 0.01
    assert s["risk_usd"] == 20.0
    prices = [t["price"] for t in s["targets"]]
    assert prices == [4365.0, 4385.0, 4405.0]       # 1:2 / 1:3 / 1:4
    usd = [t["usd"] for t in s["targets"]]
    assert usd == [40.0, 60.0, 80.0]                # $ at 0.01 lot
    assert s["breakeven"] == 4345.0                 # 50% to TP1


def test_short_signal_direction():
    s = assemble(_profile("short", "Classic Tuesday High"), 4325.0, 5000.0)
    assert s["signal"] == "SHORT" and s["stop"] == 4345.0
    assert [t["price"] for t in s["targets"]] == [4285.0, 4265.0, 4245.0]


def test_lot_capped_at_max_lot():
    # a huge risk request can't exceed max_lot(5000)=0.04
    s = assemble(_profile("long"), 4325.0, 5000.0, risk_usd=1000.0, sl_pips=200.0)
    assert s["lot"] == 0.04


def test_gate_present_and_card_renders():
    s = assemble(_profile("long"), 4325.0, 5000.0)
    assert "gate" in s
    card = format_card(s)
    assert "GOLD XAU/USD" in card and "Classic Tuesday Low" in card and "TP" in card


def test_no_trade_card():
    card = format_card({"signal": "NO TRADE", "profile": "Seek and Destroy", "reason": "avoid"})
    assert "no trade" in card.lower()
