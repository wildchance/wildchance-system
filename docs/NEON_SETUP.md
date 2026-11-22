# Neon Postgres setup (free, doesn't expire)

Render's free Postgres is deleted 30 days after creation (plus a 14-day grace period).
Neon's free tier doesn't expire, and the app already handles Neon's connection string
(`database/db.py` strips `sslmode` / `channel_binding` and turns TLS on for asyncpg).
Tables are created automatically on boot, so there are no migrations to run.

## 1. Create the database (about 5 minutes)

1. Go to **neon.tech** and sign up (GitHub or Google login works).
2. **Create project**:
   * Name: `wildchance`
   * Postgres version: the default
   * **Region: pick the one closest to your app host.** Render Oregon → AWS US West
     (Oregon); Render Frankfurt → AWS Europe (Frankfurt). A matching region keeps
     queries fast.
3. The dashboard opens on **Connection details**. Choose:
   * Branch `main`, database `neondb`, role `neondb_owner`
   * **Pooled connection: ON** (the host contains `-pooler`). Free Neon suspends when
     idle, and the pooler handles cold reconnects. The app's `pool_pre_ping=True`
     revalidates connections too.
4. Copy the connection string. It looks like:

   ```
   postgresql://neondb_owner:XXXX@ep-cool-name-123456-pooler.us-west-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require
   ```

   Paste it exactly as shown. Don't add `+asyncpg` and don't remove `?sslmode=…`;
   the app converts it.

## 2. Point the app at it

**Render:** Dashboard → your web service → **Environment** → edit `DATABASE_URL` →
paste the Neon string → **Save, rebuild and deploy**.
(**Railway:** Service → Variables → `DATABASE_URL`.)

While you're there, set these too (see `.env.example`):

| Variable | Value |
|---|---|
| `BRIDGE_TOKEN` | a long random string. The EA's `InpBridgeToken` must match it |
| `MACRO_BIAS` | e.g. `{"BTCUSD":"SELL_ONLY"}` |
| `LUNAR_GATE_ENABLED` | `true` (the default) or `false` per your backtest |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | unchanged |

## 3. Verify

```bash
curl -s https://<your-app>/health
```

`db_reachable` should be `true`. The first request after Neon has been idle can take
1–3 s while the compute wakes up; that's normal.

In the Neon console → **Tables** you should see `gold_positions`, `execution_orders`,
`broker_bars` and the rest, created on the first boot.

## 4. Keep it free

* The free tier has a small storage cap. `broker_bars` is the table that grows: about
  5 timeframes × 300 bars × N symbols is small, but if it ever bloats you can prune it
  in the Neon SQL editor:

  ```sql
  DELETE FROM broker_bars WHERE timeframe IN ('M15','H1') AND ts < now() - interval '60 days';
  ```
* Neon keeps a short point-in-time history on free, which Render free never had. For
  a real backup, run `pg_dump "$DATABASE_URL" > backup.sql` from any machine.

## 5. Moving old data from Render (only if the Render DB still exists)

```bash
pg_dump --no-owner --no-acl "<RENDER_EXTERNAL_DATABASE_URL>" > render.sql
psql "<NEON_DIRECT_URL_WITHOUT_-pooler>" < render.sql
```

Use Neon's **direct** (non-pooled) string for the import. If Render already deleted
the DB there's nothing to move: the app recreates empty tables and the EA refills
`broker_bars` within minutes.
