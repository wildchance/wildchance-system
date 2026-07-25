"""SaaS user — API-key auth + subscription tier (the multi-tenant record).

Minimal on purpose: email, a random API key (the auth credential), the tier, and the
Stripe customer id for billing. Per-user data isolation keys off user_id elsewhere.
"""

from sqlalchemy import Column, Integer, String, DateTime, Boolean
from sqlalchemy.sql import func

from database.db import Base


class User(Base):
    __tablename__ = "saas_users"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    email = Column(String, unique=True, index=True, nullable=False)
    api_key = Column(String, unique=True, index=True, nullable=False)
    tier = Column(String, default="scout", index=True)          # scout..institutional
    stripe_customer_id = Column(String)
    active = Column(Boolean, default=True)
    calls_this_period = Column(Integer, default=0)              # simple usage meter
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
