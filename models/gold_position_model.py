"""Tracked gold swing positions — the feedback the system was missing.

Each fired XAU/USD signal is persisted here as an OPEN row, monitored to a
target / stop / weekly time-stop, then CLOSED with a realized R. Closed rows feed
the gold scorecard (services.scorecard_service) so the reflection loop can finally
see how gold is actually performing.
"""

from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime
from sqlalchemy.sql import func

from database.db import Base


class GoldPosition(Base):
    __tablename__ = "gold_positions"

    id = Column(Integer, primary_key=True, index=True)
    opened_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    symbol = Column(String, nullable=False, default="XAU/USD")
    side = Column(String, nullable=False)            # long | short
    trade_type = Column(String, default="swing")     # swing | intraday | intrasession
    deadline = Column(DateTime(timezone=True))       # tier time-stop horizon
    entry = Column(Float, nullable=False)
    stop = Column(Float, nullable=False)             # current stop (trails to BE)
    stop_initial = Column(Float, nullable=False)     # for R math after BE trail
    tp1 = Column(Float)
    tp2 = Column(Float)
    tp3 = Column(Float)
    tp4 = Column(Float)                              # swing tier runs 4 targets (5-8R)
    lot = Column(Float)
    risk_usd = Column(Float)
    be_active = Column(Boolean, default=False)       # stop trailed to break-even?
    tp_hit = Column(Integer, default=0)              # highest TP reached
    profile = Column(String)                         # weekly profile name
    source = Column(String)                          # gold_scan | gold_intraday | manual
    justification = Column(String)
    status = Column(String, default="OPEN", index=True)   # OPEN | CLOSED
    exit_price = Column(Float)
    exit_reason = Column(String)                     # TP1..TP3 | SL | BE | TIME
    result_r = Column(Float)                         # realized R (CLOSED only)
    action = Column(String)                          # BUY | SELL (scorecard by_action)
    closed_at = Column(DateTime(timezone=True))
