import asyncio

import httpx

from services import ai_sentiment as ai


def run(c):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(c)
    finally:
        loop.close()


class _Resp:
    def __init__(self, code, body):
        self.status_code, self._body = code, body
        self.text = str(body)

    def json(self):
        return self._body


def _client(replies, seen):
    class C:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            seen.append(json["model"])
            return replies.pop(0)
    return C


def test_retired_model_falls_back_and_success_recorded(monkeypatch):
    seen = []
    replies = [_Resp(404, {"detail": "Model not found"}),
               _Resp(200, {"choices": [{"message": {"content": "ok"}}]})]
    monkeypatch.setattr(ai, "NVIDIA_API_KEY", "k")
    monkeypatch.setattr(ai, "NVIDIA_MODEL", "old/model")
    monkeypatch.setattr(ai, "FALLBACK_MODELS", ["new/model"])
    monkeypatch.setattr(ai, "_down_until", {"t": 0.0})
    monkeypatch.setattr(httpx, "AsyncClient", _client(replies, seen))
    assert run(ai.ask_model([{"role": "user", "content": "hi"}])) == "ok"
    assert seen == ["old/model", "new/model"] and ai.LAST["ok"] and ai.LAST["model"] == "new/model"


def test_rejected_key_reason_kept_and_cooldown(monkeypatch):
    seen = []
    monkeypatch.setattr(ai, "NVIDIA_API_KEY", "k")
    monkeypatch.setattr(ai, "_down_until", {"t": 0.0})
    monkeypatch.setattr(httpx, "AsyncClient", _client([_Resp(401, {"detail": "Unauthorized"})], seen))
    assert run(ai.ask_model([{"role": "user", "content": "hi"}])) is None
    assert ai.LAST["status"] == 401 and "Unauthorized" in ai.LAST["error"]
    assert run(ai.ask_model([{"role": "user", "content": "hi"}])) is None and len(seen) == 1   # cooling down
    from routes import ops_check
    monkeypatch.setattr(ai, "ask_model", lambda m: asyncio.sleep(0))
    r = run(ops_check.ai_status())
    assert r["key_set"] and "rejected the key" in r["hint"]


def test_no_key_reason(monkeypatch):
    monkeypatch.setattr(ai, "NVIDIA_API_KEY", None)
    assert run(ai.ask_model([])) is None and ai.LAST["status"] == "no key"


def test_failure_logged_once_per_hour_and_config_errors_back_off(monkeypatch):
    from services import errors
    seen, logged = [], []
    monkeypatch.setattr(errors, "record", lambda kind, where, detail="", ms=None: logged.append(detail))
    monkeypatch.setattr(ai, "NVIDIA_API_KEY", "k")
    monkeypatch.setattr(ai, "_logged", {"key": None, "t": 0.0})
    for _ in range(3):
        monkeypatch.setattr(ai, "_down_until", {"t": 0.0})
        monkeypatch.setattr(httpx, "AsyncClient", _client([_Resp(401, {"detail": "Unauthorized"})], seen))
        run(ai.ask_model([{"role": "user", "content": "hi"}]))
    assert len(seen) == 3 and len(logged) == 1                     # 3 failures, 1 log line
    import time
    assert ai._down_until["t"] - time.time() > 5 * 3600            # bad key: back off for hours
