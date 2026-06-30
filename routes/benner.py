"""Benner cycle endpoints — macro countdown timer.

  GET /benner            status for the current UTC year
  GET /benner/{year}     status for a specific year
"""

from __future__ import annotations

import datetime as _dt

from fastapi import APIRouter

from benner.engine import benner_status

router = APIRouter(prefix="/benner", tags=["benner"])


@router.get("")
async def benner_now():
    year = _dt.datetime.now(_dt.timezone.utc).year
    return benner_status(year)


@router.get("/{year}")
async def benner_year(year: int):
    return benner_status(year)
