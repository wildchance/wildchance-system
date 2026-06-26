from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from decouple import config
import uvicorn

from core.signals import router as signals_router
from core.trade import router as trade_router
from core.webhook import router as webhook_router
from database.db import init_db

from routes.telegram_routes import router as telegram_router
from routes.telegram_routes import register_bot
from routes.admin import router as admin_router
from routes.market import router as market_router
from routes.alert_webhook import router as alert_webhook
from routes.history import router as history_router
from routes.history_commands import router as history_cmd_router
from routes.usdjpy import router as usdjpy_router
from routes.portfolio import router as portfolio_router
from routes.wildchance import router as wildchance_router
from routes.alerts import router as alerts_router
from routes.cbdr import router as cbdr_router
from routes.pdarrays import router as pdarrays_router
from routes.instruments import router as instruments_router
from services.usdjpy_scheduler import start_scanner
from services.wildchance_scheduler import start_wildchance_scheduler

app = FastAPI(
    title="Wildchance Trading Bot API",
    description="Trading Bot Dashboard and API",
    version="1.0.0"
)

# Configurable CORS. Set ALLOWED_ORIGINS to a comma-separated list of your real
# dashboard origins (e.g. "https://app.example.com,https://example.com").
# Credentials are only enabled when origins are explicitly listed — a wildcard
# "*" with credentials is rejected by browsers and unsafe, so we disable
# credentials in that case.
_origins = config("ALLOWED_ORIGINS", default="*")
ALLOWED_ORIGINS = [o.strip() for o in _origins.split(",") if o.strip()]
_allow_credentials = ALLOWED_ORIGINS != ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_bot(app)

@app.on_event("startup")
async def startup_event():
    """Initialize the database and launch the background schedulers on startup"""
    await init_db()
    start_scanner()                 # daily USD/JPY mean-reversion scan
    start_wildchance_scheduler()    # 6h/daily/weekly confluence feed scrape

@app.get("/")
async def home():
    return {
        "message": "Wildchance API is live 🚀",
        "status": "Server running",
        "bot": "active"
    }

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy"}

app.include_router(alert_webhook)
app.include_router(signals_router)
app.include_router(trade_router)
app.include_router(webhook_router)
app.include_router(telegram_router)
app.include_router(admin_router)
app.include_router(market_router)
app.include_router(history_router)
app.include_router(history_cmd_router)
app.include_router(usdjpy_router)
app.include_router(portfolio_router)
app.include_router(wildchance_router)
app.include_router(alerts_router)
app.include_router(cbdr_router)
app.include_router(pdarrays_router)
app.include_router(instruments_router)

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        reload=True  # Enable auto-reload in development
    )
