from telegram import Update
from telegram.ext import ContextTypes


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome to Wildchance Trading Bot!\n\n"
        "Commands:\n"
        "📊 /summary — USD/JPY scoreboard (wins, R, verdict)\n"
        "📈 /history — last 10 trades\n"
        "🗒️ /last5 — last 5 trades\n"
        "🏆 /wins — win/loss tally\n"
        "💰 /profit — total result in R"
    )
