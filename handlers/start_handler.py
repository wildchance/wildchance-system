from telegram import Update
from telegram.ext import ContextTypes


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome to Wildchance Trading Bot!\n\n"
        "*Market reads*\n"
        "🧭 /edge — EdgeFinder bias board (all pairs)\n"
        "🎯 /bias EURUSD — one pair's factor breakdown\n"
        "📡 /setup EURUSD [long|short] — Entry/SL/TP card\n"
        "🗓️ /calendar [USD,EUR] — high-impact events\n\n"
        "*USD/JPY forward test*\n"
        "📊 /summary — scoreboard (wins, R, verdict)\n"
        "📈 /history — last 10 trades\n"
        "🗒️ /last5 — last 5 trades\n"
        "🏆 /wins — win/loss tally\n"
        "💰 /profit — total result in R",
        parse_mode="Markdown",
    )
