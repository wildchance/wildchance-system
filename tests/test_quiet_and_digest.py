from services import council_gate as cg


def test_quiet_market_is_blocked():
    assert "too quiet" in cg.quiet_reason("NZD/USD", {"atr_pct": 60, "adr_pips": 41.0})
    assert "bottom" in cg.quiet_reason("EUR/USD", {"atr_pct": 8, "adr_pips": 80.0})
    assert cg.quiet_reason("EUR/USD", {"atr_pct": 55, "adr_pips": 72.0}) is None
    assert cg.quiet_reason("XAU/USD", {"atr_pct": 40, "adr_pips": 30.0}) is None   # ADR floor is FX-only
    assert cg.quiet_reason("NZD/USD", None) is None


def test_digest_labels_positioning_leans_with_the_council():
    src = open("routes/alerts.py").read()
    assert "Positioning leans" in src and "watch only — council" in src and "council_gate.check" in src


def test_ea_fails_closed_by_default():
    src = open("mt5_bridge/WildChance_PropEA_v4.mq5").read()
    assert "InpGateFailOpen      = false" in src and "Sleep(1500); g = AppGate" in src
