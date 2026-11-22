"""Operator gate: engines need a token once OPS_TOKEN/CRON_TOKEN exists; public surface stays open."""

from fastapi.testclient import TestClient

import services.access as ac


def _client(monkeypatch, token="ops123"):
    monkeypatch.setattr(ac, "ops_token", lambda: token)
    from main import app
    return TestClient(app)


def test_engines_locked_public_open(monkeypatch):
    c = _client(monkeypatch)
    for path in ("/send_alert", "/gold/scan?notify=true", "/gold/stratops/deploy",
                 "/candlerange/crt?session=ny&execute=true", "/trade", "/signal"):
        assert c.post(path).status_code == 401, path
    assert c.get("/zconfluence/scan").status_code == 401
    assert c.get("/docs").status_code == 401
    for path in ("/", "/pricing", "/members", "/status", "/health", "/saas/tiers", "/macro/brief"):
        assert c.get(path).status_code != 401, path
    assert c.post("/cron/tick").status_code in (403, 503)   # gate passes; cron has its own auth


def test_ops_token_header_query_and_internal(monkeypatch):
    c = _client(monkeypatch)
    assert c.get("/lunar/state", headers={"X-Ops-Token": "ops123"}).status_code == 200
    assert c.get("/lunar/state?token=ops123").status_code == 200
    assert c.get("/lunar/state?token=wrong").status_code == 401
    assert c.get("/lunar/state", headers={"X-Internal-Token": ac.INTERNAL_TOKEN}).status_code == 200


def test_gate_off_without_tokens(monkeypatch):
    c = _client(monkeypatch, token=None)
    assert c.get("/lunar/state").status_code == 200
