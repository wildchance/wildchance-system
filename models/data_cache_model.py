"""Small JSON key/value store — what the system learns must survive Render restarts
(data-source health scores, auto base rates)."""

from sqlalchemy import Column, DateTime, String, Text
from sqlalchemy.sql import func

from database.db import Base


class DataCache(Base):
    __tablename__ = "data_cache"

    key = Column(String, primary_key=True)
    value = Column(Text, nullable=False)                  # JSON
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
