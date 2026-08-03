import logging
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from decouple import config

_log = logging.getLogger("uvicorn.error")
# Local SQLite fallback so the API ALWAYS boots — even with a missing or malformed
# DATABASE_URL. The stateless brain (signals, cards, volume-profile, VAULTUM, backtests)
# works immediately; set a real Postgres URL to persist tracking/execution.
_SQLITE_FALLBACK = "sqlite+aiosqlite:///./wildchance.db"


def _clean(raw) -> str:
    """Strip the common paste mistakes that make a good URL unparseable (and crash boot):
    surrounding quotes, whitespace/newlines, a leading ``psql `` wrapper from the Neon /
    Render copy snippet, and an accidental ``DATABASE_URL=`` echo."""
    if not raw:
        return ""
    s = str(raw).strip()
    if s.lower().startswith("psql "):
        s = s[5:].strip()
    if s.lower().startswith("database_url="):
        s = s.split("=", 1)[1].strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        s = s[1:-1].strip()
    return s


def _normalize(url):
    """Return (sqlalchemy_url, connect_args). Recovers a slightly-mangled Postgres URL and
    routes it to the asyncpg driver; falls back to local SQLite when the value is empty or
    unparseable, so a bad env var can never take the whole API down at import time.

    Managed Postgres (Neon/Supabase) hands out libpq URLs like
    ``postgresql://…?sslmode=require&channel_binding=require`` — the asyncpg dialect
    forwards those unknown params to ``asyncpg.connect()`` which rejects ``sslmode``. We
    upgrade the scheme, strip the libpq-only params, and re-enable TLS via connect_args."""
    url = _clean(url)
    if not url or "://" not in url:
        return _SQLITE_FALLBACK, {}
    if url.startswith("postgres://"):                       # Heroku-style alias
        url = url.replace("postgres://", "postgresql://", 1)
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    try:
        parts = urlsplit(url)
        query = dict(parse_qsl(parts.query))
        sslmode = query.pop("sslmode", None)
        query.pop("channel_binding", None)
        host = parts.hostname or ""
        enable_ssl = (
            sslmode in ("require", "verify-ca", "verify-full")
            or "neon.tech" in host
            or "supabase" in host
        )
        connect_args = {"ssl": True} if enable_ssl else {}
        cleaned = urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
        return cleaned, connect_args
    except Exception:
        _log.error("DATABASE_URL %r could not be parsed — falling back to SQLite", url)
        return _SQLITE_FALLBACK, {}


def _make_engine(url: str, connect_args: dict):
    if url.startswith("sqlite"):
        return create_async_engine(url, echo=False)
    # Serverless Postgres (Neon free) auto-suspends and drops connections; pool_pre_ping
    # revalidates (and reconnects) before each use and pool_recycle discards stale ones.
    return create_async_engine(url, echo=False, connect_args=connect_args,
                               pool_pre_ping=True, pool_recycle=300)


_RAW_URL = config("DATABASE_URL", default="")
DATABASE_URL, _CONNECT_ARGS = _normalize(_RAW_URL)

try:
    engine = _make_engine(DATABASE_URL, _CONNECT_ARGS)
except Exception as e:                                   # never crash boot on the engine
    _log.error("DB engine for %r failed (%s) — falling back to local SQLite", DATABASE_URL, e)
    try:
        DATABASE_URL, _CONNECT_ARGS = _SQLITE_FALLBACK, {}
        engine = _make_engine(DATABASE_URL, {})
    except Exception:                                   # even the sqlite driver is missing
        DATABASE_URL = "postgresql+asyncpg://localhost/wildchance"   # constructible dummy
        engine = create_async_engine(DATABASE_URL, echo=False)

AsyncSessionLocal = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


# Columns added to gold_positions AFTER the first deploy. create_all never
# ALTERs an existing table, so a table created by an earlier deploy is missing
# these — and any SELECT that reads them 500s (e.g. /gold/scorecard reading
# trade_type). Each ALTER is idempotent (ADD COLUMN IF NOT EXISTS), so it is safe
# to run on every cold start and is a no-op once the column exists.
_GOLD_POSITION_MIGRATIONS = [
    "ALTER TABLE gold_positions ADD COLUMN IF NOT EXISTS trade_type VARCHAR DEFAULT 'swing'",
    "ALTER TABLE gold_positions ADD COLUMN IF NOT EXISTS deadline TIMESTAMPTZ",
    "ALTER TABLE gold_positions ADD COLUMN IF NOT EXISTS stop_initial DOUBLE PRECISION",
    "ALTER TABLE gold_positions ADD COLUMN IF NOT EXISTS tp4 DOUBLE PRECISION",
    "ALTER TABLE gold_positions ADD COLUMN IF NOT EXISTS limit_price DOUBLE PRECISION",
    "ALTER TABLE gold_positions ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ",
    # Per-account copy-trade fan-out: each execution order carries which fleet
    # account (acc1..acc5) it belongs to, so each VPS connector pulls only its own.
    "ALTER TABLE execution_orders ADD COLUMN IF NOT EXISTS account VARCHAR",
    # Partial scale-out + runner break-even metadata (the 250/500 exit plan).
    "ALTER TABLE execution_orders ADD COLUMN IF NOT EXISTS group_id VARCHAR",
    "ALTER TABLE execution_orders ADD COLUMN IF NOT EXISTS scale_role VARCHAR",
    "ALTER TABLE execution_orders ADD COLUMN IF NOT EXISTS be_price DOUBLE PRECISION",
    "ALTER TABLE execution_orders ADD COLUMN IF NOT EXISTS be_after VARCHAR",
    "ALTER TABLE execution_orders ADD COLUMN IF NOT EXISTS be_done INTEGER DEFAULT 0",
]


async def init_db():
    # Import models so their tables register on the shared Base metadata.
    import models.trade_model
    import models.signal_model
    import models.usdjpy_model
    import models.wildchance_model
    import models.edge_snapshot_model
    import models.execution_model
    import models.gold_position_model
    import models.user_model                 # SaaS users (auth + tier)
    async with engine.begin() as conn:
        # create_all only — never drop. The forward test accumulates months of
        # data; dropping on startup would silently wipe it. create_all is
        # idempotent, so it is safe to run on every (cold) start.
        await conn.run_sync(Base.metadata.create_all)
        # Bring an already-created gold_positions table up to the current schema.
        # Postgres only (ADD COLUMN IF NOT EXISTS); SQLite test DBs are always
        # created fresh by create_all above, so they already have every column.
        if conn.dialect.name == "postgresql":
            from sqlalchemy import text
            for stmt in _GOLD_POSITION_MIGRATIONS:
                try:
                    await conn.execute(text(stmt))
                except Exception:
                    # A failed backfill must never block startup — the defensive
                    # read path (services.scorecard_service.gold_report) still
                    # degrades gracefully if a column is genuinely absent.
                    pass
