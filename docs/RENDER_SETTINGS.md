# Render settings — the complete list

Render → your service → **Environment** → *Add Environment Variable*. After saving,
Render redeploys. **Never paste these values into chat**, a commit, or a screenshot. If one
leaks, rotate it at the provider and update it here.

Legend: 🔴 required · 🟠 strongly recommended · 🟢 optional · ⚪ leave the default (only set
it to change behaviour)

## 1. Core — the system does not run without these
| Setting | Status | What to put | Why |
|---|---|---|---|
| `DATABASE_URL` | 🔴 | Neon connection string (`postgresql://…?sslmode=require`) | Every table: cases, members, positions, cron slots. Without it the app falls back to a throw-away SQLite file that is wiped on every deploy. |
| `TWELVEDATA_API_KEY` | 🔴 | TwelveData key | All prices. `TWELVEDATA_KEY` is also accepted. |
| `TELEGRAM_BOT_TOKEN` | 🔴 | BotFather token | Every alert. `BOT_TOKEN` is also accepted. |
| `TELEGRAM_CHAT_ID` | 🔴 | Channel id (`-100…`) | Where alerts go. |
| `CRON_TOKEN` | 🔴 | Long random string | cron-job.org calls `/cron/tick?token=…` every 5 min. |
| `OPS_TOKEN` | 🟠 | A *different* long random string | Your admin key for `?token=` on manual endpoints (sniper, yearly, backtests). If unset, `CRON_TOKEN` is used. |
| `BRIDGE_TOKEN` | 🟠 | Long random string | The MT5 EA's data feed. Must match the EA input. |

Make tokens with: `python -c "import secrets; print(secrets.token_urlsafe(32))"`

## 2. Data & intelligence
| Setting | Status | Notes |
|---|---|---|
| `NVIDIA_API_KEY` | 🟠 | AI sentiment, Polymarket reading, macro brief. |
| `FRED_KEY` | 🟠 | Macro dashboard (free at fred.stlouisfed.org). |
| `YAHOO_FALLBACK_ENABLED` | 🟠 `true` | Free backup prices when TwelveData is rate-limited. |
| `POLYGON_API_KEY` | 🟢 | Stock daily bars (NVDA etc.). |
| `FINNHUB_KEY` | 🟢 | Stock quotes. `FINNHUB_API_KEY` is also accepted. |
| `EIA_KEY` | 🟢 | Oil inventory data. |
| `TIINGO_API_KEY` | 🟠 | Long daily history (FX, stocks, crypto) — second history feed after Stooq. |
| `ALPHAVANTAGE_API_KEY` | 🟠 | News sentiment per pair/coin/stock → the council's **news** agent (refreshed 6-hourly to stay inside ~25 calls/day). |
| `MARKETAUX_API_KEY` | 🟢 | News sentiment for stocks (same agent). |
| `COINGECKO_API_KEY` | 🟢 | Crypto market cap + BTC dominance (demo key; works without, rate-limited). |
| `BLS_API_KEY` | 🟢 | US CPI / jobs / wages (works without a key at a lower limit). |
| *(no key)* | — | Stooq (decades of daily history → automatic base rates), Binance (crypto), ECB, US Treasury, CBOE VIX — nothing to set. |
| `MYFXBOOK_EMAIL` / `MYFXBOOK_PASSWORD` | 🟢 | Retail sentiment via your Myfxbook login. |
| `MIROFISH_BASE_URL` | 🟢 | Only if you host a MiroFish server; the council adds its vote automatically. |
| `OPTIONS_FEED_URL` / `OPTIONS_FEED_KEY` | 🟢 | Only if you have a gold-options JSON feed. |

## 3. Members website & billing
| Setting | Status | Notes |
|---|---|---|
| `PUBLIC_BASE_URL` | 🟠 | `https://wildchance-system.onrender.com` (or your own domain) — payment return links. |
| `PAYSTACK_SECRET_KEY` | 🟠 | Paystack → Settings → API keys (secret key). |
| `PAYSTACK_CURRENCY` | ⚪ `KES` | Currency charged on Paystack. |
| `PAYSTACK_FX` | ⚪ `130` | KES per USD used to convert plan prices. |
| `PAYSTACK_PRICE_TRADER` (etc.) | 🟢 | A fixed local price per plan instead of the FX conversion. |
| `STRIPE_SECRET_KEY` | 🟠 | Stripe → Developers → API keys. |
| `STRIPE_WEBHOOK_SECRET` | 🟠 | Stripe → Webhooks → your endpoint → signing secret (`whsec_…`). |
| `BILLING_WEBHOOK_SECRET` | 🟠 | Long random string for the manual billing bridge. |
| `MEMBERS_TELEGRAM_INVITE` | 🟠 | Private group invite link shown to paid members. |
| `ALLOWED_ORIGINS` | 🟢 | Your site domain(s), comma-separated, instead of `*`. |
| `SAAS_GATING_ENABLED` | ⚪ `false` | `true` makes premium gold endpoints require a paid member key. |

Webhook URLs to paste into the providers:
`https://<your-app>/billing/paystack/webhook` and `https://<your-app>/billing/stripe/webhook`.

## 4. Flash cards (new)
| Setting | Status | Notes |
|---|---|---|
| `FLASH_QR_URL` | 🟠 | Your Telegram join link — the QR on every card. |
| `FLASH_CHAT_ID` | 🟢 | Group for the cards; defaults to `TELEGRAM_CHAT_ID`. |
| `FLASH_NAME` | ⚪ `Wildchance` | Name on the card. |
| `FLASH_CARDS` | ⚪ `true` | `false` stops auto-posting when a sniper trade hits TP1. |

## 5. Trading rules — defaults already match your rules
| Setting | Default | Meaning |
|---|---|---|
| `MIN_RR` | `1.3` | Minimum reward:risk (your first rung). |
| `MIN_SL_PIPS` | `10` | FX minimum stop. |
| `MIN_SL_GOLD_USD` | `3` | Gold minimum stop ($). |
| `MIN_SL_BTC_USD` | `50` | BTC minimum stop ($). |
| `BTC_CONTRACT_SIZE` | `1` | Coins per lot at your broker. |
| `INDEX_USD_PER_POINT` | `1` | Index CFDs: $ per full point per 1.00 lot (pip = 0.1 point). |
| `STOCK_LOT_SHARES` | `1` | Stock CFDs: shares per 1.00 lot (pip = $0.01). |
| `AGENT_MUTE_BELOW` / `AGENT_MUTE_MIN_N` | `0.40` / `8` | A council agent right < 40 % of the time over ≥ 8 resolved live shots is muted. |
| `REALISTIC_MIN_HIST` | `25` | Targets only as far as ≥ 25 % of years travelled. |
| `SWING_HORIZON_DAYS` | `5` | Expected-move horizon for the TP3 sweet spot. |
| `BLOCK_CBDR_FORMING` | `true` | No entries 14:00–20:00 NY. |
| `WEEKLY_PRIOR_FALLBACK` | `true` | Mondays use last week's profile. |
| `ZC_NOTIFY_GRADES` | `A` | Z-score cards sent (e.g. `A,B` for more). |
| `SNIPER_SYMBOLS` | `EUR/USD,GBP/USD,USD/JPY,XAU/USD,BTC/USD` | Sniper markets. |
| `SNIPER_MIN_RATE` | `0.5` | Minimum recent hit-rate for a level's role. |
| `COUNCIL_MIN_SCORE` | `1` | Net council votes needed for GO. |
| `LOT_CARD_BALANCES` | `2500,5000` | Account sizes on the lot ladder. |
| `USDJPY_ACCOUNT_SIZE` | — | Your USD/JPY account size for its scanner. |
| `CHALLENGE_PRESET` | — | FundingPips preset for the challenge tracker. |
| `MACRO_BIAS` | — | Manual locks, e.g. `{"XAUUSD":"SELL_ONLY"}` (always wins). |
| `MACRO_REGIMES` | — | Regime presets (e.g. the Trump-1 DXY analog). |
| `SENTIMENT_AUTO_BIAS` | `false` | `true` lets news+Polymarket+retail set locks. |
| `AUTO_EVENT_BOXES` | `true` | Automatic FOMC + chained boxes. |
| `FOMC_EXTRA_DATES` | — | Add 2027 Fed dates when published, e.g. `2027-01-27,2027-03-17`. |
| `GRID_STEPS` | gold 15 · EUR 0.006 · GBP 0.007 | JSON override, e.g. `{"USDJPY":0.5}`. |
| `YEARLY_CBDR_YEARS` | `7` | Years of yearly boxes. |
| `TD_RATE_PER_MIN` / `TD_DAILY_LIMIT` | `7` / `790` | TwelveData free-plan limits — raise only on a paid plan. |

## 6. Execution (live trading) — leave OFF until the EA is proven
| Setting | Default | Notes |
|---|---|---|
| `EXECUTION_ENABLED` | `false` | Master switch for auto-execution. |
| `EXECUTION_TOKEN` | — | Needed only when execution is on. |
| `FLEET_ACCOUNTS`, `STRATEGY_ACCOUNTS`, `MT5_ACCOUNT` | — | Multi-account fan-out. |

## 7. Do NOT set (legacy / would double-run)
| Setting | Why |
|---|---|
| `WILDCHANCE_SCHEDULER_ENABLED` | Keep `false`/unset — `/cron/tick` already runs every job; enabling this would send duplicates. |
| `REDIS_URL` | Not needed on free Render; caching is in-process. |
| `WHATSAPP_*` | Old WhatsApp path, unused. |
| `SERVICE_KEY`, `WEBHOOK_BASE_URL`, `STATE_DIR`, `GUARD_STATE_FILE` | Internal/legacy — defaults are correct. |

## 8. Outside Render
- **cron-job.org**: `https://<your-app>/cron/tick?token=<CRON_TOKEN>` every 5 minutes.
- **TradingView alerts**: add `?token=<OPS_TOKEN>` to the webhook URL.
- **MT5 EA inputs**: server URL + the same `BRIDGE_TOKEN`.

## Check after deploy
Open `https://<your-app>/health` — it reports which keys are set (`twelvedata_key_set`,
database, Telegram) without showing their values.

## Trade journal (every closed trade: win / loss / breakeven)

| Key | Default | What it does |
|---|---|---|
| `JOURNAL_BE_PIPS` | `1.0` | a close within ±this many pips counts as **breakeven** |
| `JOURNAL_BE_USD` | `0.50` | …or within ±this many dollars net |
| `JOURNAL_SHOW_USD` | `false` | show $ net / expectancy in the Telegram summaries (pips + counts only by default) |
| `FLASH_MAX_AGE_MIN` | `120` | only closes this recent get a flash card (history backfill goes to the journal only) |

EA v4.53 inputs: `InpDealPushSec` (10 s), `InpDealBackfillDays` (90 — the first run sends 90 days of history to the journal).
Endpoints (ops token): `/journal/stats?days=30`, `/journal/trades`, `/journal/report?period=day|week|month`.

## Economic calendar (automatic, no key needed)

Forex Factory's public weekly export, refreshed 00/06/12/18:15 UTC. HIGH-impact releases block new entries
for that currency (USD also covers gold, BTC, US indices, NVDA). Each release's move is measured nightly; a MEDIUM
event that averages ≥ `CAL_PROMOTE_PIPS` gets promoted to a blackout. `data/event_blackouts.csv` still works for
anything extra (hearings, OPEC, owner notes).

| Key | Default | What it does |
|---|---|---|
| `CAL_BEFORE_MIN` | `30` | minutes before a release with no new entries |
| `CAL_AFTER_MIN` | `60` | minutes after |
| `CAL_PROMOTE_PIPS` | `25` | a medium event averaging this move (≥2 samples) is treated as high impact |

Endpoints: `/data/calendar?days=7`, `/data/calendar/week?notify=true`, `/data/calendar/moves`, `/data/calendar/refresh`.

## Price pool (free sources so TwelveData's 7/min limit never stalls the app)

Every bar request: EA broker feed → TwelveData if a slot is free within `POOL_TD_WAIT_S` → free pool
(Binance for crypto; Yahoo for FX, indices, stocks; Tiingo FX intraday if `TIINGO_API_KEY`; gold via Binance PAXG
or COMEX futures, basis-corrected to the last real spot price and never used without one) → TwelveData full queue.
Source health shows in `/data/sources` (`yahoo_intraday`, `binance`, `tiingo_fx`).

| Key | Default | What it does |
|---|---|---|
| `PRICE_POOL_ENABLED` | `true` | `false` = old behaviour (TwelveData queue only) |
| `POOL_TD_WAIT_S` | `3` | how long to wait for a TwelveData slot before using the free pool |

## Tradier options (licensed dealer levels — tried before CBOE's public file)

| Key | Default | What it does |
|---|---|---|
| `TRADIER_TOKEN` | — | Tradier API access token (brokerage account → API Access, or developer sandbox token) |
| `TRADIER_BASE` | `https://api.tradier.com/v1` | sandbox token → `https://sandbox.tradier.com/v1` (15-min delayed) |
| `TRADIER_MAX_EXPIRIES` | `6` | chains read per underlying (≤ `OPT_MAX_DAYS`) |

Polygon options: uses your existing `POLYGON_API_KEY` (`/v3/snapshot/options`, tried after Tradier, before CBOE).
Needs a Polygon plan that includes OPTIONS (Options Starter or higher); on Stocks-only/free plans it fails
quietly and `/data/sources` shows `polygon_options` with the error. `POLYGON_OPT_PAGES` (8) × 250 contracts max.

## CME Daily Bulletin (real COMEX gold options open interest)

Gold dealer levels come FIRST from the CME Daily Bulletin (sections 62 + 64): OG monthly + OG1–OG4 weekly calls/puts,
open interest per strike, strikes within ±`CME_BAND_PCT` (12%) of the future, shifted onto the front future and
mapped to live spot. Refreshed 01:40 and 14:40 UTC on weekdays from CME's public bulletin URLs; if those are refused,
drop the two PDFs (`Section62_Metals_Futures_Products.pdf`, `Section64_Metals_Option_Products.pdf`) into `data/cme/`
(GitHub → Add file → Upload files). `data/cme_gold_seed.json` holds the last owner-supplied bulletin.
Needs `pdftotext` (poppler-utils — installed by the Dockerfile). Endpoints: `/data/cme/gold`, `/data/cme/refresh`.
Currencies (same bulletin, sections 27–30 + 33–36): GBP/USD, USD/CAD, USD/JPY, USD/CHF futures-options OI by strike,
monthly + weekly Friday expiries, converted to pair terms (yen/franc/CAD calls become USD/xxx puts). Band ±`CME_FX_BAND_PCT` (6%).
`data/cme_fx_seed.json` holds the last owner-supplied bulletin; `/data/cme/fx/USD/JPY` shows the biggest strikes.

## Asia → London session trader + Z-setup stop floor

`/cbdr/session/run` (03:05–11:35 NY, every 30 min, weekdays): Asian box (19:00→02:45 NY) → SWEEP-reclaim or BREAKOUT
on 15m closes, ONLY with Monday's weekly bias and never while the daily CBDR boxes are back across Monday's mean
(REVERSING). TP1/TP2 inside the Asian ladder, TP3 at the nearest options-expiry level (pin / 25Δ) or dealer wall.
`SESSION_SYMBOLS` (default EUR/USD, GBP/USD, USD/JPY, USD/CHF, USD/CAD, XAU/USD). One card per symbol per day.

Z-setups: stop ≥ `ZC_MIN_RISK_ATR` (0.25) × daily ATR(14), ≥ `ZC_EVENT_RISK_ATR` (0.4) × ATR when a high-impact
release for the pair is within 24 h — no more 10-pip stops inside a pre-PCE day's noise.

## One council for every engine

`COUNCIL_GATE_ALL` (default `true`): the auto CBDR "Wildchance Signal", swing fades, Z-setups and Asia→London cards
all convene the same agent council as the sniper and only send on **GO** (≥ `COUNCIL_MIN_FOR` for, ≤ `COUNCIL_MAX_AGAINST`
against); each card carries the 🏛 council line. `false` = append the line but send anyway.
Auto CBDR signals also need their best target ≥ `MIN_RR` (1.3R). Asia→London: a trigger older than `SESSION_FRESH_MIN`
(35) minutes, already through the stop / past TP1, or > 0.5R away from the entry is not sent.
