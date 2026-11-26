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


@pytest.fixture(autouse=True)
def _price_pool_offline(monkeypatch):
    """The free price pool calls Binance / Yahoo / Tiingo — keep unit tests offline."""
    try:
        import services.price_pool as pp
        monkeypatch.setattr(pp, "POOL_ENABLED", False)
    except Exception:
        pass
    yield


@pytest.fixture(autouse=True)
def _no_internet(monkeypatch):
    """Unit tests never touch the internet: an outbound TCP connect fails at once (as if the
    host were down), so a slow or blocked network cannot make the suite slow or flaky.
    In-process ASGI clients and the event loop's own socketpairs are unaffected."""
    import socket
    real = socket.socket.connect

    def guarded(self, addr):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            raise OSError("network disabled in unit tests")
        return real(self, addr)
    monkeypatch.setattr(socket.socket, "connect", guarded)

    real_ex = socket.socket.connect_ex

    def guarded_ex(self, addr):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            return 111                                    # ECONNREFUSED
        return real_ex(self, addr)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_ex)
    yield
