"""Persist small JSON blobs in Neon (data_cache). Never raises — the app keeps
working in memory if the DB is unavailable."""

from __future__ import annotations

import datetime as dt
import json
from typing import Any, Optional, Tuple


async def get(key: str) -> Tuple[Optional[Any], Optional[dt.datetime]]:
    try:
        from database.db import AsyncSessionLocal
        from models.data_cache_model import DataCache
        async with AsyncSessionLocal() as db:
            row = await db.get(DataCache, key)
            if row:
                return json.loads(row.value), row.updated_at
    except Exception:
        pass
    return None, None


async def put(key: str, value: Any) -> bool:
    try:
        from database.db import AsyncSessionLocal
        from models.data_cache_model import DataCache
        async with AsyncSessionLocal() as db:
            row = await db.get(DataCache, key)
            text = json.dumps(value, default=str)
            if row:
                row.value = text
                row.updated_at = dt.datetime.now(dt.timezone.utc)
            else:
                db.add(DataCache(key=key, value=text))
            await db.commit()
        return True
    except Exception:
        return False
