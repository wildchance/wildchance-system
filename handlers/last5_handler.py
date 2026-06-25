from telegram import Update
from telegram.ext import ContextTypes

from database.db import AsyncSessionLocal
from services import usdjpy_service as svc


async def handle_last5(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """The 5 most recent USD/JPY trades."""
    try:
        async with AsyncSessionLocal() as db:
            trades = await svc.list_trades(db, limit=5)

        if not trades:
            await update.message.reply_text("📭 No recent trades found.")
            return

        lines = []
        for t in trades:
            r = t["result_r"]
            tail = f"{r:+.2f}R" if r is not None else "open"
            lines.append(
                f"{t['entry_date']} · {t['action']} @ {t['entry']} "
                f"(stop {t['stop']:.3f}) → {tail}"
            )
        await update.message.reply_text("📊 Last 5 Trades:\n" + "\n".join(lines))

    except Exception as e:
        await update.message.reply_text(f"❌ Error fetching last 5 trades: {e}")
