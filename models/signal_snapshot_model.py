"""Point-in-time record of every signal: what the system KNEW when it sent the card.
The research layer (backtests, weighted council) learns from these rows."""

from sqlalchemy import Column, DateTime, Float, String, Text
from sqlalchemy.sql import func

from database.db import Base


class SignalSnapshot(Base):
    __tablename__ = "signal_snapshots"

    id = Column(String, primary_key=True)                # = signal_tracker id
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    symbol = Column(String, index=True)
    side = Column(String)
    source = Column(String, index=True)                  # engine
    context = Column(Text)                               # JSON: price, levels, votes, strength, COT, options, regime…
    result = Column(String, index=True, default="open")  # open / win / loss / breakeven
    r_multiple = Column(Float)
    closed_at = Column(DateTime(timezone=True))
