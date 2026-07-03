import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from decouple import config
import uvicorn
import asyncio

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
from routes.correlation import router as correlation_router
from routes.mirofish import router as mirofish_router
from routes.scorecard import router as scorecard_router
from routes.intraday import router as intraday_router
from routes.candlerange import router as candlerange_router
from routes.quarterly import router as quarterly_router
from routes.benner import router as benner_router
from routes.propfirm import router as propfirm_router
from routes.setups import router as setups_router
from routes.emit import router as emit_router
from routes.commodities import router as commodities_router
from routes.flow import router as flow_router
from routes.autoalert import router as autoalert_router
from routes.mmm import router as mmm_router
from routes.calendar import router as calendar_router
from routes.edgefinder import router as edgefinder_router

# Real-time streaming
from services.polygon_stream import polygon_stream
from services.usdjpy_scheduler import start_scanner
from services.wildchance_scheduler import start_wildchance_scheduler


app = FastAPI(
    title="Wildchance Trading Bot API",
    description="Trading Bot Dashboard and API",
    version="1.0.0"
)

# CORS Configuration
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
    """Initialize the database and launch the background schedulers + real-time stream"""
    await init_db()
    
    # Existing schedulers
    start_scanner()                    # daily USD/JPY mean-reversion scan
    start_wildchance_scheduler()       # wildchance 6h/daily/weekly confluence

    # Start real-time Polygon.io WebSocket + Redis cache
    asyncio.create_task(polygon_stream.start())


@app.get("/")
async def home():
    return {
        "message": "Wildchance API is live 🚀",
        "status": "Server running",
        "real_time": "Polygon.io + Redis active"
    }


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy"}


# Include all routers
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
app.include_router(mirofish_router)
app.include_router(scorecard_router)
app.include_router(intraday_router)
app.include_router(candlerange_router)
app.include_router(quarterly_router)
app.include_router(benner_router)
app.include_router(propfirm_router)
app.include_router(setups_router)
app.include_router(emit_router)
app.include_router(commodities_router)
app.include_router(flow_router)
app.include_router(autoalert_router)
app.include_router(mmm_router)
app.include_router(calendar_router)
app.include_router(edgefinder_router)
app.include_router(correlation_router)

# Serve the static dashboards (EdgeFinder board at /static/dashboard/edgefinder.html).
# Guarded so a missing directory never blocks boot.
if os.path.isdir("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        reload=True
    )
