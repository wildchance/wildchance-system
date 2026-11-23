"""Import long price history (TradingView / MT5 / broker CSV exports, zipped or not)
into the yearly-CBDR tables — the owner's 50–100-year data loop.

    python -m wildchance.history_import EURUSD_1D.zip [more files …] [--symbol EUR/USD]
                                        [--source oanda] [--force]

For every year in the file it writes:
  data/yearly_boxes.csv   Q1 body box (Jan → Mar), source = file feed. Rows whose source
                          is 'manual' are kept unless --force.
  data/yearly_stats.csv   how far that year ran after its box closed (Apr → Dec), in box
                          units — the base rate the engine compares the live year to.

Formats detected by header: TradingView (time,open,high,low,close — unix or ISO time),
MT5 (<DATE> <TIME> <OPEN> …, tab or comma), generic Date/Open/High/Low/Close.
Intraday files are rolled up to daily. Weekly/monthly files give an approximate
box (source suffixed '~W' / '~M').
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import math
import os
import re
import zipfile
from collections import OrderedDict
from typing import Dict, Iterable, List, Optional, Tuple

Bar = Tuple[dt.date, float, float, float, float]
BOXES = os.environ.get("YEARLY_BOXES_CSV", "data/yearly_boxes.csv")
STATS = os.environ.get("YEARLY_STATS_CSV", "data/yearly_stats.csv")
_DATE_FMTS = ("%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d", "%m/%d/%Y", "%d.%m.%Y", "%Y%m%d")


def _norm_sym(s: str) -> str:
    s = s.upper().replace("/", "")
    return f"{s[:3]}/{s[3:6]}" if len(s) == 6 and s.isalpha() else s


def guess_symbol(name: str) -> Optional[str]:
    base = os.path.basename(name).upper()
    base = re.sub(r"^(OANDA|FX|FOREXCOM|FXCM|PEPPERSTONE|ICMARKETS|TVC|CAPITALCOM|FX_IDC|SAXO)[_:]",
                  "", base)
    m = re.match(r"([A-Z0-9]{3,10})", base)
    return _norm_sym(m.group(1)) if m else None


def _parse_date(v: str) -> Optional[dt.datetime]:
    v = v.strip().strip('"')
    if not v:
        return None
    if re.fullmatch(r"-?\d{9,11}(\.\d+)?", v):                    # unix seconds
        return dt.datetime.fromtimestamp(float(v), dt.timezone.utc).replace(tzinfo=None)
    if re.fullmatch(r"\d{12,14}", v):                             # unix ms
        return dt.datetime.fromtimestamp(int(v) / 1000, dt.timezone.utc).replace(tzinfo=None)
    head = re.split(r"[T ]", v)[0]
    for f in _DATE_FMTS:
        try:
            return dt.datetime.strptime(head, f)
        except ValueError:
            continue
    return None


def parse(text: str) -> List[Bar]:
    """Daily bars (oldest-first) from one CSV/TSV export."""
    sample = text[:4096]
    delim = "\t" if sample.count("\t") > sample.count(",") else ";" if sample.count(";") > sample.count(",") else ","
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    if not rows:
        return []
    head = [re.sub(r"[<>\s\"]", "", h).lower() for h in rows[0]]
    has_header = any(h in ("open", "close", "time", "date") for h in head)
    if not has_header:                                           # headerless MT/HistData style
        head = ["date", "time", "open", "high", "low", "close"][: len(rows[0])] \
            if len(rows[0]) >= 6 and not re.match(r"^\d+(\.\d+)?$", rows[0][1] or "x") \
            else ["date", "open", "high", "low", "close"]
    body = rows[1:] if has_header else rows
    idx = {k: head.index(k) for k in ("open", "high", "low", "close") if k in head}
    tcol = head.index("time") if "time" in head else None
    dcol = head.index("date") if "date" in head else tcol
    if len(idx) < 4 or dcol is None:
        raise ValueError(f"unrecognised header: {rows[0]}")
    daily: "OrderedDict[dt.date, List[float]]" = OrderedDict()
    for r in body:
        try:
            when = _parse_date(r[dcol])
            o, h, l, c = (float(r[idx[k]]) for k in ("open", "high", "low", "close"))
        except (IndexError, ValueError):
            continue
        if when is None or not all(math.isfinite(x) and x > 0 for x in (o, h, l, c)):
            continue
        d = when.date()
        cur = daily.get(d)
        if cur is None:
            daily[d] = [o, h, l, c, when, when]
        else:                                                    # intraday → daily roll-up
            cur[1], cur[2] = max(cur[1], h), min(cur[2], l)
            if when < cur[4]:
                cur[0], cur[4] = o, when
            if when >= cur[5]:
                cur[3], cur[5] = c, when
    out = sorted(((d, v[0], v[1], v[2], v[3]) for d, v in daily.items()), key=lambda b: b[0])
    return out


def spacing(bars: List[Bar]) -> str:
    if len(bars) < 3:
        return "D"
    gaps = sorted((b[0] - a[0]).days for a, b in zip(bars, bars[1:]))
    med = gaps[len(gaps) // 2]
    return "D" if med <= 3 else "W" if med <= 10 else "M"


def yearly_rows(bars: List[Bar]) -> List[Dict]:
    """Q1 body box + Apr–Dec excursion (box units) for every year with Q1 data."""
    sp = spacing(bars)
    need = {"D": 40, "W": 10, "M": 3}[sp]
    by_year: Dict[int, List[Bar]] = {}
    for b in bars:
        by_year.setdefault(b[0].year, []).append(b)
    out = []
    for y, bs in sorted(by_year.items()):
        q1 = [b for b in bs if b[0].month <= 3]
        if len(q1) < need:
            continue
        lo = min(min(b[1], b[4]) for b in q1)
        hi = max(max(b[1], b[4]) for b in q1)
        if hi <= lo:
            continue
        rng = hi - lo
        rest = [b for b in bs if b[0].month > 3]
        complete = bool(rest) and rest[-1][0].month == 12 and rest[-1][0].day >= 20
        row = {"year": y, "low": lo, "high": hi, "approx": "" if sp == "D" else f"~{sp}",
               "peak_up": None, "peak_down": None, "close_pos": None, "complete": complete}
        if rest:
            row["peak_up"] = round((max(b[2] for b in rest) - lo) / rng, 3)
            row["peak_down"] = round((min(b[3] for b in rest) - lo) / rng, 3)
            row["close_pos"] = round((rest[-1][4] - lo) / rng, 3)
        out.append(row)
    return out


def _read(path: str) -> List[Dict]:
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def _write(path: str, fields: List[str], rows: Iterable[Dict]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _fmt(v: float) -> str:
    return f"{v:.6g}" if v >= 100 else f"{v:.5f}"


def merge(symbol: str, rows: List[Dict], source: str, force: bool = False,
          boxes_path: str = BOXES, stats_path: str = STATS) -> Dict:
    key = lambda r: (r["symbol"].upper().replace("/", ""), int(r["year"]))   # noqa: E731
    sym_key = symbol.upper().replace("/", "")
    boxes = OrderedDict((key(r), r) for r in _read(boxes_path) if r.get("symbol"))
    wrote, kept = 0, 0
    for r in rows:
        k = (sym_key, r["year"])
        old = boxes.get(k)
        if old and old.get("low") and (old.get("source") or "").startswith("manual") and not force:
            kept += 1
            continue
        boxes[k] = {"symbol": symbol, "year": r["year"], "low": _fmt(r["low"]), "high": _fmt(r["high"]),
                    "source": source + r["approx"]}
        wrote += 1
    _write(boxes_path, ["symbol", "year", "low", "high", "source"],
           sorted(boxes.values(), key=lambda r: (r["symbol"], int(r["year"]))))
    stats = OrderedDict((key(r), r) for r in _read(stats_path) if r.get("symbol"))
    for r in rows:
        stats[(sym_key, r["year"])] = {"symbol": symbol, "year": r["year"], "peak_up": r["peak_up"],
                                       "peak_down": r["peak_down"], "close_pos": r["close_pos"],
                                       "complete": int(r["complete"]), "source": source + r["approx"]}
    _write(stats_path, ["symbol", "year", "peak_up", "peak_down", "close_pos", "complete", "source"],
           sorted(stats.values(), key=lambda r: (r["symbol"], int(r["year"]))))
    years = [r["year"] for r in rows]
    return {"symbol": symbol, "years": f"{min(years)}–{max(years)}" if years else None,
            "boxes_written": wrote, "manual_kept": kept, "stats_rows": len(rows)}


def _texts(path: str) -> Iterable[Tuple[str, str]]:
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                n = info.filename
                if info.is_dir() or not n.lower().endswith((".csv", ".txt", ".tsv")) or \
                        os.path.basename(n).startswith((".", "__")):
                    continue
                if info.file_size > 200 * 1024 * 1024:
                    raise ValueError(f"{n}: over 200 MB uncompressed — refusing")
                yield n, z.read(info).decode("utf-8-sig", errors="replace")
    else:
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            yield path, f.read()


def import_files(paths: List[str], symbol: Optional[str] = None, source: str = "file",
                 force: bool = False, boxes_path: str = BOXES, stats_path: str = STATS) -> List[Dict]:
    report = []
    for p in paths:
        for name, text in _texts(p):
            sym = _norm_sym(symbol) if symbol else guess_symbol(name)
            try:
                bars = parse(text)
            except ValueError as e:
                report.append({"file": name, "error": str(e)})
                continue
            if not sym or not bars:
                report.append({"file": name, "error": "no symbol / no bars"})
                continue
            rows = yearly_rows(bars)
            res = merge(sym, rows, source, force, boxes_path, stats_path)
            res.update(file=name, bars=len(bars), first=bars[0][0].isoformat(),
                       last=bars[-1][0].isoformat(), spacing=spacing(bars))
            report.append(res)
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--symbol")
    ap.add_argument("--source", default="file")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    for r in import_files(a.paths, a.symbol, a.source, a.force):
        print(r)
