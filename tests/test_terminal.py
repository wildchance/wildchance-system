def test_terminal_page_and_data_are_private_and_serve(monkeypatch):
    from fastapi.testclient import TestClient
    import main
    from services import access
    monkeypatch.setattr(access, "ops_token", lambda: "t0k")
    c = TestClient(main.app)
    assert c.get("/terminal/data").status_code == 401                 # data needs the token…
    r = c.get("/terminal")                                            # …the page shell has none
    assert c.get("/terminal/data", headers={"X-Ops-Token": "t0k"}).status_code == 200
    assert r.status_code == 200 and "WILDCHANCE TERMINAL" in r.text
    d = c.get("/terminal/data?token=t0k")
    assert d.status_code == 200
    j = d.json()
    for k in ("strength", "rotation", "exposure", "signal_stats", "regimes", "walls", "calendar", "audit"):
        assert k in j
