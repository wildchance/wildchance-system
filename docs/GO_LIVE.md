# VAULTUM — Go Live (MT5 VPS bridge)

Flipping the system from **paper** to **live** is two env flags on Render + running the
bridge on a Windows VPS. No code changes — the execution path is already wired
(`services/trade_executor.maybe_enqueue` → `execution` order queue → `mt5_bridge/connector.py`).
It is **safe to enable now**: orders are built from the *auto-detected* Optimus levels
(hourly `POST /gold/levels/refresh`), reject-gated, and passed through the VaR gate before
anything is queued.

## 1. Render env (the app)

| Var | Value | Effect |
|---|---|---|
| `EXECUTION_ENABLED` | `true` | `maybe_enqueue` actually queues orders (default `false` = paper) |
| `FLEET_ENABLED` | `true` | fan the order across the fleet accounts (`false` = single account) |
| `EXECUTION_TOKEN` | *a long random secret* | shared secret the bridge uses to pull/ack orders |
| `PORTFOLIO_VAR_GATE_ENABLED` | `true` | keep the VaR gate on (blocks orders over the risk limit) |
| `PORTFOLIO_EQUITY_USD` | e.g. `111450` | account equity the VaR limit is a % of |
| `PORTFOLIO_VAR_LIMIT_PCT` | e.g. `2.0` | max portfolio VaR % before the gate blocks |

Leave `EXECUTION_ENABLED` **unset/false** until the bridge is confirmed connected below —
that way the queue can be inspected (`GET /execution/status`) with nothing actually firing.

## 2. Windows VPS (the bridge)

The bridge is a thin poller — it needs MetaTrader5 running and logged into the broker
account, Python, and two packages.

```bat
pip install MetaTrader5 requests
set APP_BASE_URL=https://<your-render-app>.onrender.com
set EXECUTION_TOKEN=<the same secret as Render>
set POLL_SECONDS=5
python mt5_bridge\connector.py
```

It loops: `GET /execution/pending?token=…` → `place()` on MT5 → `POST /execution/ack` with
the ticket / fill / rejection. `BUY_LIMIT` / `SELL_LIMIT` pending orders and market orders
are both handled (`_order_type`).

## 3. Preflight (before flipping the switch)

```
GET /vaultum/readiness      # execution mode, fleet config, VaR gate, feed status in one call
GET /execution/status       # switch on? token set? how many orders queued for the bridge
GET /vaultum/policy-rates?live=true   # confirm the BIS/divergence feed resolves
GET /gold/backtest/sells/optimize     # the 250/500 partial optimizer read on current history
```

Green means: `readiness` shows `execution_enabled` + a token set, the bridge shows up in
`execution/status`, and `/vaultum/feeds` shows the free feeds live.

## 4. Flip live

1. Confirm the bridge console prints `Bridge live — polling …`.
2. Set `EXECUTION_ENABLED=true` (and `FLEET_ENABLED=true` if fanning) on Render → redeploy.
3. When the alerter arms a setup (`POST /gold/alerter/scan?notify=true`, hourly cron), the
   order is built, VaR-gated, queued, pulled by the bridge, and placed on MT5.
4. Watch `GET /execution/orders` and run `GET /execution/reconcile?token=…` on a schedule to
   catch orphan fills / stuck orders.

## 5. Kill switch

Set `EXECUTION_ENABLED=false` and redeploy — the queue stops accepting new orders instantly.
Stop the VPS `connector.py` process to halt placement. Open positions are unaffected (manage
them in MT5 / via the position endpoints).
