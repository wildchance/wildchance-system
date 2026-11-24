"""Private messages to the OWNER (not the signal channel): weekend CME bulletin reminder.

OWNER_CHAT_ID = your own Telegram user id (message @userinfobot to see it), and press
Start on the bot once so it may write to you. Without it nothing is sent.
"""

from __future__ import annotations

import datetime as dt
from typing import Dict

import httpx
from decouple import config

OWNER_CHAT_ID = config("OWNER_CHAT_ID", default=None)
BOT_TOKEN = config("TELEGRAM_BOT_TOKEN", default=None) or config("BOT_TOKEN", default=None)
STALE_DAYS = int(config("CME_STALE_DAYS", default="3"))


async def send(text: str) -> bool:
    if not (OWNER_CHAT_ID and BOT_TOKEN):
        return False
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                             json={"chat_id": OWNER_CHAT_ID, "text": text, "disable_web_page_preview": True})
        return r.status_code == 200
    except Exception:
        return False


def bulletin_message(ages: Dict[str, int]) -> str:
    lines = ["📥 Weekend reminder — CME Daily Bulletin",
             "Dealer levels are getting old: " + ", ".join(f"{k} {v} days" for k, v in ages.items()),
             "",
             "1. cmegroup.com/market-data/daily-bulletin.html — Friday's bulletin",
             "2. Download sections 62 + 64 (gold), 27–30 + 33–36 (GBP, CAD, JPY, CHF), 39 (EUR)",
             "3. GitHub → wildchance-system → data/cme → Add file → Upload files → commit",
             "Render picks them up on the next refresh (01:40 / 14:40 UTC)."]
    return "\n".join(lines)


async def cme_reminder(notify: bool = False) -> Dict:
    from services import cme_bulletin as cb
    today = dt.date.today()
    ages: Dict[str, int] = {}
    g = await cb.cached_chain(max_age_days=365)
    ages["gold"] = (today - dt.date.fromisoformat(g["asof"])).days if g else 999
    for p in ("EURUSD", "USDJPY", "GBPUSD", "USDCHF", "USDCAD"):
        c = await cb.cached_fx_chain(p, max_age_days=365)
        ages[p] = (today - dt.date.fromisoformat(c["asof"])).days if c else 999
    stale = {k: v for k, v in ages.items() if v >= STALE_DAYS}
    out = {"ages_days": ages, "stale": bool(stale), "owner_chat": bool(OWNER_CHAT_ID)}
    if notify and stale:
        out["sent"] = await send(bulletin_message(stale))
    return out
