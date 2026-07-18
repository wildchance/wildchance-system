from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from decouple import config

_RAW_URL = config("DATABASE_URL")


def _normalize(url: str):
    """Return (sqlalchemy_url, connect_args) suitable for the asyncpg driver.

    Managed Postgres providers (Neon, Supabase, …) hand out libpq-style URLs
    like ``postgresql://…?sslmode=require&channel_binding=require``. The
    SQLAlchemy *asyncpg* dialect forwards unknown query params straight to
    ``asyncpg.connect()``, which does NOT accept ``sslmode`` — so the raw Neon
    URL fails with ``TypeError: connect() got an unexpected keyword 'sslmode'``.

    Here we:
      • upgrade ``postgresql://`` → ``postgresql+asyncpg://``
      • strip the libpq-only params (``sslmode``/``channel_binding``)
      • re-enable TLS via ``connect_args={"ssl": True}`` when the URL asked for
        SSL or points at a host that requires it (Neon/Supabase).
    Plain local URLs (e.g. the docker-compose Postgres) are unaffected.
    """
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)

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
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )
    return cleaned, connect_args


DATABASE_URL, _CONNECT_ARGS = _normalize(_RAW_URL)

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    connect_args=_CONNECT_ARGS,
    # Serverless Postgres (Neon free) auto-suspends after a few minutes idle and
    # drops open connections. pool_pre_ping checks a connection is alive (and
    # transparently reconnects) before each use, and pool_recycle proactively
    # discards connections older than 5 minutes — together this prevents the
    # "connection was closed / InterfaceError" 500s on the first request after
    # the database has been idle.
    pool_pre_ping=True,
    pool_recycle=300,
)
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
