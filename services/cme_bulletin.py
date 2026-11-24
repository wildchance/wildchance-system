"""CME Daily Bulletin → real futures-options open interest by strike.

The bulletin (free, published every business day at cmegroup.com/market-data/
daily-bulletin) lists every option strike with open interest. This reads it for
COMEX gold (OG monthly + OG1–OG4 weekly Friday calls/puts, section 64) and the gold
futures settlements (section 62), and turns it into the same per-strike chain the
dealer-levels engine uses — walls, gamma-free max pain, put/call OI ratio.

Strikes are moved onto the FRONT gold future (K × F_front / F_underlying) so expiries
on different futures (Dec vs Feb) stack correctly; options_levels then maps the
front future onto live spot by ratio, like every other source.

Text comes from `pdftotext -layout` (poppler-utils, installed in the Dockerfile).
Sources: CME's public bulletin URLs (daily job) or PDFs dropped into data/cme/.
"""

from __future__ import annotations

import datetime as dt
import glob
import os
import re
import shutil
import subprocess
import tempfile
from typing import Dict, List, Optional, Tuple

from decouple import config

CME_DIR = str(config("CME_DIR", default="data/cme"))
BASE = "https://www.cmegroup.com/daily_bulletin/current/"
SECTIONS = {"metals_options": "Section64_Metals_Option_Products.pdf",
            "metals_futures": "Section62_Metals_Futures_Products.pdf"}
GOLD_PRODUCTS = ("OG", "OG1", "OG2", "OG3", "OG4")
MONTHS = {m: i + 1 for i, m in enumerate(("JAN", "FEB", "MAR", "APR", "MAY", "JUN",
                                          "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"))}
GOLD_FUT_MONTHS = (2, 4, 6, 8, 10, 12)
# strikes further than this from the future are ignored: the bulletin carries large
# 'lottery' OI at e.g. 10,000–20,000 calls priced $0.10 that would fake a call wall
BAND_PCT = float(config("CME_BAND_PCT", default="12"))

_MONTH_LINE = re.compile(r"^\s*([A-Z]{3}\d{2})\s*$")
_PRODUCT = re.compile(r"^([A-Z0-9]+)\s+(CALL|PUT)\s{2,}\S")
_ROW = re.compile(r"^\s*(\d{2,6}(?:\.\d+)?)\s+\S")
_OI = re.compile(r"(?<![\d.,])(\d[\d,]*)\s+(?:UNCH|NEW|[+-]\s*\d[\d,]*)")
_DATE = re.compile(r"(Mon|Tue|Wed|Thu|Fri|Sat|Sun), ([A-Z][a-z]{2}) (\d{1,2}), (\d{4})")


def pdf_text(path: str) -> str:
    exe = shutil.which("pdftotext")
    if not exe:
        raise RuntimeError("pdftotext not installed (poppler-utils)")
    out = subprocess.run([exe, "-layout", path, "-"], capture_output=True, timeout=120)
    return out.stdout.decode("utf-8", "ignore")


def bulletin_date(text: str) -> Optional[dt.date]:
    m = _DATE.search(text)
    if not m:
        return None
    return dt.date(int(m.group(4)), MONTHS[m.group(2).upper()], int(m.group(3)))


def _label_date(label: str, mmdd: str) -> dt.date:
    mon, yr = MONTHS[label[:3]], 2000 + int(label[3:])
    m, d = (int(x) for x in mmdd.split("/"))
    return dt.date(yr - 1 if m > mon else yr, m, d)


def expiry_table(text: str) -> Dict[Tuple[str, str, str], dt.date]:
    """'LAST TRADE DATES' grid → {(product, CALL|PUT, month label): last trade date}."""
    out: Dict[Tuple[str, str, str], dt.date] = {}
    cols: List[Tuple[int, str]] = []
    for line in text.splitlines():
        if "EXPIRATION:" in line:
            cols = [((m.start() + m.end()) // 2, m.group()) for m in re.finditer(r"[A-Z]{3}\d{2}", line)]
            continue
        if not cols:
            continue
        if "OPTIONS ON FUTURES" in line:
            break
        m = re.match(r"^\s*([A-Z0-9]+)\s+(CALL|PUT)\b", line)
        if not m:
            continue
        for v in re.finditer(r"\d{2}/\d{2}", line):
            c = (v.start() + v.end()) // 2
            label = min(cols, key=lambda x: abs(x[0] - c))[1]
            out[(m.group(1), m.group(2), label)] = _label_date(label, v.group())
    return out


def parse_options(text: str, products=GOLD_PRODUCTS) -> List[Dict]:
    """Option rows (product, CALL/PUT, month, strike, open interest) for ``products``."""
    rows, prod, typ, month = [], None, None, None
    for line in text.splitlines():
        p = _PRODUCT.match(line)
        if p:
            prod, typ, month = p.group(1), p.group(2), None
            continue
        if line.startswith(("TOTAL", "THE INFORMATION", "IS ACCEPTED")):
            continue
        mm = _MONTH_LINE.match(line)
        if mm:
            month = mm.group(1)
            continue
        if prod not in products or not month:
            continue
        r = _ROW.match(line)
        if not r:
            continue
        ois = _OI.findall(line)
        if not ois:
            continue
        rows.append({"product": prod, "type": typ, "month": month, "strike": float(r.group(1)),
                     "oi": int(ois[-1].replace(",", ""))})
    return rows


def parse_futures(text: str, product: str = "GC") -> Dict[str, float]:
    """Settlement per contract month for a futures product (section 62 layout)."""
    out: Dict[str, float] = {}
    inside = False
    for line in text.splitlines():
        if re.match(rf"^{product} FUT\s", line):
            inside = True
            continue
        if inside and re.match(r"^[A-Z0-9]+ FUT\s|^TOTAL", line):
            if line.startswith("TOTAL") or not line.startswith(f"{product} FUT"):
                inside = line.startswith(f"{product} FUT")
            continue
        if inside:
            m = re.match(r"^([A-Z]{3}\d{2})\s+(.*)$", line)
            if m:
                # settlement = the number right before the point change (±n / UNCH)
                s = re.search(r"(\d[\d,]*\.\d+)\s+(?:[+-]\s*[\d.]+|UNCH|NEW)", m.group(2))
                if s:
                    out[m.group(1)] = float(s.group(1).replace(",", ""))
    return out


def gold_underlying(expiry: dt.date, futures: Dict[str, float]) -> Optional[float]:
    """Gold options settle into the next even-month future after the expiry month."""
    y, mth = expiry.year, expiry.month
    for _ in range(14):
        mth += 1
        if mth > 12:
            mth, y = 1, y + 1
        if mth in GOLD_FUT_MONTHS:
            lab = f"{[k for k, v in MONTHS.items() if v == mth][0]}{y % 100:02d}"
            if lab in futures:
                return futures[lab]
    return None


def build_chain(opt_rows: List[Dict], expiries: Dict, futures: Dict[str, float], asof: dt.date,
                max_days: int = 45, band_pct: float = BAND_PCT) -> Dict:
    """Per-strike OI (front-future terms) for expiries within max_days of ``asof``."""
    if not futures:
        return {}
    front_lab = min(futures, key=lambda l: (2000 + int(l[3:]), MONTHS[l[:3]]))
    live = [(l, v) for l, v in futures.items()
            if (dt.date(2000 + int(l[3:]), MONTHS[l[:3]], 1) >= asof.replace(day=1))]
    front = sorted(live, key=lambda x: (int(x[0][3:]), MONTHS[x[0][:3]]))[0][1] if live else futures[front_lab]
    strikes: Dict[float, Dict] = {}
    used, calls, puts = set(), 0, 0
    for r in opt_rows:
        exp = expiries.get((r["product"], r["type"], r["month"]))
        if not exp or not 0 <= (exp - asof).days <= max_days or not r["oi"]:
            continue
        f_u = gold_underlying(exp, futures) or front
        k = round(r["strike"] * front / f_u, 1)
        if abs(k / front - 1) * 100 > band_pct:
            continue
        row = strikes.setdefault(k, {"call_oi": 0.0, "put_oi": 0.0, "gex": 0.0})
        row["call_oi" if r["type"] == "CALL" else "put_oi"] += r["oi"]
        calls += r["oi"] if r["type"] == "CALL" else 0
        puts += r["oi"] if r["type"] == "PUT" else 0
        used.add(f"{r['product']} {r['month']} ({exp:%d %b})")
    return {"spot": front, "strikes": strikes, "atm_iv": None, "days": None, "asof": asof.isoformat(),
            "expiries": sorted(used), "call_oi": calls, "put_oi": puts,
            "put_call_ratio": round(puts / calls, 2) if calls else None}


def gold_from_texts(opt_text: str, fut_text: str, max_days: int = 45) -> Dict:
    asof = bulletin_date(opt_text) or dt.date.today()
    return build_chain(parse_options(opt_text), expiry_table(opt_text), parse_futures(fut_text, "GC"),
                       asof, max_days)


# ── runtime: fetch or read local PDFs, keep the latest chain ─────────────────────
async def refresh(max_days: int = 45) -> Dict:
    """Try CME's public bulletin URLs; fall back to the newest PDFs in data/cme/."""
    from services import data_store
    texts: Dict[str, str] = {}
    errors = []
    for key, name in SECTIONS.items():
        raw = None
        local = sorted(glob.glob(os.path.join(CME_DIR, f"*{name.split('_')[0]}_*.pdf")), key=os.path.getmtime)
        try:
            if raw is None:
                raw = await _fetch_pdf(BASE + name)
            if raw:
                with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
                    f.write(raw)
                    f.flush()
                    texts[key] = pdf_text(f.name)
            elif local:
                texts[key] = pdf_text(local[-1])
        except Exception as e:
            errors.append(f"{key}: {type(e).__name__}: {str(e)[:80]}")
    if "metals_options" not in texts or "metals_futures" not in texts:
        return {"ok": False, "reason": "no bulletin (download blocked and no PDFs in data/cme/)", "errors": errors}
    chain = gold_from_texts(texts["metals_options"], texts["metals_futures"], max_days)
    if chain.get("strikes"):
        await data_store.put("cme_gold_chain", {**chain, "strikes": {str(k): v for k, v in chain["strikes"].items()}})
    return {"ok": bool(chain.get("strikes")), "asof": chain.get("asof"), "front_future": chain.get("spot"),
            "strikes": len(chain.get("strikes") or {}), "expiries": chain.get("expiries"),
            "put_call_ratio": chain.get("put_call_ratio"), "errors": errors}


async def _fetch_pdf(url: str) -> Optional[bytes]:
    """Plain request for the public file; a refusal is respected (no work-arounds)."""
    import time as _t
    import httpx
    from services.data_feeds import record
    t0 = _t.time()
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as c:
            r = await c.get(url)
        ok = r.status_code == 200 and r.content[:4] == b"%PDF"
        record("cme_bulletin", ok, (_t.time() - t0) * 1000, "" if ok else f"HTTP {r.status_code}")
        return r.content if ok else None
    except Exception as e:
        record("cme_bulletin", False, err=type(e).__name__)
        return None


SEED = "data/cme_gold_seed.json"


async def cached_chain(max_age_days: int = 5) -> Optional[Dict]:
    """Latest parsed gold chain: Neon (daily refresh) → the committed seed file."""
    import json
    from services import data_store
    try:
        c, _ = await data_store.get("cme_gold_chain")
    except Exception:
        c = None
    if not c and os.path.exists(SEED):
        with open(SEED) as f:
            c = json.load(f)
    if not c or not c.get("asof"):
        return None
    if (dt.date.today() - dt.date.fromisoformat(c["asof"])).days > max_age_days:
        return None
    return {**c, "strikes": {float(k): v for k, v in c["strikes"].items()}}
