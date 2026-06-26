"""DXY correlation / divergence + COT-extreme setup engine."""

from .engine import (
    returns,
    pearson,
    dxy_alignment,
    dxy_direction,
    signal_vs_dxy,
    cot_rank,
    cot_extreme,
)

__all__ = [
    "returns",
    "pearson",
    "dxy_alignment",
    "dxy_direction",
    "signal_vs_dxy",
    "cot_rank",
    "cot_extreme",
]
