from types import SimpleNamespace as NS

from handlers import hub_handler as hub
from services import owner_notify


def upd(chat, user):
    return NS(effective_chat=NS(id=chat), effective_user=NS(id=user))


def test_owner_matches_user_or_chat_and_tolerates_paste(monkeypatch):
    monkeypatch.setattr(hub, "OWNER_CHAT_ID", ' "123456789" ')
    assert hub.owner_ids() == {"123456789"}
    assert hub.is_owner(upd(123456789, 123456789))                 # private chat
    assert hub.is_owner(upd(-1001234567890, 123456789))            # owner typing in a group
    assert not hub.is_owner(upd(555, 555))
    monkeypatch.setattr(hub, "OWNER_CHAT_ID", "123456789, 987654321")
    assert hub.is_owner(upd(987654321, 987654321))


def test_denied_text_shows_ids(monkeypatch):
    monkeypatch.setattr(hub, "OWNER_CHAT_ID", "")
    assert "not set" in hub.denied_text(upd(1, 777)) and "777" in hub.denied_text(upd(1, 777))
    monkeypatch.setattr(hub, "OWNER_CHAT_ID", "123")
    t = hub.denied_text(upd(-100, 777))
    assert "chat id: -100" in t and "user id: 777" in t


def test_notify_first_id():
    assert owner_notify._first_id(' "123", 456 ') == "123" and owner_notify._first_id("") is None
