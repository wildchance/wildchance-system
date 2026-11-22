"""OHLC bars pushed by the MT5 EA (broker feed) — the free, account-accurate source.

The EA POSTs closed bars to /bridge/bars; ohlc_service / cbdr_service read these
FIRST and only fall back to TwelveData when the broker feed is missing or stale.
"""

from sqlalchemy import Column, DateTime, Float, Integer, String, UniqueConstraint

from database.db import Base


class BrokerBar(Base):
    __tablename__ = "broker_bars"
    __table_args__ = (UniqueConstraint("symbol", "timeframe", "ts", name="uq_broker_bar"),)

    id = Column(Integer, primary_key=True)
    symbol = Column(String, nullable=False, index=True)     # canonical: XAUUSD, EURUSD, US500
    timeframe = Column(String, nullable=False, index=True)  # M15 | H1 | H4 | D1 | W1
    ts = Column(DateTime(timezone=True), nullable=False, index=True)   # bar OPEN, UTC
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float)
