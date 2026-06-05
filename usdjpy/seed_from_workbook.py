"""Seed the forward test from the existing workbook closes.

The USDJPY_FORWARD_TEST.xlsx DAILY LOG already has real closes from 2026-04-30
onward. Run this once so the live system starts with the same 20-day warm-up
the workbook had, instead of waiting 20 fresh trading days.

Usage:
    python -m usdjpy.seed_from_workbook          # uses built-in closes below
"""

import asyncio
from datetime import date

# (date, close) — DAILY LOG rows 5..29 from USDJPY_FORWARD_TEST.xlsx.
WORKBOOK_CLOSES = [
    ("2026-04-30", 157.088), ("2026-05-03", 157.248), ("2026-05-04", 157.896),
    ("2026-05-05", 156.370), ("2026-05-06", 156.938), ("2026-05-07", 156.686),
    ("2026-05-10", 157.226), ("2026-05-11", 157.615), ("2026-05-12", 157.894),
    ("2026-05-13", 158.384), ("2026-05-14", 158.776), ("2026-05-17", 158.858),
    ("2026-05-18", 159.080), ("2026-05-19", 158.934), ("2026-05-20", 158.990),
    ("2026-05-21", 159.206), ("2026-05-24", 158.929), ("2026-05-25", 159.314),
    ("2026-05-26", 159.518), ("2026-05-27", 159.246), ("2026-05-28", 159.271),
    ("2026-05-31", 159.672), ("2026-06-01", 159.938), ("2026-06-02", 160.092),
    ("2026-06-03", 159.971),
]


async def main():
    from database.db import AsyncSessionLocal, init_db
    from services import usdjpy_service as svc

    await init_db()
    async with AsyncSessionLocal() as db:
        for d, close in WORKBOOK_CLOSES:
            await svc.ingest_close(db, date.fromisoformat(d), close, source="workbook")
        sb = await svc.get_scoreboard(db)
        latest = await svc.latest_signal(db)
    print("Seeded", len(WORKBOOK_CLOSES), "closes.")
    print("Latest:", latest)
    print("Scoreboard:", sb)


if __name__ == "__main__":
    asyncio.run(main())
