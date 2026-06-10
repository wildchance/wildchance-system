# Hedge-Fund Portfolio Construction Framework

The code form of the multi-asset portfolio playbook. Where `USDJPY_SYSTEM.md`
is one frozen mean-reversion strategy, this is the **book-level** layer: how
capital is split across conviction tiers, how every candidate trade is scored
before entry, how risk is budgeted and de-risked into a drawdown, and how stops
and targets are laid out.

It lives in the `portfolio/` package — pure stdlib engines (so they unit-test
in isolation) — with a thin FastAPI surface in `routes/portfolio.py`. Every
number below is the single source of truth in `portfolio/framework.py`.

---

## 1. Portfolio Construction Framework

Four conviction tiers split the book **40 / 35 / 15 / 10**.

| Tier | Name | Allocation | Character |
|---|---|---|---|
| 1 | High Probability | 40% | Strong macro trend, positive carry / CB divergence, flow aligned, < 100 pips from target |
| 2 | Medium Probability | 35% | Trend exists, moderate momentum, **requires confirmation** |
| 3 | Speculative Macro | 15% | EM & commodity trades, **max 3% per position** |
| 4 | Metals Book | 10% | Long-term metals hedge |

**Tier 1 (40%)** — per-position weights sum to the tier:

| Pair | Bias | Confidence | Weight |
|---|---|---|---|
| EURJPY | Long | 9.0 | 8% |
| GBPJPY | Long | 9.0 | 8% |
| USDJPY | Long | 8.5 | 6% |
| GBPCAD | Long | 8.0 | 6% |
| SGDJPY | Long | 8.0 | 6% |
| AUDNZD | Long | 8.0 | 6% |

**Tier 2 (35%)** — EURGBP Short (7.5), NZDUSD Long (7), EURSGD Short (7),
GBPSEK Long (7), EURPLN Short (7), EURCAD Short (7).

**Tier 3 (15%)** — USDMXN, USDTRY, EURTRY, GBPTRY, EURMXN — all Long, **≤ 3%
each**.

**Tier 4 (10%)** — XAUUSD (long-term), XAGUSD (long-term), XPTUSD,
XPDUSD (speculative) — all Long.

**Current hedge-fund ranking (highest → lowest probability):** EURJPY ▸ GBPJPY ▸
USDJPY ▸ SGDJPY ▸ GBPCAD ▸ AUDNZD ▸ NZDUSD ▸ EURGBP (S) ▸ USDMXN ▸ XAUUSD.

---

## 2. Entry Precision Engine — score every trade

A weighted score out of 100. **Enter only when the total clears 80.**

| Component | Weight | What it reads |
|---|---|---|
| Trend | 40% | 200-EMA alignment (price on the correct side; rising/falling EMA confirms) |
| Momentum | 25% | RSI sweet spot 55–70 (mirror 30–45 for shorts) + MACD sign |
| Positioning | 20% | Crowd vs smart money — **fade the retail crowd** |
| Volatility | 15% | ATR compression followed by a breakout |

The trend gate is hard: price on the **wrong** side of the 200 EMA scores 0 for
the whole 40%, so an against-trend idea can never reach 80. Positioning is
contrarian — the framework's example is **USDCNH 100% retail long → SELL the
rallies**, which scores a perfect 20/20.

```bash
curl -X POST localhost:8000/portfolio/score -H "Content-Type: application/json" -d '{
  "bias": "LONG", "price": 101, "ema200": 100, "ema200_slope": 0.5,
  "rsi": 62, "macd_hist": 0.4, "retail_long_pct": 0.1,
  "atr_now": 0.7, "atr_avg": 1.0, "breakout": true
}'
# -> {"total": 98.0, "qualifies": true, "trend": 100, "momentum": 100, ...}
```

---

## 3. Dynamic Risk Management Model

**Per-trade risk by tier:** Tier 1 = 1.5%, Tier 2 = 1.0%, Tier 3 = 0.5%.
(Tier 4 metals is run as a hedge book, not on the per-trade ladder.)

**Maximum exposure per basket** (of the 100% risk budget):

| Basket | Max |
|---|---|
| JPY | 25% |
| USD | 25% |
| EUR | 20% |
| Metals | 15% |
| EM FX | 15% |

**Drawdown controls** — escalating de-risking on peak-to-trough drawdown:

| Drawdown | Action | Risk after |
|---|---|---|
| 5% | Reduce risk 25% | ×0.75 |
| 10% | Reduce risk 50% | ×0.50 |
| 15% | Close weakest positions | ×0.50 |
| 20% | Portfolio reset | ×0.00 |

`GET /portfolio/risk?balance=10000&drawdown_pct=0.10` returns the live budget —
the per-trade dollar risk per tier, already scaled by any active control (at 10%
down, Tier 1 risk halves from $150 to $75).

---

## 4. Stop-Loss & Take-Profit Architecture (ATR model)

One risk unit is **1 ATR**; targets ladder at fixed multiples:

```
SL  = 1 ATR
TP1 = 2 ATR   (1:2)
TP2 = 4 ATR   (1:4)
TP3 = 8 ATR   (1:8)
```

Worked example — **EURJPY** entry 184.97, stop 183.80 → 117-pip risk = 1 ATR,
so TP1/TP2/TP3 fall at **187.31 / 189.65 / 194.33**:

```bash
curl -X POST localhost:8000/portfolio/plan -H "Content-Type: application/json" \
  -d '{"symbol": "EURJPY", "bias": "LONG", "entry": 184.97, "stop": 183.80}'
```

Supply either `stop` (the stop distance *is* the ATR) or `atr` directly. Pip
distances use the instrument's pip size (JPY crosses & metals = 0.01, most FX =
0.0001).

### Restructuring rules

- **Weekly rebalance.**
- **Increase exposure** when trend score > 85, profit > 2R, correlation < 0.70.
- **Reduce exposure** when trend score < 60, profit deteriorates, or the central
  bank regime changes.

---

## Endpoints

| Method & path | What it does |
|---|---|
| `GET /portfolio` | The whole framework (tiers, ranking, summary) |
| `GET /portfolio/tiers` | The four tiers + allocations |
| `GET /portfolio/tiers/{n}` | One tier (1–4) |
| `GET /portfolio/candidates` | Every candidate across all tiers |
| `GET /portfolio/ranking` | Current hedge-fund ranking |
| `POST /portfolio/score` | Entry Precision Engine (> 80 to enter) |
| `GET /portfolio/risk` | Risk budget for `balance` & `drawdown_pct` |
| `POST /portfolio/exposure` | Check `{group: pct}` exposures vs the caps |
| `GET /portfolio/drawdown/{pct}` | The control that fires at a drawdown |
| `POST /portfolio/plan` | Build the ATR SL/TP ladder for a trade |

---

## Layout

```
portfolio/framework.py    tiers, candidates, weights, hedge-fund ranking, summary
portfolio/scoring.py      Entry Precision Engine (trend/momentum/positioning/vol, >80)
portfolio/risk_model.py   per-trade risk, basket exposure caps, drawdown controls
portfolio/sltp.py         ATR stop-loss / take-profit ladder (1/2/4/8 ATR)
routes/portfolio.py       the API
tests/                    test_portfolio_{framework,scoring,risk,sltp}.py
```

## Expected portfolio profile

Win rate **52–60%**, average R **2.8–4.5**, target annualized return **25–40%**,
max controlled drawdown **< 10%**.

> These engines compute the framework's rules; they do not place trades or pull
> a live feed. Wire them to your execution and data layer the same way the
> USD/JPY scanner is wired, when you're ready to act on the scores.
