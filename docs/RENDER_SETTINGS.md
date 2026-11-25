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

EUR/USD (section 39): calls and puts share one listing without labels — each row is classified from its delta vs the
future (a call is in the money below the future). Validated on bulletin #226: 76/76 rows agree with the sign of their
settlement change, and the EUU DEC26 block sums to the bulletin's own TOTAL (154,547).

## Owner reminders + polite calendar

| Key | Default | What it does |
|---|---|---|
| `OWNER_CHAT_ID` | — | your own Telegram user id (ask @userinfobot), after pressing Start on the bot: private reminders |
| `CME_STALE_DAYS` | `3` | Saturday/Sunday 09:15 UTC: remind to upload the bulletin when the dealer levels are this old |
| `CAL_MIN_REFRESH_MIN` | `60` | never call the Forex Factory export more often than this |
| `CAL_BACKOFF_H` | `3` | after an HTTP 429 (rate limit) wait this long; stored events keep working |

## Signal tracker (flash cards without MT5) + retail average prices

Every card sent by the sniper, Z-setups, auto CBDR and Asia→London is recorded and checked every 5 min on live prices:
TP1 → flash card at once (then the stop moves to entry), TP2/TP3 → more cards, stop before TP1 → loss, still open after
`TRACK_EXPIRE_H` (72) → closed at the last price (±`JOURNAL_BE_PIPS` = breakeven). Manual signals (e.g. from another
platform): `/signals/track?symbol=USDJPY&side=SELL&entry=159.3&stop=159.467&tp=159.064,158.746,158.11&pending=true`.
Journal: `/signals/stats`, open: `/signals/open`.

`data/retail_levels.csv` (owner paste of Myfxbook community outlook): trapped retail longs' / shorts' average entry
prices become 'retail avg' zones for the sweeper and Asia→London targets.

## Weekly data audit

`/ops/data-audit` (also a line in `/ops/checklist`; sent privately to `OWNER_CHAT_ID` every Saturday 09:30 UTC):
vol-screen strikes (future expiry left, screenshot age > `DATA_STALE_DAYS` (7), strike order, distance from live price
vs 3× the expiry's implied move, interpolated / cropped screens), CME chains (bulletin age, strike count, put/call,
front future vs live), Myfxbook paste age, calendar refresh, failing feeds.

CME bulletin now also covers **AUD/USD** (section 38: futures from its own table) and the **indices**: S&P 500 (47 + 48),
Nasdaq-100 (40: EOM, QN weeklies, heading-only weekly blocks), Dow (60: strikes quoted /10). `CME_INDEX_BAND_PCT` (10).
Agent scoring now grades the council on every engine's tracked signals (TP1 first = held, stop first = broke), and the
daily / weekly journal reports include the signal tracker's win / loss / breakeven results by engine.

## Hedge-fund FX layer: strength matrix · event rotation · exposure caps

- `/fx/strength` — USD EUR GBP JPY CHF CAD AUD NZD scored −1…+1 (5-day momentum, COT percentile, retail contrarian,
  CME put/call) → strongest-vs-weakest pairings. Telegram card 06:40 UTC weekdays; the top pairings join the sniper scan.
- `/fx/rotation` — any currency with a high-impact release within `ROTATE_AHEAD_H` (3 h) steps aside; the sniper rotates
  to pairs WITHOUT it, ranked by strength gap and by how little that release has historically moved them (the calendar
  now measures 12 crosses on every release). Card once per event set per day.
- `/fx/exposure` — open tracked signals netted per currency; a card that would push any currency beyond `EXPOSURE_MAX`
  (2) units is blocked in the council gate and the sniper. `STRENGTH_MIN_GAP` (0.5) = minimum score gap for a pairing.

## Research layer (A–C)

A. **Point-in-time store** — table `signal_snapshots`: every tracked card with what the system knew at send time
(levels, council votes, currency strength + gap, blocked currencies, COT / retail read, options levels, VIX regime,
next events, hour / weekday) and, once resolved, the result and realised R. Export: `/research/snapshots`.
B. **Walk-forward backtests** — `/research/backtest?engine=z|asia&symbol=EUR/USD`, `/research/backtest/all?engine=…`;
Sunday 12:10 NY scorecards on Telegram. Win rate, expectancy R, profit factor, max drawdown R, Sharpe, 4-fold stability.
C. **Weighted council** — P(TP1 before stop) from each agent's measured accuracy (log-odds); shown on every card,
enforced once agents have `COUNCIL_PROB_MIN_CASES` (20) graded signals: GO also needs ≥ `COUNCIL_MIN_PROB` (0.60).
`COUNCIL_BASE_RATE` (0.50) = prior. The 4-for / 0-against floor stays. Calibration: `/research/council`.

## Risk budget (D), regimes (E), terminal (F)

- **Risk budget**: risk % per trade = `RISK_BASE_PCT` (0.5) × ATR-percentile × VIX × event × open-trade factors, floor
  `RISK_MIN_PCT` (0.15), never above the base. Every lot ladder uses it; cards show `⚖️ risk x%` and why.
- **Regime**: TRENDING (efficiency ratio ≥ `REGIME_TREND_ER` 0.35) / RANGING / STRESSED (VIX ≥ 30 or ATR top 10 %) /
  EVENT. Reversion engines (Z, swing, Asia sweep) may not fade a trend; continuation engines (auto CBDR, Asia breakout)
  are held in ranges. Shown on every card (`🌡 Regime …`) and stored with each snapshot.
- **Terminal**: `https://wildchance-system.onrender.com/terminal?token=YOUR_OPS_TOKEN` — strength matrix, rotation,
  exposure + open signals, signal results by engine, regimes, CME dealer walls (on live spot), calendar, COT/retail,
  backtests, data audit. Refreshes every 60 s; works on a phone.

## Subscriber terminal

`https://wildchance-system.onrender.com/members/terminal` — members log in with email + password (or their key), same
as /members (a "Terminal" link is in its menu). Paid tiers (Trader and up): currency strength + pairings, event
rotation, CME dealer walls on live spot, regimes, calendar, COT/retail and the track record (closed signals only).
Free tier: strongest/weakest currencies, the calendar, the track-record summary and an upgrade button. Never shown:
open signals/entries, exposure, backtests, the data audit. Data is built once a minute for everyone.

## Error visibility (point 3)

`services/errors.py` counts every crash (`http_500`), feed-had-no-data response (`upstream_data`, 502/503/504),
slow request (> `SLOW_REQUEST_S` 20 s), failed / slow scheduled call (`cron_fail`, `cron_slow` > `SLOW_CRON_S` 120 s),
failed Telegram send (`telegram_fail`) and errors swallowed inside the signal tracker, council gate, snapshots and
regime engine (`swallowed`). `/ops/errors` (today + last events), a checklist line, a terminal tile, and a private
daily digest at 05:50 UTC (`errors_digest`, needs `OWNER_CHAT_ID`). The scheduler now has a FAST lane: the 5-minute
signal tracker runs even while a long hourly bundle is still working, so TP flash cards are never delayed by it.

## Hardening (7–11)

- Sniper: each market is scanned in isolation — one failure is logged (`/ops/errors`) and the rest still run.
- Schema: versioned migrations in `database/migrations.py` (recorded in `schema_migrations`, once-only, never block
  startup); `/ops/schema` + a checklist line show pending migrations and any model column missing in Neon.
- Backups: every Sunday 03:20 UTC a gzip'd JSON of the research tables is sent to your Telegram (`OWNER_CHAT_ID`);
  `/ops/backup` downloads one on demand. Member / billing tables are not included.
- Terminal token: open `/terminal?token=…` once; it is stored in that browser, removed from the address bar, and sent
  as the `X-Ops-Token` header from then on.
- Runtime: Python 3.12 (Docker + CI), python-telegram-bot 21.11.1, httpx 0.27.

## Liquidity guard + vol-screen walls

- `CME_MIN_OI` (2000): a CME chain with fewer contracts (calls + puts) is not drawn as walls — today that parks
  the Dow (~100) and NZD/USD (~600, puts only). The data audit lists it as "too thin".
- Pairs with no liquid listed options (FX crosses, NZD/USD, thin chains) use the owner's vol-screen strikes: the
  nearest expiry with both sides → 10Δ/25Δ call ceilings, ATM pin, 25Δ/10Δ put floors. They show in the terminals,
  the sniper zones and on event-rotation picks (`[walls floor / ceiling]`). Live today: GBP/JPY, EUR/JPY. Send
  EUR/GBP, EUR/AUD, EUR/CHF, NZD/USD screens to light up those pairs.

## Flash cards, partials, owner copies

- A flash card is posted once per trade (symbol + side + open + close) within `FLASH_DEDUPE_H` (24 h); the tracker
  merges the same trade sent by two engines into one record (`sniper+z-setup`) and never runs two checks at once.
- `FLASH_COPY_OWNER` (true): every flash card is also sent as a downloadable PNG file to your private chat with the
  bot (`OWNER_CHAT_ID`) — the channel can keep "restrict saving content" on.
- Partials: when an open tracked trade is in profit and the 1h z-score reaches the opposite extreme
  (`PARTIAL_Z` 2.0 — a sell going oversold, a buy overbought) a "🎯 Close partials" card is sent once per trade.
- Candle-range limit plans and confirmed CRT gold trades now need a council GO like every other engine.

## WildChance bot = owner hub

Commands (menu re-registered at every deploy): /strength /rotation /walls EURUSD /calendar [USD,EUR] /results /recent
/edge /terminal /help — and in the owner's own chat (`OWNER_CHAT_ID`) also /open /health /audit. The old USD/JPY
forward-test commands now read the signal tracker (/summary /profit /wins → /results, /history /last5 → /recent).
The bot's /calendar and the EdgeFinder news check read the stored calendar (no extra Forex Factory calls → fewer 429s).

## Feeding the system through the bot (owner chat)

- **CME bulletin**: send the section PDFs (or one .zip of them) to your bot chat. Sections are recognised by name or
  page header, kept 3 days so pairs can arrive separately, and parsed as soon as a group is complete (gold 62+64,
  FX 27–36, AUD 38, EUR 39, Nasdaq 40, S&P 47+48, Dow 60). The bot replies with what was updated.
- **Calendar**: paste the Forex Factory week as text into the bot chat. High impact is decided by release name
  (the paste loses FF's colours); times are converted from `CAL_PASTE_UTC_OFFSET` (+3) to UTC.
- The data audit now separates expected blocks (CBOE, CME downloads, Polygon options, FF next-week) from real failing
  feeds and shows each failure's reason.

## Groups, topics and the deal watch

- `GROUP_ROUTES` (JSON): copies every main-channel post to groups / forum topics by market family, e.g.
  `{"gold":"-1001234567890:12","fx":"-1001234567890:15","crypto":"-1009876543210","indices":"-1001234567890:20",
  "dxy":"-1001234567890:22","general":"-1001234567890:3","results":"-1005555555555","all":""}` — one family → that
  route, mixed / none → `general`, flash cards + track-record reports → also `results`, `all` gets everything.
  Get ids with **/chatid** typed inside the group or topic (the bot must be a member / admin). Commands work in
  groups too; the bot never replies to ordinary group chat.
- Deal watch (owner chat): `/watch rtx 4090 1500`, `/watches`, `/unwatch 2`, `/deals`; checked every 4 h from
  public deal RSS feeds (`DEAL_FEEDS`, default Slickdeals front page); matches arrive in your bot chat.

## Hardware scout (private side business, owner bot chat)

eBay Browse API (official) → landed cost in Nairobi → margin vs your local selling prices → alerts every 6 h.
| Key | Default | Meaning |
|---|---|---|
| `EBAY_CLIENT_ID` / `EBAY_CLIENT_SECRET` | — | developer.ebay.com → Application keys (Production) |
| `EBAY_MARKETPLACE` | `EBAY_US` | e.g. `EBAY_GB`, `EBAY_DE` |
| `KES_PER_USD` | `129` | FX; `SCOUT_FX_BUFFER_PCT` 2 adds a cushion |
| `SCOUT_FREIGHT_USD` / `SCOUT_FREIGHT_SERVER_USD` | `40` / `150` | freight to Nairobi (servers are heavy) |
| `SCOUT_FORWARDER_USD` | `15` | forwarder handling per item |
| `SCOUT_DUTY_PCT` | `0` | import duty — CONFIRM per HS code with your clearing agent |
| `SCOUT_VAT_PCT` / `SCOUT_IDF_PCT` / `SCOUT_RDL_PCT` | `16` / `3.5` / `2` | import VAT, IDF, RDL — confirm current rates |
| `SCOUT_MIN_MARGIN_PCT` | `25` | alert threshold |
| `SCOUT_VAT_REGISTERED` | `false` | true → /ledger deducts output VAT from sales |
Bot: `/scoutwatch dell r740 600`, `/price r740 115000`, `/scout`, `/scoutlist`, `/scoutdel 2`, `/bought desc 98000`,
`/sold 3 145000`, `/stock`, `/ledger`.
