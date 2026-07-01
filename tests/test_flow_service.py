"""Tests for the flow_service confluence mapping (pure part)."""

from services.flow_service import confluence


def test_no_flow_data():
    c = confluence(None, "long")
    assert c["flow"] is None and c["agreement"] == "none"


def test_flow_confirms_long():
    p = {"pressure": "long", "state": "bid_heavy", "imbalance": 0.5}
    c = confluence(p, "long")
    assert c["agreement"] == "confirms"


def test_flow_diverges_from_long():
    p = {"pressure": "short", "state": "ask_heavy", "imbalance": -0.5}
    c = confluence(p, "long")
    assert c["agreement"] == "diverges"


def test_flow_confirms_short():
    p = {"pressure": "short", "state": "ask_heavy", "imbalance": -0.4}
    assert confluence(p, "short")["agreement"] == "confirms"


def test_flow_neutral():
    p = {"pressure": "neutral", "state": "balanced", "imbalance": 0.05}
    assert confluence(p, "long")["agreement"] == "neutral"
