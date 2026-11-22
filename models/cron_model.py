"""One row per executed schedule slot — the unique key makes overlapping ticks
(cron-job.org + GitHub backup) and restarts safe: a slot runs at most once."""

from sqlalchemy import Column, DateTime, String
from sqlalchemy.sql import func

from database.db import Base


class CronRun(Base):
    __tablename__ = "cron_runs"

    slot = Column(String, primary_key=True)          # "<job>@2026-11-23T08:03Z"
    ran_at = Column(DateTime(timezone=True), server_default=func.now())
