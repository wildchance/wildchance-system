"""Telegram bot in WEBHOOK mode (ideal for Render free / sleepy hosts).

Instead of long-polling (which needs an always-on process), Telegram POSTs each
update to /telegram/webhook. That inbound request wakes the service and the
update is processed on the spot — so the bot responds even after the host has
been asleep. No Updater / polling loop is created.

Env vars:
  TELEGRAM_BOT_TOKEN or BOT_TOKEN   the full BotFather token (required)
  TELEGRAM_CHAT_ID                  default chat for /send_alert
  TELEGRAM_WEBHOOK_SECRET           optional shared secret (recommended)
  WEBHOOK_BASE_URL                  public base URL; on Render, RENDER_EXTERNAL_URL
                                    is used automatically if this is unset.
"""

from fastapi import APIRouter, FastAPI, Request, HTTPException
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from handlers.start_handler import start
from handlers.history_handler import handle_history
from handlers.last5_handler import handle_last5
from handlers.profit_handler import handle_profit
from handlers.wins_handler import handle_wins
from handlers.summary_handler import handle_summary
from handlers.admin_handler import handle_admin
from handlers.create_signal_handler import handle_create_signal
import os

router = APIRouter()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
WEBHOOK_PATH = "/telegram/webhook"
# On Render, RENDER_EXTERNAL_URL is injected automatically (e.g. https://app.onrender.com).
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL") or os.getenv("RENDER_EXTERNAL_URL")
WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")

application = None
if BOT_TOKEN:
    # updater(None): no polling loop — we feed updates in via the webhook route.
    application = ApplicationBuilder().token(BOT_TOKEN).updater(None).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("history", handle_history))
    application.add_handler(CommandHandler("last5", handle_last5))
    application.add_handler(CommandHandler("profit", handle_profit))
    application.add_handler(CommandHandler("wins", handle_wins))
    application.add_handler(CommandHandler("summary", handle_summary))
    application.add_handler(CommandHandler("admin", handle_admin))
    application.add_handler(CommandHandler("create_signal", handle_create_signal))

    async def echo(update, context: ContextTypes.DEFAULT_TYPE):
        if update.message and update.message.text:
            await update.message.reply_text(f"You said: {update.message.text}")

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))
else:
    print("[telegram] TELEGRAM_BOT_TOKEN/BOT_TOKEN not set — bot disabled")


async def _start_and_register_webhook():
    if application is None:
        return
    try:
        await application.initialize()
        await application.start()
        if WEBHOOK_BASE_URL:
            url = WEBHOOK_BASE_URL.rstrip("/") + WEBHOOK_PATH
            kwargs = {"url": url, "drop_pending_updates": True,
                      "allowed_updates": Update.ALL_TYPES}
            if WEBHOOK_SECRET:
                kwargs["secret_token"] = WEBHOOK_SECRET
            await application.bot.set_webhook(**kwargs)
            print(f"🤖 Telegram webhook registered: {url}")
        else:
            print("[telegram] WEBHOOK_BASE_URL/RENDER_EXTERNAL_URL not set — "
                  "webhook NOT registered (set it so Telegram can reach the bot)")
    except Exception as e:
        print(f"❌ Telegram webhook setup failed: {e}")


def register_bot(app: FastAPI):
    @app.on_event("startup")
    async def _startup():
        if application is not None:
            await _start_and_register_webhook()

    @app.on_event("shutdown")
    async def _shutdown():
        if application is None:
            return
        try:
            await application.bot.delete_webhook()
            await application.stop()
            await application.shutdown()
            print("✅ Telegram bot stopped")
        except Exception as e:
            print(f"❌ Error stopping bot: {e}")


@router.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    """Receive an update from Telegram and process it."""
    if application is None:
        raise HTTPException(status_code=503, detail="bot not configured")
    # Verify Telegram's secret-token header if a secret is configured.
    if WEBHOOK_SECRET and request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="invalid secret token")
    data = await request.json()
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return {"ok": True}


@router.post("/send_alert")
async def send_alert(message: dict):
    if application is None:
        return {"status": "No bot configured"}
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not chat_id:
        return {"status": "No chat_id configured"}
    try:
        await application.bot.send_message(chat_id=chat_id, text=message["message"])
        return {"status": "Alert sent"}
    except Exception as e:
        return {"status": "Failed", "error": str(e)}
