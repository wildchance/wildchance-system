"""Every signal the system posts to Telegram, kept for the members' web feed."""

from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.sql import func

from database.db import Base


class MemberSignal(Base):
    __tablename__ = "member_signals"

    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    kind = Column(String, index=True)          # z-setup | swing | cbdr | gold | digest | other
    text = Column(Text, nullable=False)
