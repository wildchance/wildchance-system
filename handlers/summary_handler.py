from telegram import Update
from telegram.ext import ContextTypes

from database.db import AsyncSessionLocal
from services import usdjpy_service as svc


def _fmt(x):
    return "—" if x is None else f"{x:+.2f}"


def _pct(x):
    return "—" if x is None else f"{x * 100:.0f}%"


async def handle_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """The live USD/JPY scoreboard."""
    try:
        async with AsyncSessionLocal() as db:
            sb = await svc.get_scoreboard(db)

        msg = (
            "📊 USD/JPY Scoreboard\n"
            f"Trades: {sb['trades_completed']}\n"
            f"Wins: {sb['wins']}   Losses: {sb['losses']}\n"
            f"Win rate: {_pct(sb['win_rate'])}\n"
            f"Total R: {_fmt(sb['total_r'])}\n"
            f"Expectancy: {_fmt(sb['expectancy_r'])} R/trade\n"
            f"Profit factor: {_fmt(sb['profit_factor'])}\n"
            f"Verdict: {sb['verdict']}"
        )
        await update.message.reply_text(msg)

    except Exception as e:
        await update.message.reply_text(f"❌ Error fetching summary: {e}")
