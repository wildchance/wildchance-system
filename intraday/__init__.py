"""Intraday mean-reversion breach detection (1h-bar z-score, fresh-breach debounce)."""

from .engine import (
    Breach,
    scan_closes,
    z_at,
    DEFAULT_WINDOW,
    DEFAULT_Z,
)

__all__ = ["Breach", "scan_closes", "z_at", "DEFAULT_WINDOW", "DEFAULT_Z"]
