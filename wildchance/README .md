# Wildchance Confluence Engine — Deployment

Three files work together:

- **wildchance_scraper.py** — pulls data on a 3-tier cadence, writes `feed.json`
- **wildchance_engine.html** — dashboard; reads `feed.json` if served, else shows demo snapshot
- **setup_schedule.sh** / **setup_schedule.ps1** — install the schedule (Linux/mac / Windows)

Put all of them in one folder (e.g. `~/wildchance`). `feed.json` is created there by the scraper.

---

## 1. Credentials (never hardcoded)

The scraper reads everything from environment variables. Nothing lives in the source.

| Variable | Used by | If unset |
|---|---|---|
| `TWELVEDATA_KEY` (or `TWELVEDATA_API_KEY`) | live prices | falls back to price snapshot |
| `MYFXBOOK_EMAIL` / `MYFXBOOK_PASSWORD` | retail sentiment | falls back to retail snapshot |
| `GOLD_COT_COHORT` | gold direction (`managed_money` default, or `commercial`) | `managed_money` |

```bash
export TWELVEDATA_KEY="your_key"
export MYFXBOOK_EMAIL="you@email.com"
export MYFXBOOK_PASSWORD="your_password"
```

> Regenerate any key/password you've ever pasted into a chat or shared screen.

---

## 2. Test it once, by hand

```bash
cd ~/wildchance
python3 wildchance_scraper.py --tier weekly   # COT (incl. gold) + retail + calendar + prices
```

A healthy live run prints lines like:

```
[COT] EUR: 26 weeks, latest 2026-05-26 net=7296
[COT] XAU (gold, managed_money): 26 weeks, latest 2026-05-26 net=...
[RETAIL] myfxbook: 7 pairs (7 live)
[CAL] faireconomy: 180 events (24 high-impact)
[PRICE] Twelve Data: 7 symbols (7 live)
```

Any source that fails says so and uses its snapshot — the feed always writes.

---

## 3. Schedule it

### Linux / macOS (cron)
Edit the CONFIG block at the top of `setup_schedule.sh` (path + credentials), then:

```bash
chmod +x setup_schedule.sh
./setup_schedule.sh test       # run all three tiers now
./setup_schedule.sh install    # add the cron jobs
./setup_schedule.sh show       # confirm
```

Installed cadence:

```
0 */6 * * *   --tier 6h       prices + recompute signals
30 6 * * *    --tier daily     retail + calendar
0 21 * * 5    --tier weekly    COT + gold COT   (Fri, after CFTC ~15:30 ET release)
```

Logs go to `engine.log` in the engine folder.

### Windows (Task Scheduler)
Edit the CONFIG block in `setup_schedule.ps1`, then in PowerShell:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\setup_schedule.ps1 install
```

Creates tasks `WildchanceEngine-6h`, `-daily`, `-weekly`. Credentials are stored with `setx` as user env vars (open a new terminal after install).

---

## 4. View the dashboard live

`fetch()` can't read `file://`, so serve the folder:

```bash
cd ~/wildchance
python3 -m http.server 8000
```

Open `http://localhost:8000/wildchance_engine.html`. The badge shows **LIVE** with a timestamp when it reads `feed.json`, **SNAPSHOT** otherwise. It re-pulls every 6h.

---

## Data sources

| Layer | Source | Endpoint | Auth |
|---|---|---|---|
| COT (FX) | CFTC Traders in Financial Futures | `publicreporting.cftc.gov/resource/gpe5-46if.json` | none |
| COT (gold) | CFTC Disaggregated (COMEX) | `publicreporting.cftc.gov/resource/72hh-3qpy.json` | none |
| Retail | myfxbook Community Outlook API | `myfxbook.com/api/get-community-outlook.json` | login |
| Calendar | ForexFactory (faireconomy mirror) | `nfs.faireconomy.media/ff_calendar_thisweek.json` | none |
| Prices | Twelve Data | `api.twelvedata.com/price` | key |

The myfxbook **widgets** embedded in the dashboard (news / calendar / outlook) are display-only — the engine reads the **API**, not the widgets, because JavaScript can't read cross-domain iframe content.

---

## Gold COT cohort — a real choice

Gold has no Leveraged-Money cohort like FX; the COMEX report splits into **Managed Money** (speculators) and **Producer/Merchant** (commercial hedgers).

- `GOLD_COT_COHORT=managed_money` (default): gold is treated like FX — confirm signals against speculative funds. Internally consistent with the other pairs.
- `GOLD_COT_COHORT=commercial`: confirm against hedgers, i.e. *fade* the Managed Money crowd. Many gold COT traders prefer this.

Example with retail 73% long gold: `managed_money` → WATCH (spec funds also long, no edge); `commercial` → SHORT (commercials short, confirms fading retail). Pick the one that matches your method.

---

## Calendar / NFP surprise scoring

`fetch_calendar()` pulls the ForexFactory week as JSON and normalizes each event to `{date, ccy, event, actual, forecast, previous, impact}` with numeric values. `score_nfp()` finds the latest NFP with both actual and forecast and computes the surprise (actual − forecast), the USD direction, and the beat %. That `nfp` block flows into `feed.json` and the dashboard's NFP playbook. No HTML scraping — it's a clean JSON feed.
