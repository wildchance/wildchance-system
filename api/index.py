"""Vercel serverless entrypoint for the Wildchance API.

Serverless functions don't keep a process alive, so this app deliberately OMITS
the always-on pieces that live in the root main.py:
  • the Telegram long-polling bot (routes/telegram_routes.register_bot)
  • the in-process schedulers (start_scanner / start_wildchance_scheduler)

Scheduling is handled instead by the free GitHub Actions cron workflow
(.github/workflows/scheduled-scans.yml), which POSTs /usdjpy/scan and
/wildchance/scrape on a schedule. Outbound Telegram ALERTS still work — they are
plain HTTPS POSTs (services/usdjpy_alert.py) made during a request, which is
fine on serverless.

Vercel's @vercel/python runtime serves the ASGI object named `app` below.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from decouple import config

from database.db import init_db

# Only the self-contained, audited routers — no Telegram polling, no schedulers.
from routes.alert_webhook import router as alert_webhook
from routes.market import router as market_router
from routes.usdjpy import router as usdjpy_router
from routes.portfolio import router as portfolio_router
from routes.wildchance import router as wildchance_router

_db_ready = False


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # create_all is idempotent; runs on the first cold start. Never raises out.
    global _db_ready
    if not _db_ready:
        try:
            await init_db()
            _db_ready = True
        except Exception as e:  # don't let a DB hiccup 500 the whole function
            print(f"[startup] init_db skipped: {e}")
    yield


app = FastAPI(title="Wildchance API (serverless)", version="1.0.0", lifespan=lifespan)

_origins = config("ALLOWED_ORIGINS", default="*")
ALLOWED_ORIGINS = [o.strip() for o in _origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=ALLOWED_ORIGINS != ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def home():
    return {"message": "Wildchance API (serverless) is live 🚀", "status": "ok"}


@app.get("/health")
async def health():
    return {"status": "healthy"}


for _r in (alert_webhook, market_router, usdjpy_router,
           portfolio_router, wildchance_router):
    app.include_router(_r)
