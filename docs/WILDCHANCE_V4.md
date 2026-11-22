# WildChance v4 — Prop-Challenge System, Audit & Low-Cost Ops Plan

_Updated 2026-11-22._ This covers the 4-step pipeline, the prop-challenge guard, the lunar
overlay, the MT5 EA, why Telegram went quiet, and the cheapest way to run it all.

---

## 1. Why the signals stopped (root causes)

| # | Cause | Evidence | Fix |
|---|-------|----------|-----|
| 1 | **GitHub disabled `scheduled-scans` on 2026-10-02** (60 days with no commits). Nothing called the app after that, so no scans and no Telegram cards. | Actions API: `state: disabled_inactivity`, last run 2026-10-02. | Re-enable it in **Actions → scheduled-scans → Enable workflow**. A keepalive step now re-enables it on every run. |
| 2 | **Render free Postgres expires 30 days after creation**, then is deleted after a 14-day grace period. Free web services also sleep after 15 minutes idle. | Render docs/changelog. | Move the DB to Neon free (doesn't expire). See §6. |
| 3 | **TwelveData errors were silent.** An expired key or "out of credits" returned `[]`, which looks exactly like "no setup". | `services/ohlc_service.py` swallowed the error payload. | The error is now logged (`TwelveData error 429: …`). Optional Yahoo fallback: `YAHOO_FALLBACK_ENABLED`. |
| 4 | The workflow's `post` step never fails on HTTP errors, so a dead app still shows green runs. | `scheduled-scans.yml` uses `curl … \|\| true`. | Read the `-> HTTP` codes in the run log after re-enabling. |

**Do these in order:** (1) re-enable the workflow; (2) open `<APP_BASE_URL>/health`: if
`db_reachable` is false, the Render DB is gone, so follow §6; (3) check the app log for
`TwelveData error`.

---

## 2. Architecture (v4)

```
Stage 0  Macro gate (weekly)      COT · currency meter · fear/greed · options walls → macro_bias
Stage 1  Probability + CHOP-TRAP  pipeline/engine.py  score_setup()   AES · BPSR · ADX<20 demotion
Stage 2  Classification           ANCHOR (R:R≥1.8, top 8) · RANGE · WATCH · OUTLIER (bottom 20% cut)
Stage 3  Overlay                  [CLUSTER-RISK] shared currency · [NEWS] 13 releases · [LUNAR-SHIFT]
Stage 4  ±1σ ladder + prop sizing  compact 8→+1 fade→+1 runner · full 10→11→12→13 · max concurrent
            │
            ├── POST /pipeline/audit            (watchlist → R01…R_N, actions, risk %)
            ├── GET  /propfirm/challenge/plan   (losers-to-breach, trades-to-target)
            ├── GET  /propfirm/challenge/guard  (same kill-switch as bridge + EA)
            ├── GET  /lunar/state|check|backtest
            │
   app queue (execution_orders) ──► mt5_bridge/connector.py + equity_guard.py (VPS)
                                    └─ or standalone: mt5_bridge/WildChance_PropEA_v4.mq5
```

One rule set lives in three places, and they all use the same numbers:
`propfirm/challenge.py` (app), `mt5_bridge/equity_guard.py` (Python bridge), and
`CheckPropGuard()` in the EA.

### Flowchart → code map

| Flowchart box | Where it lives |
|---|---|
| COT report, currency meter, sentiment, fear & greed | `services/cftc_service.py`, `cot/`, `services/free_macro_feeds.py` (fear/greed), MiroFish (`/mirofish`) → feeds `macro_bias` |
| Open interest, put/call, expiry, bar-chart options | `services/options_service.py`, `services/flow_service.py` |
| FastBull depth of market | **not available** (no free DoM feed). The EA uses spread and tick volume as a proxy |
| Price action: liquidity, OB, FVG/void, premium/discount, volume profile | `pdarrays/`, `gold/volume_profile.py`, EA M/W + stop hunts |
| Sessions (Asia, London, NY, overlaps) | `cbdr/engine.py`, `propfirm.active_sessions`, EA session filter |
| Check news (PPI, CPI, JOLTS, NFP …) | `services/news_guard.py`, `pipeline.NEWS_EVENTS`, EA `CalendarValueHistory` |

---

## 3. Prop-firm calibration (FundingPips as the base account)

Defaults (`fundingpips_2step_8`): Phase 1 target 8%, Phase 2 target 5%, daily loss 5%
(from the higher of start-of-day balance or equity), max loss 10%, 3 minimum trading days.
**These are from third-party summaries. Confirm them on fundingpips.com before trading.**
Other presets: `fundingpips_2step_10`, `fundingpips_2step_pro` (the daily figure is
unverified), `ftmo_2step`.

| Setting | Value | Why |
|---|---|---|
| Risk per trade | **0.4–0.5%** | At 0.5%, 7 straight losers reach the 3.5% daily guard and 16 reach the 8% max guard. At 1%, three bad trades end the day. |
| Our daily stop | **3.5%** (70% of 5%) | Slippage and spread spikes can overshoot a stop. |
| Our max stop | **8%** (80% of 10%) | Same reason. |
| Concurrent trades | **2–3** | 3 × 0.5% = 1.5% exposure in one correlated move. `challenge_plan` warns if this exceeds the daily guard. |
| Red news | **Flat (PAUSE)** | Most firms restrict trading or straddles around high-impact releases. Prop mode removes brackets. |

Targets of 6, 8, 10 and 12%: `GET /propfirm/challenge/plan?balance=10000&risk_pct=0.5&win_rate=0.45&avg_r=1.8`
shows the net R and expected trades for each phase. With 45% wins at 1.8R, expectancy is
+0.26R per trade, so 16R for 8% takes about 62 trades. Faster passes need better
expectancy, not bigger risk.

The existing TENDAJI cheat-sheet tiers (`/propfirm/limits`, 6/8/10/12) still work for
per-session budgeting. The challenge module sits on top of them.

---

## 4. Lunar overlay: what it is and isn't

* Phase times use Meeus' true New/Full Moon (accurate to minutes, no API). They are
  checked against two published reference New Moons, 6 Jan 2000 18:14 UTC and
  Meeus' 1977 worked example (see `tests/test_wildchance_v4.py`). The naive
  29.53-day formula in the draft was off by up to about 14 hours.
* **There is no established evidence that moon phase predicts price direction.** It is
  wired in as an **optional** gate that defaults OFF (`LUNAR_GATE_ENABLED=false`,
  EA `InpUseLunarEngine=false`).
* Before switching it on, run `GET /lunar/backtest?symbol=XAU/USD&hold_days=14`. It
  compares New-Moon longs and Full-Moon shorts with an "any day" baseline and gives a
  t-stat. On a random walk it correctly says "NO EVIDENCE". Enable it only on a
  positive result, and then forward-test on demo.
* Treating the observation window as a **risk reducer** (half size around syzygy) is
  the defensible use, even without a directional edge.

---

## 5. MT5 Expert Advisor (`mt5_bridge/WildChance_PropEA_v4.mq5`)

Built from your v2.2 indicator logic. **I could not compile it here (no MetaEditor).**
Compile with F7, run it in the Strategy Tester, then on DEMO.

Fixes compared with the draft v4 that was pasted into chat:

| Problem in draft | Effect | Fix |
|---|---|---|
| `g_initialBalance` re-captured in `OnInit` | Every terminal restart **reset the max-drawdown line** to current balance. After a 6% loss plus a restart, the EA would allow another 8%. | Persisted per account login in GlobalVariables; `InpInitialBalance` overrides. |
| Daily floor trailed the intraday equity high | Locked the account after normal giveback on winning days. | Day reference = max(balance, equity) at server midnight (the FundingPips rule). |
| Hunt bar indexes stored relative to a sliding 1000-bar window | After the first stop hunt, `RecentHunt()` was **always true**, which disabled the sweep filter. | Tracked by bar time. |
| Asia-range hunts, ID50 trend-day filter and wrong-side SL check from v2.2 were dropped | Looser entries than your indicator. | Restored. |
| Lot size clamped **up** to the minimum lot | Small accounts or wide stops risked more than 0.5%. | The trade is skipped ("MIN LOT WOULD OVER-RISK"). |
| `ORDER_FILLING_IOC` hardcoded | Rejected on FOK-only brokers. | `SetTypeFillingBySymbol`. |
| `ResultOrder()` used as the position ticket | Fragile. | The position is looked up by magic and symbol. |
| Markdown stripped the `*` operators (`dt.year10000`, `point10.0`) | Would not compile. | Rewritten. |
| v2.2 bug: an M pattern's candle confirmation checked **bullish** closes | M scores were inflated by up-candles. | Bearish confirmation for M, bullish for W. |
| News filter in the Strategy Tester | `CalendarValueHistory` returns nothing in backtests. | Skipped in the tester and stated here. Backtests will include news days. |

Notes: partial closes need a **hedging** account (the usual prop setup). Use the same
magic number on every chart so `InpMaxConcurrent` counts across symbols. "Randomize SL"
defaults off. Don't use it to disguise copied trades across accounts, because firms
treat that as a rules breach.

---

## 6. Running it cheaply (current costs: verify before paying)

| Need | $0 option | Low-cost option | Notes |
|---|---|---|---|
| App (FastAPI) | Render free web (sleeps after 15 min; the GitHub cron wakes it in about 1 min, and `curl -m 90` covers that) | Render Starter or Railway Hobby (always on, a few $/month) | Background schedulers and the Polygon stream only run while awake on free. Keep scans in GitHub Actions. |
| Database | **Neon free Postgres** (doesn't expire; `database/db.py` already handles Neon SSL) | Neon/Supabase paid | Set `DATABASE_URL` to the Neon URL. Tables are auto-created on boot. |
| Scheduler | GitHub Actions cron (free on public repos, included minutes on private) | — | The keepalive stops the 60-day disable. |
| Execution | MT5 EA alone (no app needed to trade) | Windows VPS for MT5 + `connector.py` | The EA already has every guard. The bridge is for app-generated signals. |
| Telegram | Bot API (free) | — | Needs the app awake. Webhooks wake a sleeping Render service. |

**Market data, cheapest first:**

| Source | Cost | Use for | Caveat |
|---|---|---|---|
| **MT5 broker feed** (inside the EA and terminal) | Free | Execution, entries, SL/TP | These are the prices your prop account actually fills on, so the best source for trade levels. |
| TwelveData (current) | Free tier, paid from about $29/month | Gold, FX, indices bars for app signals | The free daily credit is small and ~26 cron jobs can exhaust it. Watch for `TwelveData error` logs. |
| Yahoo Finance (`YAHOO_FALLBACK_ENABLED`) | Free, unofficial | Bias, regime, backtests | Gold is futures (GC=F), not spot. No SLA. |
| FRED, CFTC COT, ForexFactory calendar, alternative.me | Free | Macro, COT, news, fear/greed | Already wired. |
| Finnhub | Free tier (60/min) | Calendar, news | Forex and metal coverage on the free tier is uncertain. |
| Massive (formerly Polygon) | Paid | Tick streaming | Only when you scale. |

**Upgrade path:** Neon + Render free + GitHub cron (about $0) → add a Windows VPS when
the EA or bridge goes live on demo → paid TwelveData or Render Starter only once the
signal volume justifies it.

---

## 7. Your macro documents: audit notes

* **Master Execution Matrix** says gold spot is **$4,120**. Your 21 Nov monthly chart
  shows **$3,537.83** (−8.7% on the month), so every gold level and size in that doc
  needs re-basing.
* **Quantitative Drawdown Matrix**:
  * BTC shows a **negative size (−$3,317)**. Its stop (109,000) sits *above* the
    104,000 trigger for a long accumulation, so the risk distance has the wrong sign.
  * The gold rows (trigger 3,100, floor 2,600, stop 2,450) don't match the Master
    Matrix (4,800 peak, 3,900 floor, 3,700 stop). Pick one set.
* Both docs size at **2% risk per trade on $100k**. That is a portfolio and investment
  sizing model, and four losers would break a prop challenge's daily limit. Use them
  for direction and levels only, and size with `/propfirm/challenge/plan` (0.4–0.5%).
* The Hedging Playbook and Indices Categorisation are narrative scenarios (the Nvidia
  audit, a "Blue Sweep" outcome). Treat them as Stage-0 macro bias inputs, not as
  facts the engine trades on.

---

## 8. Quick start

```bash
curl -s "$APP/lunar/state"
curl -s "$APP/propfirm/challenge/plan?balance=10000&risk_pct=0.5"
curl -s -X POST "$APP/pipeline/audit" -H 'content-type: application/json' -d '{
  "risk_pct": 0.5, "max_concurrent": 3, "macro_bias": "BOTH",
  "watchlist": [
    {"symbol":"XAUUSD","side":"BUY","win_rate":0.48,"rr":2.1,"adx":27,"cost_r":0.05,
     "entry":3540,"stop":3505,"sigma":75,"news":["US CPI"]},
    {"symbol":"EURUSD","side":"SELL","win_rate":0.47,"rr":1.9,"adx":24}
  ]}'
```

Weekly calibration: `POST /pipeline/calibration` with `[{"prob":0.6,"won":true,"r":1.8}, …]`
returns the Brier score (lower is better; 0.25 is a coin flip), win rate and expectancy.
