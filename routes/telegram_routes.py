from fastapi import APIRouter, FastAPI
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from handlers.start_handler import start
from handlers.history_handler import handle_history
from handlers.last5_handler import handle_last5
from handlers.profit_handler import handle_profit
from handlers.wins_handler import handle_wins
from handlers.summary_handler import handle_summary
from handlers.admin_handler import handle_admin
import asyncio
import os

router = APIRouter()

# Accept either env-var name; if neither is set, the bot is simply disabled
# (the app still boots and alerts via httpx still work).
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")

application = None
if BOT_TOKEN:
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("history", handle_history))
    application.add_handler(CommandHandler("last5", handle_last5))
    application.add_handler(CommandHandler("profit", handle_profit))
    application.add_handler(CommandHandler("wins", handle_wins))
    application.add_handler(CommandHandler("summary", handle_summary))
    application.add_handler(CommandHandler("admin", handle_admin))

    async def echo(update, context: ContextTypes.DEFAULT_TYPE):
        # Safe echo — guards against NoneType messages.
        if update.message and update.message.text:
            await update.message.reply_text(f"You said: {update.message.text}")

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))
else:
    print("[telegram] TELEGRAM_BOT_TOKEN/BOT_TOKEN not set — interactive bot disabled")


async def start_telegram_bot():
    """Start polling INSIDE the existing event loop.

    Must NOT use Application.run_polling() here — that helper creates and closes
    its own event loop, which raises 'Cannot close a running event loop' when
    called from within uvicorn's running loop. The initialize/start/
    updater.start_polling sequence is the correct embedded pattern.
    """
    if application is None:
        return
    try:
        await application.initialize()
        await application.start()
        await application.updater.start_polling(drop_pending_updates=True)
        print("🤖 Telegram bot is now polling and receiving messages")
    except Exception as e:
        print(f"❌ Telegram bot failed to start: {e}")


def register_bot(app: FastAPI):
    @app.on_event("startup")
    async def _startup():
        if application is not None:
            asyncio.create_task(start_telegram_bot())

    @app.on_event("shutdown")
    async def _shutdown():
        if application is None:
            return
        print("🛑 Shutting down Telegram bot...")
        try:
            await application.updater.stop()
            await application.stop()
            await application.shutdown()
            print("✅ Telegram bot stopped")
        except Exception as e:
            print(f"❌ Error stopping bot: {e}")


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
