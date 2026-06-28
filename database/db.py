# database/db.py  (add/replace the connection logic section)

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from decouple import config
from urllib.parse import urlparse

DATABASE_URL = config("DATABASE_URL")

def get_engine():
    """Create async engine with proper SSL handling for Render, Neon, Supabase, etc."""
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL environment variable is required")

    # Parse URL to detect host
    parts = urlparse(DATABASE_URL)
    host = parts.hostname or ""
    sslmode = parts.query.get("sslmode", "") if parts.query else ""

    # Auto-enable SSL for cloud providers including Render
    enable_ssl = (
        sslmode in ("require", "verify-ca", "verify-full")
        or "neon.tech" in host
        or "supabase" in host
        or "render.com" in host
        or ".onrender.com" in host
    )

    connect_args = {"ssl": True} if enable_ssl else {}

    engine = create_async_engine(
        DATABASE_URL,
        connect_args=connect_args,
        echo=False,
        pool_pre_ping=True,      # Helps with Render's connection resets
    )
    return engine


# Session factory
AsyncSessionLocal = sessionmaker(
    get_engine(), class_=AsyncSession, expire_on_commit=False
)


async def init_db():
    """Create tables if they don't exist."""
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)  # assuming you have Base imported
