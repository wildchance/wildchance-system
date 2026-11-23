from routes.cbdr import px


def test_price_precision():
    assert px("EUR/USD", 1.1013199999999999) == "1.10132"
    assert px("EUR/USD", 1.0874700000000002) == "1.08747"
    assert px("USD/JPY", 160.17268999999996) == "160.173"
    assert px("XAU/USD", 3540.1234) == "3540.12"
    assert px("NAS100", 21000.456) == "21000.46"
    assert px("EUR/USD", None) == "None"
