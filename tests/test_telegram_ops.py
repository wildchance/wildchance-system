import asyncio
import datetime as dt
from types import SimpleNamespace as NS


def run(c):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(c)
    finally:
        loop.close()


def test_webhook_keeps_pending_messages():
    src = open("routes/telegram_routes.py").read()
    assert '"drop_pending_updates": False' in src and '"drop_pending_updates": True' not in src


def test_telegram_status_names_the_problem(monkeypatch):
    from routes import ops_check, telegram_routes as tr

    class Bot:
        async def get_webhook_info(self):
            return NS(url="https://old.example/telegram/webhook", pending_update_count=9,
                      last_error_message="Connection timed out",
                      last_error_date=dt.datetime(2026, 11, 26, 7, 0, tzinfo=dt.timezone.utc))

        async def get_me(self):
            return NS(username="wcbot")
    monkeypatch.setattr(tr, "application", NS(bot=Bot()))
    monkeypatch.setattr(tr, "WEBHOOK_BASE_URL", "https://wildchance-system.onrender.com")
    r = run(ops_check.telegram_status())
    assert r["ok"] is False and r["bot"] == "@wcbot" and r["pending_updates"] == 9
    assert any("ANOTHER service" in p for p in r["problems"]) and any("timed out" in p for p in r["problems"])
    monkeypatch.setattr(tr, "application", None)
    assert "bot disabled" in run(ops_check.telegram_status())["reason"]


def test_glued_command_arguments_recovered():
    from handlers import hub_handler as hub
    assert hub.split_glued("/trialme@mail.com", hub.HANDLERS) == ("trial", ["me@mail.com"])
    assert hub.split_glued("/wallsEURUSD", hub.HANDLERS) == ("walls", ["EURUSD"])
    assert hub.split_glued("/walletadd tron T1", hub.HANDLERS) is None          # a real command, not glued
    assert hub.split_glued("/xyz", hub.HANDLERS) is None
    assert "carry" in hub.HANDLERS and hub.LEGACY_COMMANDS >= {"admin", "setup"}


def test_webhook_can_be_pinned_to_one_service(monkeypatch):
    from routes import telegram_routes as tr
    monkeypatch.setattr(tr, "WEBHOOK_BASE_URL", "https://wildchance-system-tjfy.onrender.com")
    monkeypatch.setattr(tr, "PINNED_WEBHOOK", "")
    assert tr.webhook_target() == "https://wildchance-system-tjfy.onrender.com/telegram/webhook"
    monkeypatch.setattr(tr, "PINNED_WEBHOOK", "https://wildchance-system.onrender.com")
    assert tr.webhook_target() == "https://wildchance-system.onrender.com/telegram/webhook"


def test_checklist_flags_sqlite():
    from routes import ops_check
    judge = next(f for name, path, f in ops_check.CHECKS if name == "database")
    assert judge({"db_backend": "sqlite", "db_reachable": True})[0] == "❌"
    assert judge({"db_backend": "postgresql", "db_reachable": True})[0] == "✅"


def test_register_webhook_skips_and_retries(monkeypatch):
    from types import SimpleNamespace as NS
    from telegram.error import RetryAfter
    from routes import telegram_routes as tr
    import asyncio as _a

    async def nosleep(s):
        return None
    monkeypatch.setattr(_a, "sleep", nosleep)
    calls = []

    class Bot:
        def __init__(self, url, err=None, flood=0):
            self.url, self.err, self.flood = url, err, flood

        async def get_webhook_info(self):
            return NS(url=self.url, last_error_message=self.err)

        async def set_webhook(self, **kw):
            calls.append(kw["url"])
            if self.flood:
                self.flood -= 1
                raise RetryAfter(1)
            return True
    u = "https://wildchance-system.onrender.com/telegram/webhook"
    assert run(tr.register_webhook(Bot(u), u)) == "already set" and calls == []
    assert run(tr.register_webhook(Bot(u, err="Wrong response 403"), u)) == "registered"
    assert run(tr.register_webhook(Bot("https://x-tjfy/telegram/webhook", flood=2), u)) == "registered"
    assert run(tr.register_webhook(Bot("", flood=9), u, tries=2)) == "failed (flood control)"


def test_td_out_of_credits_locks_until_midnight(monkeypatch):
    from services import td_limiter as tl
    body = b'{"code":429,"message":"You have run out of API credits for the day. 815 API credits were used","status":"error"}'
    assert tl.out_of_daily_credits(body) and not tl.out_of_daily_credits(b'{"code":429,"message":"per minute"}')
    assert tl.next_utc_midnight(1795651200.0 + 3600) == 1795651200.0 + 86400
    monkeypatch.setitem(tl._persist, "exhausted_until", 10 ** 12)
    assert tl.reserve(now=1000.0) is None
    monkeypatch.setitem(tl._persist, "exhausted_until", 0.0)
