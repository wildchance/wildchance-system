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
the Wade OTE entry, always with the SL/TP the app computed.

## Scale-out across the trend-TP ladder

When a signal carries a trend-extension ladder, the app **splits the sized
position into partial legs** (`build_orders`) — each leg is a fraction of the
volume with its **own trend-TP** and the **same structure stop**. So one signal
enqueues several `execution_orders` rows, and the bridge (which already places
each pending order independently) opens them as **separate MT5 positions** that
ladder out: bank the most at the nearest target, run a tail to the furthest
extension. Example — a 0.20-lot gold long becomes:

```
leg 1/4   0.07 lot   TP 4214.2   ┐
leg 2/4   0.06 lot   TP 4272.6   │  all share SL 4090
leg 3/4   0.04 lot   TP 4367.1   │  (structure invalidation)
leg 4/4   0.03 lot   TP 4520.1   ┘
```

The `comment` on each MT5 position reads `<profile> i/n` so you can see the legs
in the terminal. No connector change is needed — it already loops the queue.

Tuning (app env):
- `EXECUTION_SCALE_OUT` — `true` (default) / `false` to send one full-size order.
- `EXECUTION_SCALE_WEIGHTS` — leg weights, default `0.4,0.3,0.2,0.1` (front-loaded).
- `EXECUTION_MIN_LOT` / `EXECUTION_LOT_STEP` — broker lot floor/step (default 0.01).

If the sized lot is too small to split (e.g. 0.01 over 4 rungs), it degrades
gracefully to as many legs as it can afford — nearest TPs first — or a single
order. `MAX_VOLUME` on the VPS still hard-caps **each leg**.
