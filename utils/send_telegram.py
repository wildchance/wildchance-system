import requests
import os

BOT_TOKEN = os.getenv("BOT_TOKEN")
BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

def _tidy(text):
    try:
        from services.signal_feed import tidy_numbers
        return tidy_numbers(text)
    except Exception:
        return text


def send_telegram_message(chat_id: str, message: str):
    url = f"{BASE_URL}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": _tidy(message)
    }
    response = requests.post(url, json=payload)
    return response.json()
