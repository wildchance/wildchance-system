"""Execution order queue — signals the MT5 bridge pulls and reports back on.

The Render app never touches MT5 (Linux, no terminal). It ENQUEUES computed
orders here; a MetaTrader5 connector on a Windows VPS polls /execution/pending,
places them, and reports fills to /execution/ack.
"""

from sqlalchemy import Column, Integer, String, Float, DateTime
from sqlalchemy.sql import func

from database.db import Base


class ExecutionOrder(Base):
    __tablename__ = "execution_orders"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    symbol = Column(String, nullable=False)          # XAUUSD
    side = Column(String, nullable=False)            # buy | sell
    order_type = Column(String, nullable=False)      # market | limit
    volume = Column(Float, nullable=False)           # lots
    price = Column(Float)                             # limit/OTE price (None for market)
    sl = Column(Float)
    tp = Column(Float)
    magic = Column(Integer)
    comment = Column(String)
    source = Column(String)                          # gold_intraday | gold_scan | manual
    account = Column(String, index=True)             # acc1..acc5 (fleet fan-out); None = single
    status = Column(String, default="pending", index=True)  # pending|sent|filled|rejected|cancelled
    ticket = Column(Integer)                          # MT5 ticket, filled by the bridge
    fill_price = Column(Float)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
