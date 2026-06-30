"""Trade-setup zone builder (Entry/SL/TP1-3 from CBDR SD extremes + CRT trigger)."""

from .engine import (
    build_setup,
    crt_to_setup,
    CONT_TP_SDS,
    REV_TP_SDS,
    SL_BUFFER_SD,
)

__all__ = ["build_setup", "crt_to_setup", "CONT_TP_SDS", "REV_TP_SDS", "SL_BUFFER_SD"]
