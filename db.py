from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from decouple import config

DATABASE_URL = config("DATABASE_URL")
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

engine = create_async_engine(DATABASE_URL, echo=True)
AsyncSessionLocal = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session

async def init_db():
    # Import models so their tables register on the shared Base metadata.
    import models.trade_model
    import models.signal_model
    import models.usdjpy_model
    async with engine.begin() as conn:
        # create_all only — never drop. The USD/JPY forward test accumulates
        # months of daily closes and trades; dropping tables on every startup
        # would silently wipe the experiment. New tables are added safely;
        # existing data is preserved across restarts/redeploys.
        await conn.run_sync(Base.metadata.create_all)

