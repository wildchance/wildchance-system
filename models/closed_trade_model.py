"""Every closed broker trade (any source — EA, signals, manual), pushed by the EA.
The trade journal's source of truth: wins, losses and breakevens."""

from sqlalchemy import Column, DateTime, Float, Integer, String
from sqlalchemy.sql import func

from database.db import Base


class ClosedTrade(Base):
    __tablename__ = "closed_trades"

    ticket = Column(String, primary_key=True)          # MT5 deal ticket (unique, never re-logged)
    logged_at = Column(DateTime(timezone=True), server_default=func.now())
    symbol = Column(String, index=True)                # canonical (USDCHFb.Z → USDCHF)
    broker_symbol = Column(String)
    side = Column(String)                              # BUY / SELL (the position)
    volume = Column(Float)
    open_price = Column(Float)
    close_price = Column(Float)
    open_time = Column(DateTime(timezone=True))
    close_time = Column(DateTime(timezone=True), index=True)
    net = Column(Float)                                # profit + commission + swap
    pips = Column(Float)
    result = Column(String, index=True)                # win / loss / breakeven
    magic = Column(Integer)                            # 0 = manual
    comment = Column(String)
