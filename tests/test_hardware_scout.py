from services import hardware_scout as hs

R = {"kes_per_usd": 129.0, "fx_buffer": 0.02, "freight": 40.0, "freight_server": 150.0, "forwarder": 15.0,
     "duty": 0.0, "vat": 0.16, "idf": 0.035, "rdl": 0.02, "min_margin": 0.25}


def test_landed_cost_kenya():
    lc = hs.landed_kes(400.0, 20.0, "NVIDIA RTX 3090 24GB", R)
    cif = 400 + 20 + 40 + 15
    assert lc["cif_usd"] == cif and lc["vat_usd"] == round(cif * 0.16, 2)
    assert lc["landed_kes"] == round(cif * (1 + 0.16 + 0.035 + 0.02) * 129 * 1.02)
    assert hs.landed_kes(400, 20, "Dell PowerEdge R740 server", R)["cif_usd"] == 400 + 20 + 150 + 15


def test_price_match_longest_phrase():
    prices = {"rtx 3090": 145000, "rtx 3090 founders": 150000, "r740": 115000}
    assert hs.match_price("NVIDIA RTX 3090 Founders Edition", prices) == ("rtx 3090 founders", 150000)
    assert hs.match_price("Dell PowerEdge R740 2x Gold", prices) == ("r740", 115000)
    assert hs.match_price("Coffee maker", prices) is None


def test_parse_and_evaluate():
    data = {"itemSummaries": [
        {"itemId": "v1|1", "title": "NVIDIA RTX 3090 24GB GDDR6X", "price": {"value": "520.00", "currency": "USD"},
         "shippingOptions": [{"shippingCost": {"value": "25.00"}}], "condition": "Used",
         "itemWebUrl": "https://ebay/1", "seller": {"feedbackPercentage": "99.6", "feedbackScore": 4210}},
        {"itemId": "v1|2", "title": "broken", "price": {}}]}
    items = hs.parse_ebay(data)
    assert len(items) == 1 and items[0]["ship_usd"] == 25.0 and items[0]["seller_pct"] == 99.6
    ev = hs.evaluate(items[0], {"rtx 3090": 145000}, R)
    assert ev["landed_kes"] < 145000 and ev["worth_it"] == (ev["margin"] >= 0.25)
    assert hs.evaluate(items[0], {"r740": 1}, R) is None


def test_ledger_totals():
    led = [{"id": 1, "desc": "a", "cost_kes": 100000, "sold_kes": 145000}, {"id": 2, "desc": "b", "cost_kes": 50000}]
    t = hs.ledger_totals(led, vat_registered=True)
    assert t["sold"] == 1 and t["in_stock"] == 1 and t["output_vat_kes"] == round(145000 * 16 / 116)
    assert t["profit_kes"] == 145000 - 100000 - t["output_vat_kes"]
    assert hs.ledger_totals(led, False)["profit_kes"] == 45000
