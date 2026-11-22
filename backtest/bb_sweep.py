"""Multi-core parameter sweep for the Bollinger fade (+ lunar check) across symbols.

Built for a rented compute box / GPU node: the work is CPU-parallel (one process per
symbol), so more cores = faster; a GPU adds nothing at this size. Data comes from
your app's broker feed (what the EA pushed), so results match your broker's prices.

  git clone <repo> && cd wildchance-system
  python -m backtest.bb_sweep --app https://wildchance-system.onrender.com \\
         --token $BRIDGE_TOKEN --symbols XAUUSD,XAGUSD,BTCUSD,USOIL,US500 --workers 8

  # or offline from CSV files named <SYMBOL>.csv with date,open,high,low,close:
  python -m backtest.bb_sweep --csv-dir ./data --symbols XAUUSD,EURUSD

Writes sweep_results.json and prints each symbol's best out-of-sample config.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import urllib.parse
import urllib.request
from concurrent.futures import ProcessPoolExecutor

from backtest.bb_engine import sweep


def load_app(app: str, token: str, symbol: str, tf: str, n: int):
    q = urllib.parse.urlencode({"symbol": symbol, "timeframe": tf, "n": n, "token": token})
    req = urllib.request.Request(f"{app.rstrip('/')}/bridge/export?{q}",
                                 headers={"User-Agent": "wildchance-sweep"})
    with urllib.request.urlopen(req, timeout=60) as r:
        rows = json.load(r)["bars"]
    return [(b["t"], b["o"], b["h"], b["l"], b["c"]) for b in rows]


def load_csv(path: str):
    with open(path) as fh:
        return [(r["date"], float(r["open"]), float(r["high"]), float(r["low"]),
                 float(r["close"])) for r in csv.DictReader(fh)]


def _job(args):
    symbol, bars, lunar = args
    out = {"symbol": symbol, "bars": len(bars)}
    if len(bars) < 200:
        out["error"] = "need ≥200 bars (let the EA push longer D1 history: InpInitialBars)"
        return out
    out["bollinger"] = sweep(bars)
    if lunar:
        import datetime as dt
        from lunar.engine import phase_returns
        dated = [(dt.datetime.utcfromtimestamp(b[0]).date() if isinstance(b[0], (int, float))
                  else dt.date.fromisoformat(str(b[0])[:10]),) + tuple(b[1:]) for b in bars]
        out["lunar"] = {h: phase_returns(dated, h) for h in (7, 10, 14)}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--app"); ap.add_argument("--token"); ap.add_argument("--csv-dir")
    ap.add_argument("--symbols", required=True)
    ap.add_argument("--tf", default="D1"); ap.add_argument("--n", type=int, default=3000)
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 2)
    ap.add_argument("--no-lunar", action="store_true")
    ap.add_argument("--out", default="sweep_results.json")
    a = ap.parse_args()
    jobs = []
    for s in [x.strip().upper() for x in a.symbols.split(",") if x.strip()]:
        bars = (load_csv(os.path.join(a.csv_dir, f"{s}.csv")) if a.csv_dir
                else load_app(a.app, a.token, s, a.tf, a.n))
        jobs.append((s, bars, not a.no_lunar))
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        results = list(ex.map(_job, jobs))
    with open(a.out, "w") as fh:
        json.dump(results, fh, indent=1, default=str)
    for r in results:
        b = (r.get("bollinger") or {}).get("best")
        if not b:
            print(f"{r['symbol']:8s} {r.get('error') or 'no robust config'}")
            continue
        o = b["out_of_sample"]
        print(f"{r['symbol']:8s} {b['params']}  OOS n={o['n']} win={o['win_rate']} "
              f"exp={o['expectancy_r']}R pf={o['profit_factor']} dd={o['max_dd_r']}R")
    print(f"→ {a.out}")


if __name__ == "__main__":
    main()
