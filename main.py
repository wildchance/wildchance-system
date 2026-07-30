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

# The Telegram INBOUND bot (command webhook) pulls python-telegram-bot →
# cryptography. That is an OPTIONAL integration: outbound alerts go out over plain
# httpx (services.*._tg), so a broken/absent telegram lib must NOT take down the
# whole trading API. Import it defensively and degrade to a no-op if it fails.
try:
    from routes.telegram_routes import router as telegram_router
    from routes.telegram_routes import bot_startup as _bot_startup
    from routes.telegram_routes import bot_shutdown as _bot_shutdown
    _TELEGRAM_BOT_OK = True
except BaseException as _tg_err:  # ImportError, OR a cryptography rust
                                  # PanicException (which subclasses BaseException,
                                  # not Exception) — so BaseException is required.
    if isinstance(_tg_err, (KeyboardInterrupt, SystemExit)):
        raise
    import logging as _logging
    _logging.getLogger("uvicorn.error").warning(
        "Telegram inbound bot disabled (%s: %s) — outbound alerts still work",
        type(_tg_err).__name__, _tg_err)
    telegram_router = None
    _TELEGRAM_BOT_OK = False

    async def _bot_startup():      # no-op fallbacks
        return None

    async def _bot_shutdown():
        return None

from routes.admin import router as admin_router
from routes.market import router as market_router
from routes.alert_webhook import router as alert_webhook
from routes.history import router as history_router
# Also a Telegram-command module (imports python-telegram-bot); its router carries
# no HTTP endpoints, so degrade to None if the telegram lib is unavailable.
try:
    from routes.history_commands import router as history_cmd_router
except BaseException as _hc_err:
    if isinstance(_hc_err, (KeyboardInterrupt, SystemExit)):
        raise
    history_cmd_router = None
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
from routes.backtest import router as backtest_router
from routes.gold import router as gold_router
from routes.execution import router as execution_router
from routes.saas import router as saas_router
from routes.vaultum import router as vaultum_router
# Complementary intermarket/structure engines (best-of the parallel stream). The AMD
# triad route is intentionally NOT mounted — Bumblebee supersedes it.
from routes.pairs import router as pairs_router
from routes.structure import router as structure_router

# Real-time streaming
from services.polygon_stream import polygon_stream
from services.usdjpy_scheduler import start_scanner
from services.wildchance_scheduler import start_wildchance_scheduler


from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown via the modern lifespan context (on_event is deprecated and
    will be removed — this keeps DB-init + schedulers working across fastapi bumps)."""
    # --- startup ---
    import logging as _log
    _lg = _log.getLogger("uvicorn.error")
    # A DOWN/expired database must NOT crash-loop the whole API. The signal engines
    # (Optimus, VAULTUM, Bumblebee, Venom, …) are stateless compute — they stay live
    # even with no DB; only tracking/execution/SaaS endpoints need it (they 500 per-call
    # until DATABASE_URL is reachable). /health reports db_reachable so you see it.
    try:
        await init_db()
    except Exception as e:
        _lg.error("init_db failed (%s: %s) — API stays UP; DB-backed endpoints will "
                  "error until the database is reachable. Check DATABASE_URL.",
                  type(e).__name__, e)
    for _name, _start in (("usdjpy_scanner", start_scanner),
                          ("wildchance_scheduler", start_wildchance_scheduler)):
        try:
            _start()
        except Exception as e:
            _lg.warning("%s not started (%s) — core API unaffected", _name, e)
    try:
        asyncio.create_task(polygon_stream.start())   # real-time Polygon.io + Redis
    except Exception as e:
        _lg.warning("polygon stream not started (%s) — core API unaffected", e)
    if _TELEGRAM_BOT_OK:
        try:
            await _bot_startup()       # register the Telegram webhook (optional)
        except Exception as e:
            import logging
            logging.getLogger("uvicorn.error").warning("bot_startup skipped: %s", e)
    yield
    # --- shutdown ---
    if _TELEGRAM_BOT_OK:
        try:
            await _bot_shutdown()
        except Exception:
            pass


app = FastAPI(
    title="Wildchance Trading Bot API",
    description="Trading Bot Dashboard and API",
    version="1.0.0",
    lifespan=lifespan,
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

# SaaS tier-gating — OPT-IN (SAAS_GATING_ENABLED=false by default). No-op unless
# turned on, so the current system + crons run unchanged.
try:
    from saas.gating import TierGatingMiddleware
    app.add_middleware(TierGatingMiddleware)
except Exception as _saas_err:
    import logging as _lg
    _lg.getLogger("uvicorn.error").warning("SaaS gating not loaded: %s", _saas_err)

@app.get("/")
async def home():
    return {
        "message": "Wildchance API is live 🚀",
        "status": "Server running",
        "real_time": "Polygon.io + Redis active"
    }


@app.get("/health")
async def health_check():
    """Deep health check — proves the app is actually WIRED, not just 'up'.

    Returns the mounted-route count (a 7-route app = include_router silently
    dropped every router, the boot bug), DB reachability, and the telegram bot
    state. A monitor pinging this catches an 'up but empty' deploy immediately."""
    route_paths = [r.path for r in app.routes if hasattr(r, "path")]
    routes_total = len(route_paths)
    gold_routes = len([p for p in route_paths if p.startswith("/gold")])
    # Healthy only if the real router surface mounted (built-ins alone are ~7).
    wired = routes_total > 100

    db_ok = None
    try:
        from database.db import engine
        from sqlalchemy import text
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    healthy = wired and (db_ok is not False)
    return {
        "status": "healthy" if healthy else "degraded",
        "wired": wired,
        "routes_total": routes_total,
        "gold_routes": gold_routes,
        "db_reachable": db_ok,
        "telegram_bot": "enabled" if _TELEGRAM_BOT_OK else "disabled",
        "execution_mode": ("LIVE" if config("EXECUTION_ENABLED", default=False, cast=bool)
                           else "PAPER"),
    }


# Include all routers
app.include_router(alert_webhook)
app.include_router(signals_router)
app.include_router(trade_router)
app.include_router(webhook_router)
if telegram_router is not None:
    app.include_router(telegram_router)
app.include_router(admin_router)
app.include_router(market_router)
app.include_router(history_router)
if history_cmd_router is not None:
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
app.include_router(backtest_router)
app.include_router(gold_router)
app.include_router(execution_router)
app.include_router(saas_router)
app.include_router(vaultum_router)
app.include_router(pairs_router)
app.include_router(structure_router)
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
