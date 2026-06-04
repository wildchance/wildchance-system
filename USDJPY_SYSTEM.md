# USD/JPY Mean-Reversion — Real-Time Forward-Test System

This is the code version of **`USDJPY_FORWARD_TEST.xlsx`** + the **TENDAJI risk
cheat sheet**. It does exactly what the tracker workbook did — spot when the
z-score hits ±2 and tell you the BUY/SELL, the stop, and the size — except it
can fetch the daily close for you instead of you pasting it in.

> The strategy rules are **FROZEN** (locked 2026-06-04). Changing the
> thresholds, hold period, or instrument mid-test invalidates the forward test.
> The code mirrors the workbook to the decimal; see `usdjpy/engine.py`.

---

## The strategy in one paragraph

USD/JPY, daily closes only. Track the 20-day moving average (MA20) and 20-day
sample standard deviation (SD20). `z = (close − MA20) / SD20`. When
`z ≤ −2` → **BUY** (price stretched far below average, fade up). When
`z ≥ +2` → **SELL** (stretched far above, fade down). Otherwise **wait**.
Entry = the triggering close. Stop = `0.5 × SD20` beyond entry. Exit = the
close **3 trading days** after entry (time-based, no discretionary exits).
Risk per trade comes from the TENDAJI cheat sheet (fixed small lot per account
size). Demo only. ~2 signals/month expected.

**Verdict (decided in advance):** after ≥20 trades, profit factor > 1.2 and
positive expectancy = PASS; profit factor < 1.0 = FAIL; anything else =
INCONCLUSIVE, keep on demo.

---

## Daily routine (now)

**Manual (the original workflow):** once a day, read the USD/JPY daily close
off TradingView and POST it:

```bash
curl -X POST localhost:8000/usdjpy/close \
  -H "Content-Type: application/json" \
  -d '{"close": 159.97, "account_size": 10000, "notify": true}'
```

The response tells you the signal (`BUY`/`SELL`/`NO TRADE`), the entry, the
stop (price + pips), the lot to use, and the estimated money risk vs your
daily loss cap. If it's a BUY/SELL, place that trade on demo at the given lot.
Three trading days later, the day+3 close you submit automatically fills the
exit and the R result — no manual exit step.

**Automated (the bot/scanner):** set `USDJPY_SCANNER_ENABLED=true`. Once a day
(default 22:00 UTC, configurable) the app fetches the latest USD/JPY daily
close itself, runs the engine, opens trades, fills due exits, and sends a
Telegram alert on a signal. You just act on the alert. You can also trigger a
fetch on demand:

```bash
curl -X POST "localhost:8000/usdjpy/scan?account_size=10000&notify=true"
```

---

## Endpoints

| Method & path | What it does |
|---|---|
| `POST /usdjpy/close` | Submit a daily close manually → signal + sizing. Body: `{close, date?, account_size?, notify?}` |
| `POST /usdjpy/scan` | Auto-fetch today's close from the feed and run it. Query: `account_size?`, `notify?` |
| `GET /usdjpy/signal` | Latest evaluated row (today's BUY/SELL/NO TRADE) |
| `GET /usdjpy/scoreboard` | Live tally: wins, total R, profit factor, expectancy, PASS/FAIL verdict |
| `GET /usdjpy/trades` | Trade journal (open + closed, with R) |
| `GET /usdjpy/closes` | Daily log (close, MA20, SD20, z, signal) |
| `GET /usdjpy/risk` | Risk + take-profit target table for all 10 account sizes |
| `GET /usdjpy/risk/{size}` | Sizing + targets for one account size, e.g. `/usdjpy/risk/10000` |
| `GET /usdjpy/rules` | The frozen rules |

---

## Risk & take-profit targets per account size (TENDAJI cheat sheet)

All figures are clean percentages of the account balance, and the per-trade lot
is fixed: **lot = balance ÷ 125,000** (0.01 lot per $1,250). `GET /usdjpy/risk`
returns the full grid; the rules are:

| Horizon | Profit target | Max loss |
|---|---|---|
| Daily minimum | 0.30% | 0.15% |
| Daily maximum | 0.60% | 0.30% |
| Weekly minimum | 1.50% | 0.75% |
| Weekly maximum | 3.00% | 1.50% |
| Monthly minimum | 6.00% | 3.00% |
| Monthly maximum | 12.0% | 6.00% |

Reference lots: $1,250→0.01, $2,500→0.02, $5,000→0.04, $10,000→0.08,
$25,000→0.2, $50,000→0.4, $100,000→0.8, $200,000→1.6, $400,000→3.2,
$800,000→6.4.

Because this strategy's exit is time-based (day+3 close), the money figures
above are the **targets/limits to manage the account to**, not per-trade
take-profit prices. On each signal the API also returns `trade_risk` — the
estimated dollar loss if the stop is hit at the fixed lot — and flags it when a
volatile (wide-stop) day would exceed your daily max-loss cap, so you can size
down.

---

## Seeding from the workbook

To start the live system warmed up with the same 20-day history the workbook
already has (instead of waiting 20 fresh trading days):

```bash
python -m usdjpy.seed_from_workbook
```

---

## Configuration (.env)

```
USDJPY_SCANNER_ENABLED=false   # true to run the daily auto-scan
USDJPY_SCAN_HOUR_UTC=22        # hour (0-23 UTC) to fetch the daily close
USDJPY_ACCOUNT_SIZE=0          # demo account size for sizing in alerts (0 = skip)
TWELVEDATA_API_KEY=...         # primary daily-close feed (Frankfurter ECB is a free fallback)
BOT_TOKEN=... / TELEGRAM_CHAT_ID=...   # for Telegram signal alerts
```

> **Note:** TradingView has no free official data API. The scanner pulls the
> same USD/JPY daily close from TwelveData (primary) with the free Frankfurter
> ECB rate as fallback. Use whatever single, consistent daily close you trust —
> the strategy only needs one close per trading day.

---

## Layout

```
usdjpy/engine.py             pure signal engine (frozen rules, z-score, stop, R, scoreboard)
usdjpy/risk_engine.py        TENDAJI sizing + per-account targets/limits
usdjpy/seed_from_workbook.py one-shot import of the workbook's closes
models/usdjpy_model.py       DB tables: daily closes + trades
services/usdjpy_service.py   orchestration over the DB (ingest, fill exits, scoreboard)
services/usdjpy_close_service.py  auto-fetch the daily close
services/usdjpy_scheduler.py once-a-day background scanner
services/usdjpy_alert.py     Telegram signal alerts
routes/usdjpy.py             the API
tests/                       engine + risk tests (verified against the workbook)
```

## Discipline

No changing thresholds, hold period, or instrument because results look bad —
that is the exact behaviour that turns an honest test into self-deception. The
verdict thresholds are frozen in `FROZEN_RULES`. If it fails, that is a real and
useful answer.
