
from saas import passwords as pw


def test_hash_verify_and_strength():
    h = pw.hash_password("correct horse battery", iterations=1000)
    assert h.startswith("pbkdf2$1000$") and pw.verify_password("correct horse battery", h)
    assert not pw.verify_password("wrong", h) and not pw.verify_password("x", "garbage")
    assert pw.strong_enough("longenough1") and not pw.strong_enough("short") \
        and not pw.strong_enough("1234567890123")


def test_attempt_limiter():
    k = "e:test@x.io"
    pw.clear(k)
    for _ in range(pw.MAX_FAILS):
        assert not pw.locked(k)
        pw.record_fail(k)
    assert pw.locked(k)
    pw.clear(k)
    assert not pw.locked(k)


def test_login_route_end_to_end(monkeypatch, tmp_path):
    import asyncio
    from fastapi.testclient import TestClient
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker
    import database.db as dbm
    import models.user_model  # noqa: F401
    from main import app
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'pw.db'}")     # isolated DB
    Session = sessionmaker(bind=eng, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(dbm, "AsyncSessionLocal", Session)

    async def _db():
        async with Session() as s:
            yield s
    app.dependency_overrides[dbm.get_db] = _db

    async def mk():
        async with eng.begin() as c:
            await c.run_sync(dbm.Base.metadata.create_all)
    loop = asyncio.new_event_loop(); loop.run_until_complete(mk()); loop.close()
    monkeypatch.setattr(pw, "ITERATIONS", 1000)
    try:
        c = TestClient(app)
        key = c.post("/saas/signup?email=Pw.Test@Example.com").json().get("api_key")
        assert key
        assert c.post("/members/password", json={"password": "short"},
                      headers={"X-API-Key": key}).status_code == 400
        assert c.post("/members/password", json={"password": "a-good-passphrase"},
                      headers={"X-API-Key": key}).json() == {"ok": True}
        bad = c.post("/members/login", json={"email": "pw.test@example.com", "password": "nope-nope-nope"})
        assert bad.status_code == 401
        ok = c.post("/members/login", json={"email": "pw.test@example.com", "password": "a-good-passphrase"})
        assert ok.status_code == 200 and ok.json()["api_key"] == key
    finally:
        app.dependency_overrides.clear()
