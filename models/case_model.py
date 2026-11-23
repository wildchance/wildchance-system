"""Live sniper setups — every fire is recorded and later resolved (held / broke), so
the case library learns from the system's own recent live action."""

from sqlalchemy import Column, DateTime, Float, Integer, String, Text
from sqlalchemy.sql import func

from database.db import Base


class LiveCase(Base):
    __tablename__ = "live_cases"

    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    symbol = Column(String, index=True)
    side = Column(String)                     # BUY | SELL
    roles = Column(String)                    # e.g. "extreme 1 · TP2|buy/sell limit"
    zone = Column(String)                     # "low-high"
    entry = Column(Float)
    stop = Column(Float)
    tp1 = Column(Float)
    status = Column(String, index=True, default="pending")   # pending | held | broke
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    note = Column(Text)
