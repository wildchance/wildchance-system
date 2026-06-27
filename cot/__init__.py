"""COT (Commitments of Traders) helpers — release-cadence freshness / discount."""

from .freshness import (
    freshness,
    cot_as_of,
    most_recent_tuesday,
    FRESH_DAYS,
    STALE_DAYS,
    DISCOUNT_FLOOR,
)

__all__ = [
    "freshness",
    "cot_as_of",
    "most_recent_tuesday",
    "FRESH_DAYS",
    "STALE_DAYS",
    "DISCOUNT_FLOOR",
]
