"""Tests for the signal builder (manual path + formatting + gate)."""

import pytest

from services import signal_engine as se


def test_build_manual_shape_and_r():
    sig = se.build_manual("EURUSD", "long", entry=1.0850, stop=1.0820,
                          tps=[1.0900, 1.0950, 1.1000])
    assert sig["symbol"] == "EURUSD" and sig["side"] == "long"
    assert sig["source"] == "manual"
    assert len(sig["targets"]) == 3
    # R rises with distance: (50/30, 100/30, 150/30)
    rs = [t["r"] for t in sig["targets"]]
    assert rs[0] < rs[1] < rs[2]
    assert sig["targets"][0]["r"] == pytest.approx(round(0.0050 / 0.0030, 2))


def test_gate_present_and_blocks_logic():
    sig = se.build_manual("EURUSD", "short", 1.10, 1.105, [1.09, 1.08, 1.07])
    assert "gate" in sig and "allow" in sig["gate"]
    # one conservative risk unit on a 2500 acct is within the daily cap
    assert sig["gate"]["allow"] is True


def test_format_card_contains_zones():
    sig = se.build_manual("XAUUSD", "long", 1900, 1890, [1910, 1920, 1940])
    card = se.format_card(sig)
    assert "XAUUSD" in card and "Entry" in card and "TP1" in card
    assert "GATE" in card


def test_qr_payload_compact():
    sig = se.build_manual("EURUSD", "long", 1.085, 1.082, [1.09, 1.095])
    p = se.qr_payload(sig)
    assert p.startswith("WILDCHANCE|EURUSD|LONG|")
    assert "E:1.085" in p and "SL:1.082" in p and "TP:1.09,1.095" in p


def test_make_qr_returns_bytes_or_none():
    # Either qrcode is installed (bytes) or not (None) — must not raise.
    out = se.make_qr("WILDCHANCE|TEST")
    assert out is None or isinstance(out, bytes)


def test_buy_sell_aliases_via_assemble():
    # build_manual lowercases side; 'long'/'short' expected from handler
    sig = se.build_manual("GBPUSD", "short", 1.30, 1.305, [1.29])
    assert sig["side"] == "short"
    assert sig["targets"][0]["r"] is not None
