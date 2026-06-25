from telegram import Update
from telegram.ext import ContextTypes

from database.db import AsyncSessionLocal
from services import usdjpy_service as svc


async def handle_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Last 10 USD/JPY trades, read straight from the database."""
    try:
        async with AsyncSessionLocal() as db:
            trades = await svc.list_trades(db, limit=10)

        if not trades:
            await update.message.reply_text("📭 No USD/JPY trades yet.")
            return

        lines = []
        for t in trades:
            r = t["result_r"]
            tail = f"{r:+.2f}R ({t['status']})" if r is not None else t["status"]
            lines.append(f"{t['entry_date']} · {t['action']} @ {t['entry']} → {tail}")
        await update.message.reply_text("📈 USD/JPY Trades (last 10):\n" + "\n".join(lines))

    except Exception as e:
        await update.message.reply_text(f"⚠️ Error fetching history: {e}")
