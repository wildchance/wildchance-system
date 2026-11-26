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
    assert any("points elsewhere" in p for p in r["problems"]) and any("timed out" in p for p in r["problems"])
    monkeypatch.setattr(tr, "application", None)
    assert "bot disabled" in run(ops_check.telegram_status())["reason"]


def test_glued_command_arguments_recovered():
    from handlers import hub_handler as hub
    assert hub.split_glued("/trialme@mail.com", hub.HANDLERS) == ("trial", ["me@mail.com"])
    assert hub.split_glued("/wallsEURUSD", hub.HANDLERS) == ("walls", ["EURUSD"])
    assert hub.split_glued("/walletadd tron T1", hub.HANDLERS) is None          # a real command, not glued
    assert hub.split_glued("/xyz", hub.HANDLERS) is None
    assert "carry" in hub.HANDLERS and hub.LEGACY_COMMANDS >= {"admin", "setup"}
