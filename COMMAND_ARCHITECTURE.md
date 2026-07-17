# Gold System — Command Architecture, Data Plan & Audit

Operating the gold edge as a theater command (the CENTCOM model): component
commands each own a domain, and a central operations cell (STRATOPS) sorts and
allocates opportunities toward a campaign objective in real time.

---

## 1. The command map (CENTCOM → system)

| Command | Doctrine role | System modules (built) | Job in the system |
|---------|---------------|------------------------|-------------------|
| **STRATOPS** (theater J3) | real-time opportunity sorting + allocation | **— to build —** | rank every candidate strike, allocate execution toward the objective |
| **NAVCENT** (5th Fleet — strategic control) | owns the theater picture | `gold/timeline` (HTF zones), `gold/macro_cycle` (regime), `cbdr` range | where we are, the campaign objective, the bias |
| **AFCENT** (air / ISR) | continuous surveillance | `gold_scan`, `gold_intraday`, `candlerange/crt`, `/gold/session-levels`, regime refresh | the eyes — scan every session for candidate setups |
| **ARCENT** (ground / logistics) | holds ground, moves supply | `gold_positions`, `trade_executor` (+MT5), `propfirm`, `scorecard` | hold positions, manage the risk budget, execute, score |
| **MARCENT** (rapid expeditionary) | fast strikes / crisis response | intraday & intrasession tiers, CRT 1-5-9, pre-London limits, news gate | quick tactical entries within the session |
| **SOCCENT** (unconventional) | hunts behind the lines | S&D fades, protraction, session-liquidity sweeps | take liquidity at the extremes / ranging weeks |

The commands already exist as code. The **missing piece is STRATOPS** — the cell
that turns AFCENT's surveillance into a ranked engagement list under ARCENT's
logistics constraints.

---

## 2. The campaign objective — CBDR range-to-range target engine

The strategic objective is not a single trade; it is the **next CBDR range**.

- The zone between one CBDR range and the next ≈ **2,500 pips / $250 in gold**.
- **Enter at a CBDR level** (buy/sell) → **target the next CBDR range**.
- Every lower-timeframe trade — weekly profile, intraday, intrasession — is a
  **tactical move toward that objective**, in the same direction, compounding into
  the $250 leg.

**Have:** CBDR levels + SD ladder (`cbdr`), HTF named zones (`timeline`), the tiers,
protraction & liquidity. **Missing:** an engine that (a) identifies the *current*
CBDR range and the *next* range objective + direction, and (b) frames every
tactical signal as "aligned toward the objective / against it / neutral."

---

## 3. STRATOPS — the opportunity sorter (the sharpening)

The one capability to build. A real-time cell that, on every scan:

1. **Objective** — from NAVCENT: current CBDR range → next CBDR range (the ~$250
   leg) + direction (HTF timeline zone + macro regime).
2. **Muster** — collect all live candidates from AFCENT/MARCENT/SOCCENT (each tier,
   each limit, each fade) with their gate results.
3. **Score** — rank each candidate by confluence toward the objective:
   `HTF align + regime + discount/premium location + protraction + liquidity draw +
   tier R:R + news-clear`. One number per candidate.
4. **Allocate** — under ARCENT's logistics: the exposure cap, the daily risk budget,
   one-per-side-per-tier. Pick the top-ranked, size them, stand the rest down.
5. **Engagement list** — output: *take these · hold these · stand down these*, with
   the objective and the reason each was ranked where it was.

This is the "sort, look, and take out opportunities in real time" you described.

---

## 4. Audit — how far the system has gone (12 commits, 199/199 tests)

| Command | Built | Coverage |
|---------|-------|----------|
| NAVCENT | HTF timeline (named daily zones), macro-cycle regime (DXY inverse + live FRED/CFTC), CBDR + SD ladder | **~90%** — missing the range-to-range objective |
| AFCENT | gold_scan, gold_intraday (tiered), CRT, session-levels, Friday gap, daily refresh crons | **~90%** — solid ISR |
| ARCENT | tracked positions (PENDING→fill→TP/SL/time-stop), scorecard by-tier, prop gate, MT5 execute | **~80%** — missing exposure cap + the VPS MT5 connector |
| MARCENT | intraday/intrasession/CRT/pre-London limits, news gate, pre-London→NY OTE | **~90%** |
| SOCCENT | S&D Monday-CBDR fade, protraction gate, 8h + 1am/7am/PDH/PDL/PWH liquidity | **~85%** |
| STRATOPS | — | **0%** — not started (the next phase) |

**Full gate stack live:** HTF timeline → macro/regime → weekly profile → session →
tier → location → protraction → news → prop. **Backtest:** swing tier measured
(`/gold/backtest`); intraday tiers pending H1 history.

---

## 5. Unfinished (carried from the roadmap)

1. **STRATOPS opportunity sorter** — the new ask (Section 3). *[highest]*
2. **CBDR range-to-range objective engine** — Section 2. *[highest, STRATOPS depends on it]*
3. **Gold exposure cap** (ARCENT logistics) — required before the sorter can allocate.
4. **Intraday-tier backtest** — needs H1/M15 history; tunes the sorter's weights.
5. **MT5 bridge connector** (the Windows VPS side) — orders enqueue but nothing
   places them until the connector runs.
6. `swept_both` mid-week S&D detection.
7. Live WGC ETF/CB flows (only FRED real-rate/Fed + CFTC COT are live).
8. HTF confluence as a hard gate (optional).

---

## 6. Data plan — phased, to begin once you verify the current build

**Phase 0 — Verify + connect feeds (you).**
Apply the 12-commit bundle; set keys: `TWELVEDATA_API_KEY` (price/OHLC — CBDR, CRT,
sessions), `FRED_KEY` (real rate/Fed/RBUSBIS), CFTC is keyless, `TELEGRAM_*`,
`DATABASE_URL`. Confirm `/gold/regime`, `/gold/session-levels`, `/gold/timeline`,
`/gold/backtest` return live data. **Exit:** endpoints green on real data.

**Phase 1 — CBDR range-to-range objective engine.**
*Data:* daily + intraday CBDR history (TwelveData). *Build:* identify the current
CBDR range + the next range objective ($250 leg) + direction; expose `/gold/objective`.
*Success:* the objective matches the chart's zones you already identified.

**Phase 2 — STRATOPS sorter + exposure cap.**
*Data:* the live signal set (all tiers/limits) + open positions (DB). *Build:* the
scoring + allocation cell (Section 3) and the ARCENT exposure cap. *Success:* a
ranked engagement list that never exceeds the risk budget.

**Phase 3 — Intraday-tier backtest → tune weights.**
*Data:* H1/M15 history (~1–2 yr). *Build:* extend `backtest/gold_tiers` to the
intraday/intrasession/CRT tiers. *Success:* per-tier expectancy → the sorter's
weights are fit to measured edge, not guessed.

**Phase 4 — Live paper-run + feedback loop.**
*Data:* live tracked outcomes (gold_positions → scorecard). *Build:* nothing new —
run STRATOPS in paper, let the scorecard's reflection factor sharpen the weights.
*Success:* GREEN verdict on a rolling window before going live.

---

**My read:** the CENTCOM framing is worth adopting as the *orchestration* layer —
it names the one gap that matters (STRATOPS) and gives the components clean
boundaries. Keep it a thin command cell over the engines we already have; don't
rebuild the components around the metaphor. The campaign objective (range-to-range)
is what makes the sorter meaningful — build that first, then the sorter, then let
the backtest and the live feedback tune it.
