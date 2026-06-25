from telegram import Update
from telegram.ext import ContextTypes

from database.db import AsyncSessionLocal
from services import usdjpy_service as svc


async def handle_wins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Win/loss tally from the USD/JPY scoreboard."""
    try:
        async with AsyncSessionLocal() as db:
            sb = await svc.get_scoreboard(db)

        await update.message.reply_text(
            f"🏆 Wins: {sb['wins']} / {sb['trades_completed']} completed "
            f"(losses: {sb['losses']})"
        )

    except Exception as e:
        await update.message.reply_text(f"❌ Error fetching wins: {e}")
