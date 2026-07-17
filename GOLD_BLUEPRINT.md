# Gold Edge System — Blueprint & Audit

The operating manual for the XAU/USD system built across this branch. It is both
the **trade blueprint** (how the pieces combine into a decision) and the **gap
audit** (what is still left out). Every layer names its module + endpoint so this
doubles as the wiring map.

---

## 1. The edge in one line

> Trade **with** the higher-timeframe regime, **in** the right session, **on** the
> weekly profile, entering only at **discount/premium locations** (OTE / −SD), and
> **hold to the tier's target** — then let the scorecard tell you which tier earns.

Nothing fires unless the whole stack agrees. The Sunday-open winner that started
this was the only trade taken at a *reference point*; the system now forces every
trade to be taken at one.

---

## 2. The decision flow (top → bottom)

```
HTF REGIME            gold/macro_cycle.regime_read()      GET /gold/regime
  dollar (DXY inverse) + FRED real-rate/Fed + WGC CB demand + CFTC COT
  → accumulation | distribution + a gold bias
        │  (must not oppose the trade; price above the $3,900 invalidation)
        ▼
WEEKLY PROFILE        gold/ict.classify_week()            GET /gold/profile
  1 of 12 ICT profiles → bias (long/short/neutral) + the week's high/low
        │
        ▼
SESSION               gold/quarterly_session              (clock)
  Asia 00–08 accumulation (ENTRY) · London 08–13 manipulation (SETUP, no entry)
  · New York 13–21 distribution (ENTRY)
        │
        ▼
TRADE-TYPE TIER       gold/trade_types.classify_tier()
  reversal profile              → SWING        SL weekly hi/lo   1:5–1:8  → Mon close/Tue open
  continuation in NY (Q3)       → INTRADAY     SL day hi/lo      1:2–1:3  → NY close
  continuation in Asia (Q1)     → INTRASESSION SL session hi/lo  1:3–1:5  → session end
  continuation in London (Q2)   → stand aside (pre-London limits cover it)
  Seek & Destroy (ranging)      → extreme fades, HTF side only (see §3e)
        │
        ▼
ENTRY GATES           gold/intraday.assemble_intraday()   POST /gold/intraday
  macro anchor  → bias must agree with macro, price > invalidation
  location      → long only in discount, short only in premium (no chasing)
  regime        → dollar (DXY inverse) + COT not opposing
  FLD trigger   → Hurst FLD confirms the direction
  prop gate     → propfirm risk budget allows
        │
        ▼
TRACK & MANAGE        services/gold_positions             POST /gold/monitor
  open a GoldPosition → BE after TP1 → close on TP/SL/time-stop (tier horizon)
        │
        ▼
FEEDBACK              services/scorecard_service.gold_report   GET /gold/scorecard
  realized R by tier → reflection verdict (GREEN/AMBER/RED) + confidence factor
```

---

## 3. The trade-type playbook

### a. Swing (reversal weeks)
Reversal profiles (Classic Tue/Wed low-high, Thu reversals, Wed weekly reversals).
Direction from the weekly bias; **enter the discount/OTE pullback**; SL beyond the
week's extreme; scale-out **5R/6R/7R/8R**; hold to **Monday close / Tuesday open**.

### b. Intraday (NY distribution)
Continuation profiles during **NY (Q3)**. SL beyond the day's range; targets
**2R/3R**; closes at the **NY close** time-stop.

### c. Intrasession (Asia accumulation)
Continuation profiles during **Asia (Q1)**. SL beyond the session range (tightest);
targets **3R/4R/5R**; closes at **session end**.

### d. 1-5-9 CRT — `candlerange/crt.py`, `POST /candlerange/crt`
The **1-o'clock** candle (01:00 / 13:00) body sets the range → **buy −1 dev / sell
+1 & +3 dev** limits → **confirm at 5-o'clock** (05:00 Asian / 17:00 NY) → **target
9-o'clock** (09:00 / 21:00). Crons 05:00 & 17:00 UTC.

### e. Pre-London limits — `cbdr/engine.prelondon_limits`, `POST /cbdr/limits`
At **02:45–03:00 ET** (window closes 06:45 UTC EDT / 07:45 UTC EST), off the
pre-London CBDR box: **buy −1SD, sell +1SD & +3SD**, with the **1–1.5SD grey zone**
flagged for reversals. Anticipates the NY entry once price retraces to OTE after the
London manipulation.

### f. Seek & Destroy fade — `gold/trade_types.seek_destroy_plan`, `GET /gold/sd-fade`
Ranging weeks: **extreme ±3SD limits outside the range**, projected from **Monday's
CBDR box** (the "20:00 range" that sets the week's trend), **only on the HTF-trend
side** (monthly/quarterly via `regime_read`). Fade the liquidity sweep back inside.

### g. Friday → Monday gap — `candlerange.friday_gap_read`, `POST /candlerange/friday`
The Friday pre-close 1h candle anticipates Monday's gap. Mid-week reads are tagged
**low-confidence** (choppy). Cron 20:55 UTC Friday.

---

## 4. The gate stack (what blocks a trade)

| Gate | Module | Blocks when |
|------|--------|-------------|
| Macro anchor | `gold/macro.py` | weekly bias ≠ macro bias, or price < $3,900 invalidation |
| Location | `gold/location.py` | long not in discount / short not in premium |
| Regime | `gold/macro_cycle.regime_gate` | dollar (DXY) opposes, or COT stretched against a long |
| FLD | `gold/hurst` | Hurst FLD not confirming the direction |
| Session/timing | `gold/quarterly_session` | not an entry session (London manip / rollover) |
| Prop risk | `propfirm/engine` | over the daily/tier risk budget |

Direction is set once by the profile + macro anchor, so the **mid-week flip** can't
whipsaw it. Location + regime are the two that turned the losers around.

---

## 5. Operational schedule (cron → endpoint)

| UTC | Purpose |
|-----|---------|
| `0 2 * * 1-5` | Asia intrasession gold scan |
| `0 5 / 0 17 * * 1-5` | CRT 1-5-9 confirmation (Asian / NY) |
| `0 7 (Mar–Oct) / 0 8 (Nov–Feb)` | Pre-London limits, 03:00 ET year-round |
| `0 13 / 30 13 * * 1-5` | NY distribution gold scan / NY-AM |
| `0 22 * * 0` | Sunday/weekly-open swing + Asian gold CBDR + scorecard |
| `0 * * * *` | Gold swing monitor (TP/SL/time-stop) |
| `55 20 * * 5` | Friday → Monday-gap read |
| `0 21 * * 5` | Live regime refresh (FRED + CFTC, post-COT release) |

---

## 6. AUDIT — what we left out (prioritised)

| # | Gap | Impact | Fix |
|---|-----|--------|-----|
| 1 | ~~Limit plans aren't tracked or executed.~~ **DONE** — pre-London, CRT and S&D limits are money-sized (`gold/limit_orders.size_limit`) and opened as **PENDING** `GoldPosition`s (`gold_positions.open_limit`) that fill on touch and are monitored to TP/SL/time-stop; the monitor now handles PENDING fill/cancel. | — | `POST /gold/prelondon`, `GET /gold/sd-fade?track`, `POST /candlerange/crt?track` |
| 2 | ~~CRT confirmation isn't sized/tracked.~~ **DONE** — a confirmed XAU/USD CRT is sized and opened as a tracked position. | — | `/candlerange/crt` |
| 3 | ~~Pre-London → NY OTE isn't one flow.~~ **DONE** — in the NY distribution the entry location is gated on the pre-London box (a long only fires in its discount = the London-manipulation OTE retrace); attached as `prelondon_ote`. | — | `services/gold_intraday` |
| 4 | ~~News guard not wired into gold.~~ **DONE** — same-day tier-1 (NFP/CPI/FOMC) **blocks** gold entries, within-window events flag; daily `GET /gold/news` refresh cron. | — | `gold_scan` / `gold_intraday` news gate |
| 5 | ~~Live dollar (RBUSBIS) collected but unused.~~ **DONE** — `refresh_inputs` stores the live RBUSBIS-implied gold bias; `regime_gate`/`dollar_gold_bias()` prefer it over the anticipated DXY structure. | — | `gold/macro_cycle` |
| — | **MT5 execution** for the tracked limits: **ENABLED** — `build_order` treats limit cards as LIMIT orders; pre-London/CRT crons run `execute=true` (orders queue for the MT5 bridge). | — | `trade_executor` + routes |
| 6 | **Static snapshots remain:** macro price levels ($4000/$3900/targets) and WGC ETF/CB tonnage are hand-set; only FRED real-rate/Fed + CFTC COT are live. | Med | wire WGC ETF flows; make levels config-driven |
| 7 | **`swept_both` unused** — S&D only detected on **Friday** (`ict.py:98` computes it, never uses it). | Med | flag S&D mid-week when both sides of the range are swept |
| 8 | ~~No backtest of the new tiers/gates.~~ **DONE (swing tier)** — `backtest/gold_tiers.py` replays classify_week → classify_tier → discount gate → tier_stop/RR and forward-simulates (TP/SL/BE/time-stop, mirroring `position.evaluate`), scored through `build_scorecard`. `GET /gold/backtest`. Intraday/intrasession/CRT tiers still need intraday history. | Med | extend to intraday tiers on H1 history |
| 9 | **Reflection doesn't feed sizing.** `confidence_factor` is computed but lot sizing doesn't use it (by design — frozen rules). | Low | optional: multiply base lot by the clamped factor |
| 10 | **No gold portfolio exposure cap** across tiers — swing + intraday + intrasession + limits can stack same-side risk. | Med | add an aggregate open-risk cap for XAU/USD |
| 11 | **DST shoulder weeks** ~1 week off at the Mar/Nov changeover (cron can't express "2nd Sunday"). | Low | accept, or add exact-date one-shot triggers |

---

## 7. Recommended build order from here

1. **Track + execute the limit plans** (gap 1) — unifies CBDR / CRT / S&D into the
   monitored, scored system. Biggest single lift in real edge.
2. **CRT confirmation → sized tracked trade** (gap 2).
3. **News guard on gold entries** (gap 4) — cheap, prevents the worst stop-outs.
4. **Backtest the tiers** (gap 8) — turn the blueprint into measured expectancy.
5. **Live RBUSBIS into the regime gate** (gap 5), then WGC flows (gap 6).
6. **`swept_both` mid-week S&D** (gap 7) and the **gold exposure cap** (gap 10).

Everything above the audit line is built, tested (106/106), and wired. The audit
line is the roadmap to close the loop from *signals* to a *measured, executed edge*.
