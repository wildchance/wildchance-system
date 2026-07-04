# MT5 Execution Bridge (FundingPips)

The Render app **decides & sizes** trades; this connector **places** them. MT5's
Python API is Windows-only and needs a running terminal, so the bridge runs on a
small **Windows VPS** with the FundingPips MT5 terminal logged in.

```
Render app  ──enqueue──►  execution_orders (DB)
     ▲                          │
     │ /execution/ack           │ GET /execution/pending?token=…
     │                          ▼
  connector.py  ──order_send──►  MT5 terminal  ──►  FundingPips
   (Windows VPS)
```

## App side (already deployed)
- `POST /gold/intraday?...&execute=true` → sizes the signal, prop-gates it, and
  **enqueues** the order.
- `GET  /execution/pending?token=…` → the bridge pulls pending orders.
- `POST /execution/ack?token=…` → the bridge reports ticket / fill / rejection.
- `GET  /execution/orders` → monitor recent orders.

Set **`EXECUTION_TOKEN`** in the app env (a long random string). Until it's set,
`/execution/pending` and `/ack` return 503 — orders are never exposed by accident.

## VPS side
1. Windows VPS (e.g. any $5–10/mo Windows droplet), install MT5 + log into your
   FundingPips account.
2. `py -m pip install MetaTrader5 requests`
3. Set env vars (see the header of `connector.py`) — **use the same
   `EXECUTION_TOKEN` as the app**, and `GOLD_SYMBOL` to your broker's gold symbol
   (`XAUUSD`, `XAUUSD.r`, `GOLD`…).
4. `py connector.py`

## Go-live checklist
- [ ] Start on a **DEMO / practice** account first.
- [ ] `MAX_VOLUME` env caps lot size as a hard backstop.
- [ ] Confirm `GOLD_SYMBOL` matches the broker (check Market Watch).
- [ ] Watch `/execution/orders` and MT5 fills agree for a few signals.
- [ ] Only then point it at the funded account.

The connector places **market** orders at signal price and **limit** orders at
the Wade OTE entry, always with the SL/TP the app computed. Scale-out to TP2/TP3
(`tp_levels`) can be added once single-TP fills are verified.
