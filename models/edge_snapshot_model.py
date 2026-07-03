"""Daily EdgeFinder bias snapshots — one row per pair per day.

Persists the scoreboard each day so the system can show how a pair's bias
TRENDS and flag SHIFTS (a flip in direction = a change in the macro read).
"""

from sqlalchemy import Column, Integer, String, Date, DateTime
from sqlalchemy.sql import func

from database.db import Base


class EdgeSnapshot(Base):
    __tablename__ = "edge_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    snap_date = Column(Date, nullable=False, index=True)
    pair = Column(String, nullable=False, index=True)
    bias = Column(String)
    score = Column(Integer)
    system_verdict = Column(String)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
