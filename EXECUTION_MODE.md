# Execution Mode — MT5 EA foundation (future timeline)

Status: **design only, not wired.** This documents how the reviewed MT5 EAs map
onto the wildchance execution layer once we flip from paper (`stratops_paper`) to
live order routing. Nothing here runs yet — it is the blueprint for the connector.

## Verdict on the reviewed EAs (2026-07-18 upload)

| EA | Engine | Risk model | Take / reject |
|----|--------|-----------|---------------|
| GoldReaper v3 / v3.1 | 9/21/50 EMA cross + continuation | `Buy(...,SL=0,TP=0)`, ≤16 open, float-lock $25 / daily $50 | **Reject** the risk model (basket, no per-trade stop). Study the session/body filters only. |
| TrendRider v5 | SuperTrend + 3-candle body confirm | `PositionOpen(...,SL=0,TP=0)`, float-lock $250 / daily $500 | **Reject** basket risk. Borrow the session + ATR + spread guards. |
| **Fusion Flow v1** | Breakout straddle, `BuyStop`/`SellStop` | **real `orderSL`** on pending stops + trailing | **Adopt as the connector foundation** — only one with defined per-trade risk. |

The three EMA EAs are martingale baskets with no hard stop: one sustained trend
against the stack blows the account. They are un-survivable under our reflection
scorecard and must never drive our risk. Fusion Flow is the closest to us because
it places **pending stop orders with a real SL and a trailing stop** — the same
shape as our pre-London CBDR limit plan.

## How Fusion Flow maps onto wildchance (when execution mode opens)

Our system already emits limit/stop specs of the shape
`{side, entry, stop, targets:[...], trade_type, reason}` from:

- `cbdr.engine.prelondon_limits` (buy −1SD / sell +1SD/+3SD)
- `gold.zones.sniper_stack` (2–3 layered limits, one shared stop) ← **new**
- `candlerange` CRT 1-5-9 and `gold.trade_types.seek_destroy_plan`

Fusion Flow's `BuyStop/SellStop + orderSL + trailing` loop is exactly the MT5-side
executor for those specs. The redesign to fit us:

1. **Replace its signal engine** (Delta/MaxDistance breakout) with *our* specs —
   the EA becomes a dumb order-router; wildchance decides entry/stop/targets.
2. **Keep its pending-order + SL + trailing plumbing** (`CTrade`, `BuyStop`,
   `SellStop`, `PositionModify` trailing) — port it into the connector.
3. **Enforce our exposure cap** (`gold.exposure`, `DEFAULT_RISK_CAP_USD`) and the
   account-tier lot sizing (`gold.risk_engine.lot_ladder`,
   `gold.flip_ladders`) BEFORE any order is sent — the layering stack is sized to
   the account so aggregate risk stays inside parameters.
4. **BE + partials** driven by `gold.position.evaluate` (our TP1→BE→TP2/TP3/time-
   stop state machine), not the EA's float-lock.
5. **News guard** — block sends during NFP/CPI/FOMC via `/gold/news`.

## Flip system (same trades, tiered accounts)

The account-tier growth ladders (`gold.flip_ladders`) define the sizing cadence the
executor scales to:

- **Cent flipper** (deposit < 700): 500-pip lot-doubling runs → ~81× in ~10 runs.
- **Middle** (700–5,000; 5,000 = drawdown benchmark): 12 runs, cycles of
  [500, 500, 1500] pips.
- **Flipper** (≥ 5,000): 1,500-pip runs only.

Same across cent / USD / KES / KWD denominations. The executor reads the tier for
the connected account, sizes the sniper stack within that tier's risk parameters,
and runs the identical zone-to-zone campaign.

## Not done / next

- Build the MT5 connector service (VPS bridge) — the actual socket/file/REST link.
- Port Fusion Flow's pending-order + trailing loop into it as the send/manage layer.
- Wire `sniper_stack` orders → connector enqueue behind the exposure cap.
- Flip `stratops_paper` → live once the paper scorecard verdict is GREEN.
