import asyncio

from handlers import hub_handler as hub


class Msg:
    def __init__(self, text):
        self.text = text
        self.out = []

    async def reply_text(self, text, **k):
        self.out.append(text)


class Chat:
    def __init__(self, cid):
        self.id = cid


class Upd:
    def __init__(self, text, cid=1):
        self.message = Msg(text)
        self.effective_chat = Chat(cid)


class Ctx:
    def __init__(self, args=None):
        self.args = args or []


def _run(fn, text, args=None, cid=1):
    u = Upd(text, cid)
    asyncio.new_event_loop().run_until_complete(fn(u, Ctx(args)))
    return u.message.out


def test_every_command_answers(monkeypatch):
    from services import signal_tracker as stk

    async def load():
        return [{"symbol": "EURUSD", "side": "SELL", "result": "win", "pips": 24.4, "source": "z-setup",
                 "closed_at": 1795600000, "entry": 1.09637, "stop": 1.0975, "stop_now": 1.09637, "hits": [{"tp": 1}],
                 "targets": [1.09393]}]
    monkeypatch.setattr(stk, "_load", load)
    for name, fn in hub.HANDLERS.items():
        out = _run(fn, f"/{name}", ["EURUSD"] if name == "walls" else None)
        assert out and out[0], name
    assert "24.4" in _run(hub.recent_cmd, "/recent")[0]
    assert "1 closed" in _run(hub.results_cmd, "/results")[0]


def test_owner_only(monkeypatch):
    monkeypatch.setattr(hub, "OWNER_CHAT_ID", "42")
    assert "Owner only" in _run(hub.open_cmd, "/open", cid=7)[0]
    assert "Owner only" not in _run(hub.health_cmd, "/health", cid=42)[0]
    assert "/open" in _run(hub.help_cmd, "/help", cid=42)[0] and "/open" not in _run(hub.help_cmd, "/help", cid=7)[0]


def test_old_names_map_to_new_reads():
    assert hub.HANDLERS["summary"] is hub.results_cmd and hub.HANDLERS["history"] is hub.recent_cmd
