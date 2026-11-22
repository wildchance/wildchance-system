"""Shared test setup.

The lunar gate is ON in production (LUNAR_GATE_ENABLED default true), which makes the
broker queue depend on the calendar date. Execution tests must not flip with the moon,
so the gate is pinned OFF here; tests that exercise it enable it explicitly.
"""

import pytest


@pytest.fixture(autouse=True)
def _lunar_gate_off(monkeypatch):
    try:
        import services.trade_executor as te
        monkeypatch.setattr(te, "LUNAR_GATE_ENABLED", False)
    except Exception:
        pass
    yield
