"""Versioned schema migrations + drift check (why not Alembic: see the note at the end).

On every start, after create_all (which adds NEW tables), each numbered migration that is not
yet in `schema_migrations` runs once, in order, inside its own transaction, and is recorded with
its name and time. A failing migration is NOT recorded (it retries next start), is counted in
the error tracker, and never blocks startup. All statements are idempotent (IF NOT EXISTS), so
version 1 is safe on databases that already had the old ad-hoc column backfills.

drift() compares every model's columns with the live database and lists what is missing — the
checklist shows it, so a model change without a migration is caught before a page 500s.

To change an existing table: add a new (version, name, [SQL]) entry at the END. Never edit or
reorder an applied version. Note: Alembic would need a sync driver, an autogenerate baseline
against the live Neon schema and a deploy step; this runner keeps the same guarantees (ordered,
recorded, once-only, never blocking) with zero new moving parts.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

MIGRATIONS: List[Tuple[int, str, List[str]]] = [
    (1, "baseline: columns added after first deploys", [
        "ALTER TABLE gold_positions ADD COLUMN IF NOT EXISTS trade_type VARCHAR DEFAULT 'swing'",
        "ALTER TABLE gold_positions ADD COLUMN IF NOT EXISTS deadline TIMESTAMPTZ",
        "ALTER TABLE gold_positions ADD COLUMN IF NOT EXISTS stop_initial DOUBLE PRECISION",
        "ALTER TABLE gold_positions ADD COLUMN IF NOT EXISTS tp4 DOUBLE PRECISION",
        "ALTER TABLE gold_positions ADD COLUMN IF NOT EXISTS limit_price DOUBLE PRECISION",
        "ALTER TABLE gold_positions ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ",
        "ALTER TABLE execution_orders ADD COLUMN IF NOT EXISTS account VARCHAR",
        "ALTER TABLE saas_users ADD COLUMN IF NOT EXISTS password_hash VARCHAR",
        "ALTER TABLE live_cases ADD COLUMN IF NOT EXISTS council TEXT",
        "ALTER TABLE execution_orders ADD COLUMN IF NOT EXISTS group_id VARCHAR",
        "ALTER TABLE execution_orders ADD COLUMN IF NOT EXISTS scale_role VARCHAR",
        "ALTER TABLE execution_orders ADD COLUMN IF NOT EXISTS be_price DOUBLE PRECISION",
        "ALTER TABLE execution_orders ADD COLUMN IF NOT EXISTS be_after VARCHAR",
        "ALTER TABLE execution_orders ADD COLUMN IF NOT EXISTS be_done INTEGER DEFAULT 0",
        "ALTER TABLE saas_users ADD COLUMN IF NOT EXISTS paid_until TIMESTAMPTZ",
        "ALTER TABLE saas_users ADD COLUMN IF NOT EXISTS provider VARCHAR",
        "ALTER TABLE saas_users ADD COLUMN IF NOT EXISTS provider_ref VARCHAR",
    ]),
    (2, "indexes for the research store and journal", [
        "CREATE INDEX IF NOT EXISTS ix_signal_snapshots_created ON signal_snapshots (created_at)",
        "CREATE INDEX IF NOT EXISTS ix_closed_trades_close_time ON closed_trades (close_time)",
    ]),
]

_LEDGER = """CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY, name VARCHAR NOT NULL, applied_at TIMESTAMPTZ DEFAULT now())"""


async def apply(engine) -> Dict:
    """Run pending migrations (Postgres). SQLite test DBs are fresh from create_all → skipped."""
    from sqlalchemy import text
    out = {"applied": [], "failed": [], "skipped": False}
    async with engine.begin() as conn:
        if conn.dialect.name != "postgresql":
            out["skipped"] = True
            return out
        await conn.execute(text(_LEDGER))
        done = {r[0] for r in (await conn.execute(text("SELECT version FROM schema_migrations"))).all()}
    for ver, name, stmts in MIGRATIONS:
        if ver in done:
            continue
        try:
            async with engine.begin() as conn:
                for s in stmts:
                    await conn.execute(text(s))
                await conn.execute(text("INSERT INTO schema_migrations (version, name) VALUES (:v, :n)"),
                                   {"v": ver, "n": name})
            out["applied"].append(ver)
        except Exception as e:
            out["failed"].append({"version": ver, "error": f"{type(e).__name__}: {str(e)[:160]}"})
            try:
                from services import errors
                errors.record("migration_fail", f"v{ver} {name}", str(e)[:200])
            except Exception:
                pass
            break                                      # later versions may depend on this one
    return out


async def status(engine) -> Dict:
    from sqlalchemy import text
    try:
        async with engine.connect() as conn:
            if conn.dialect.name != "postgresql":
                return {"dialect": conn.dialect.name, "note": "migrations run on Postgres only"}
            rows = (await conn.execute(text("SELECT version, name, applied_at FROM schema_migrations "
                                            "ORDER BY version"))).all()
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)[:160]}"}
    applied = {r[0] for r in rows}
    return {"applied": [{"version": r[0], "name": r[1], "at": r[2].isoformat() if r[2] else None} for r in rows],
            "pending": [v for v, _, _ in MIGRATIONS if v not in applied], "latest": MIGRATIONS[-1][0]}


async def drift(engine) -> Dict:
    """Model columns missing from the live database (would 500 on read)."""
    from sqlalchemy import inspect
    from database.db import Base
    try:
        async with engine.connect() as conn:
            def _cols(sync_conn):
                insp = inspect(sync_conn)
                have = set(insp.get_table_names())
                missing = {}
                for t in Base.metadata.sorted_tables:
                    if t.name not in have:
                        missing[t.name] = ["(table missing)"]
                        continue
                    cols = {c["name"] for c in insp.get_columns(t.name)}
                    gone = [c.name for c in t.columns if c.name not in cols]
                    if gone:
                        missing[t.name] = gone
                return missing
            missing = await conn.run_sync(_cols)
    except Exception as e:
        return {"ok": None, "error": f"{type(e).__name__}: {str(e)[:160]}"}
    return {"ok": not missing, "missing": missing}
