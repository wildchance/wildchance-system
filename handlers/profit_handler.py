from telegram import Update
from telegram.ext import ContextTypes

from database.db import AsyncSessionLocal
from services import usdjpy_service as svc


async def handle_profit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Total result in R (the strategy is measured in risk units, not money —
    actual $ depends on your lot size / account)."""
    try:
        async with AsyncSessionLocal() as db:
            sb = await svc.get_scoreboard(db)

        total_r = sb["total_r"]
        exp = sb["expectancy_r"]
        msg = f"💰 Total: {total_r:+.2f}R over {sb['trades_completed']} trades"
        if exp is not None:
            msg += f"\nExpectancy: {exp:+.2f}R per trade"
        await update.message.reply_text(msg)

    except Exception as e:
        await update.message.reply_text(f"❌ Error fetching profit: {e}")
