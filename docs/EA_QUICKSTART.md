# WildChance EA — 3-minute setup (any MT5 broker)

1. **Install** — download the EA from your members page (`WildChance_EA.ex5`) → MT5 → File → Open Data Folder →
   `MQL5/Experts` → paste it there.
   In MT5's **Navigator**, right-click **Expert Advisors → Refresh**.
2. **Allow the app** — Tools → Options → Expert Advisors → tick **Allow WebRequest for listed URL**
   → add `https://wildchance-system.onrender.com` → OK. Click **Algo Trading** in the toolbar (green).
3. **Attach** — drag **WildChance_PropEA_v4** from **Navigator** onto a chart (EURUSD H1 is a good start).
   Do **not** start it from MetaEditor's ▶ Debug button (that ignores your settings).
4. **License key** — Inputs tab → **License key** = the key from your members page (Copy button).
   The owner gets theirs from the bot: `/license`.
   Common tab → tick **Allow Algo Trading** → OK.

That's all. The defaults suit a normal account: 0.5 % risk per trade, stop for the day at −3 %, stop at −8 %,
no profit-target lock, sessions in GMT (any broker's server time is handled automatically).
For a prop challenge, pick its preset in **Account plan** instead.

## The panel tells you what's wrong
The **APP FEED** line shows the next step until everything works:

| It says | Do this |
|---|---|
| `SETUP: click Algo Trading in the toolbar` | turn the Algo Trading button green |
| `SETUP: EA properties > Common > tick Allow Algo Trading` | right-click chart → Expert Advisors → Properties |
| `SETUP: Tools > Options > Expert Advisors > allow …` | step 2 above |
| `SETUP: paste your License key` / `not recognised` | step 4 — copy the key again from the members page |
| `SETUP: license expired` | renew your plan |
| `READY - licensed (council gate on)` | done — the EA trades only when the council says GO |
| title starts `DEBUG RUN` | stop the MetaEditor debug run and do step 3 |

## For the owner: shipping the EA
Compile once (MetaEditor → F7), rename the `.ex5` to `WildChance_EA.ex5` and put it in `static/ea/` — members
download it from the "Install the EA" card. Sell the `.ex5` only: no compiling for buyers, source stays private.
