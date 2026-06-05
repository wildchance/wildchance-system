"""Persistence for the USD/JPY forward test — the DB version of the workbook.

UsdJpyClose  == one row of the DAILY LOG (the yellow CLOSE cell + computed cols)
UsdJpyTrade  == a fired BUY/SELL signal, its stop, and (later) its day+3 exit
"""

from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, Date, UniqueConstraint
from sqlalchemy.sql import func
from database.db import Base


class UsdJpyClose(Base):
    __tablename__ = "usdjpy_closes"
    __table_args__ = (UniqueConstraint("trade_date", name="uq_usdjpy_close_date"),)

    id = Column(Integer, primary_key=True, index=True)
    trade_date = Column(Date, nullable=False, index=True)
    close = Column(Float, nullable=False)
    ma20 = Column(Float, nullable=True)
    sd20 = Column(Float, nullable=True)
    z = Column(Float, nullable=True)
    signal = Column(String, nullable=False, default="NO TRADE")
    source = Column(String, nullable=True)  # "auto:twelvedata", "manual", ...
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class UsdJpyTrade(Base):
    __tablename__ = "usdjpy_trades"

    id = Column(Integer, primary_key=True, index=True)
    entry_date = Column(Date, nullable=False, index=True)
    action = Column(String, nullable=False)        # BUY | SELL
    entry = Column(Float, nullable=False)
    stop = Column(Float, nullable=False)
    sd20 = Column(Float, nullable=False)
    stop_pips = Column(Float, nullable=False)

    # Filled in 3 trading days later.
    exit_date = Column(Date, nullable=True)
    exit_price = Column(Float, nullable=True)
    result_r = Column(Float, nullable=True)
    stop_breached = Column(Boolean, nullable=True)  # informational only

    status = Column(String, nullable=False, default="OPEN")  # OPEN | CLOSED
    created_at = Column(DateTime(timezone=True), server_default=func.now())
