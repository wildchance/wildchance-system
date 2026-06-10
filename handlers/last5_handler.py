from telegram import Update
from telegram.ext import ContextTypes
import requests
import os

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")

async def handle_last5(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        response = requests.get(f"{API_BASE_URL}/history/trades?limit=5")
        if response.status_code == 200 and response.json():
            trades = response.json()
            msg = "📊 Last 5 Trades:\n\n"
            for t in trades:
                msg += f"{t['pair']} | {t['action']} | {t['price']} | {t['created_at']}\n"
        else:
            msg = "⚠ No recent trades found."
    except Exception as e:
        msg = f"❌ Error fetching last 5 trades: {e}"

    await update.message.reply_text(msg)
