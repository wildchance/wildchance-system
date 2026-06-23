from fastapi import APIRouter, Request, HTTPException, Depends
from decouple import config
from sqlalchemy.ext.asyncio import AsyncSession
from database.db import get_db
from models.signal_model import SignalLog
from utils.send_telegram import send_telegram_message

router = APIRouter()
# default=None so a missing secret does not crash the app at import/startup.
WEBHOOK_SECRET = config("WEBHOOK_SECRET", default=None)


@router.post("/webhook/alertatron")
async def alertatron_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    # If no secret is configured, reject rather than accepting unauthenticated calls.
    if not WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail="Webhook not configured")

    body = await request.json()

    if body.get("secret") != WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")

    symbol = body.get("symbol")
    action = body.get("action")
    strength = body.get("strength", 3)

    new_log = SignalLog(symbol=symbol, action=action, strength=strength)
    db.add(new_log)
    await db.commit()

    await send_telegram_message(f"📢 {symbol} → {action} (Strength: {strength})")

    return {"status": "success", "message": "Alert logged & sent"}
