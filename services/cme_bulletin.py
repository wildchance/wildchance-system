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


# ── currency futures options (separate CALL and PUT sections per currency) ──────────
# section title word → (traded pair, inverse?)  inverse: futures are USD per foreign unit
# but the pair is quoted foreign per USD (USD/JPY = 1 / JPY future) → strikes invert and
# a yen CALL is a USD/JPY PUT.
FX = {"JAPANESE YEN": ("USDJPY", True), "SWISS FRANC": ("USDCHF", True),
      "CANADIAN DOLLAR": ("USDCAD", True), "BRITISH POUND": ("GBPUSD", False)}
FX_SECTIONS = {"GBP_C": "Section27_British_Pound_Call_Options.pdf", "GBP_P": "Section28_British_Pound_Put_Options.pdf",
               "CAD_C": "Section29_Canadian_Dollar_Call_Options.pdf", "CAD_P": "Section30_Canadian_Dollar_Put_Options.pdf",
               "JPY_C": "Section33_Japanese_Yen_Call_Options.pdf", "JPY_P": "Section34_Japanese_Yen_Put_Options.pdf",
               "CHF_C": "Section35_Swiss_Franc_Call_Options.pdf", "CHF_P": "Section36_Swiss_Franc_Put_Options.pdf"}
FX_MONTHLY = ("JPU", "GBU", "CAU", "CHU", "ADU", "EUU")
FX_BAND_PCT = float(config("CME_FX_BAND_PCT", default="6"))
_FX_MONTH = re.compile(r"^\s*([A-Z]{3}\d{2})?\s*(?:([A-Z0-9]{2,4}) OPT(?: - ([A-Z0-9]{2,4}))?)?\s*"
                       r"\(FUTURES SETT\.\s+([\d.]+)")
_FX_PRODUCT = re.compile(r"^\s*([A-Z0-9]{2,4}) OPT(?: - ([A-Z0-9]{2,4}))?\s*$")


def _fridays(year: int, month: int) -> List[dt.date]:
    d = dt.date(year, month, 1)
    return [d + dt.timedelta(days=i) for i in range(31)
            if (d + dt.timedelta(days=i)).month == month and (d + dt.timedelta(days=i)).weekday() == 4]


def fx_expiry(code: str, label: str) -> Optional[dt.date]:
    """CME FX options: monthly = 2nd Friday before the 3rd Wednesday; '2JY' = 2nd Friday."""
    y, m = 2000 + int(label[3:]), MONTHS[label[:3]]
    if code in FX_MONTHLY:
        first = dt.date(y, m, 1)
        wed3 = first + dt.timedelta(days=(2 - first.weekday()) % 7 + 14)
        return wed3 - dt.timedelta(days=12)           # Wed − 5 = Friday before, − 7 more = 2nd Friday before
    if re.fullmatch(r"[1-5][A-Z]{2}", code):
        fr = _fridays(y, m)
        n = int(code[0])
        return fr[n - 1] if n <= len(fr) else None
    return None


def fx_section_meta(text: str) -> Optional[Tuple[str, bool, str]]:
    head = "\n".join(text.splitlines()[:12]).upper()
    for word, (pair, inv) in FX.items():
        if word in head:
            typ = "CALL" if " CALL OPTIONS" in head else "PUT" if " PUT OPTIONS" in head else None
            return (pair, inv, typ) if typ else None
    return None


def parse_fx_options(text: str) -> List[Dict]:
    """Rows: code, month, futures settlement, strike, OI for one CALL or PUT section."""
    rows, code, month, fut = [], None, None, 0.0
    seen: set = set()
    nth: Dict[Tuple, int] = {}
    for line in text.splitlines():
        if "ADDITIONAL" in line and "**" in line:
            nth = {}                          # repeated listing starts: count strikes afresh
            continue
        mm = _FX_MONTH.match(line)
        if mm:
            month = mm.group(1) or month
            if mm.group(2):
                code = mm.group(3) or mm.group(2)
            fut = float(mm.group(4)) if mm.group(4).strip(".") else 0.0
            continue
        p = _FX_PRODUCT.match(line)
        if p:
            code, month = p.group(2) or p.group(1), None
            continue
        if line.startswith(("TOTAL", "THE INFORMATION", "IS ACCEPTED")) or not (code and month):
            continue
        r = _ROW.match(line)
        if not r:
            continue
        ois = _OI.findall(line)
        k0 = (code, month, float(r.group(1)))
        nth[k0] = nth.get(k0, -1) + 1        # same strike twice in a block (American + European) is real
        key = (*k0, nth[k0])
        if ois and key not in seen:          # …but the whole section repeats under "ADDITIONAL …" — skip that
            seen.add(key)
            rows.append({"code": code, "month": month, "fut": fut, "strike": float(r.group(1)),
                         "oi": int(ois[-1].replace(",", ""))})
    return rows


def _scale(k: float, f: float) -> float:
    return min((10.0 ** -e for e in range(0, 8)), key=lambda s: abs(k * s - f))


def fx_chain(call_text: str, put_text: str, max_days: int = 45, band_pct: float = FX_BAND_PCT) -> Dict:
    """Both sections of one currency → per-strike OI in PAIR terms on the front future."""
    meta = fx_section_meta(call_text) or fx_section_meta(put_text)
    if not meta:
        return {}
    pair, inv, _ = meta
    asof = bulletin_date(call_text) or bulletin_date(put_text) or dt.date.today()
    data = [(fx_section_meta(t) or (None, None, None))[2] for t in (call_text, put_text)]
    rows = [{**r, "type": typ} for t, typ in zip((call_text, put_text), data) if typ for r in parse_fx_options(t)]
    futs = {}
    for r in rows:
        if r["fut"]:
            futs.setdefault(r["month"], r["fut"])
    return _fx_assemble(pair, inv, rows, futs, asof, max_days, band_pct)


def _fx_assemble(pair: str, inv: bool, rows: List[Dict], futs: Dict[str, float], asof: dt.date,
                 max_days: int, band_pct: float, fut_of_row=None) -> Dict:
    """Typed rows (code, month, strike, oi, type) + futures by month → pair-terms chain."""
    if not futs:
        return {}
    order = sorted(futs, key=lambda l: (int(l[3:]), MONTHS[l[:3]]))

    def fut_for(label: str) -> float:
        if futs.get(label):
            return futs[label]
        key = (int(label[3:]), MONTHS[label[:3]])
        later = [l for l in order if (int(l[3:]), MONTHS[l[:3]]) >= key]
        return futs[later[0]] if later else futs[order[-1]]

    front_f = futs[order[0]]
    front = 1 / front_f if inv else front_f
    strikes: Dict[float, Dict] = {}
    used, calls, puts = set(), 0, 0
    for r in rows:
        exp = fx_expiry(r["code"], r["month"])
        if not exp or not 0 <= (exp - asof).days <= max_days or not r["oi"]:
            continue
        f_u = (fut_of_row(r, exp) if fut_of_row else None) or fut_for(r["month"])
        k_fut = r["strike"] * _scale(r["strike"], f_u)
        k_pair, u_pair = (1 / k_fut, 1 / f_u) if inv else (k_fut, f_u)
        typ = ({"CALL": "PUT", "PUT": "CALL"}[r["type"]]) if inv else r["type"]
        k = round(k_pair * front / u_pair, 5 if front < 20 else 3)
        if abs(k / front - 1) * 100 > band_pct:
            continue
        row = strikes.setdefault(k, {"call_oi": 0.0, "put_oi": 0.0, "gex": 0.0})
        row["call_oi" if typ == "CALL" else "put_oi"] += r["oi"]
        calls += r["oi"] if typ == "CALL" else 0
        puts += r["oi"] if typ == "PUT" else 0
        used.add(f"{r['code']} {r['month']} ({exp:%d %b})")
    return {"pair": pair, "spot": front, "strikes": strikes, "atm_iv": None, "days": None, "asof": asof.isoformat(),
            "expiries": sorted(used), "call_oi": calls, "put_oi": puts,
            "put_call_ratio": round(puts / calls, 2) if calls else None}


# ── Euro FX (section 39): calls and puts MIXED in one listing, no CALL/PUT label ───
EUR_SECTION = "Section39_Euro_FX_And_Cme$Index_Options.pdf"
_EUR_MONTH = re.compile(r"^\s*([A-Z]{3}\d{2})?\s+([A-Z0-9]{2,4}) OPT(?: - ([A-Z0-9]{2,4}))?\s*$")
_DELTA = re.compile(r"^(?:\.\d{3}|1\.000)$")


def parse_eur_futures(text: str) -> Dict[str, float]:
    """EURO FX futures settlements (the first futures table: 'DEC26 … 1.09135 ( .9163)')."""
    out: Dict[str, float] = {}
    for line in text.splitlines():
        if "ADDITIONAL EURO FX" in line and "FUTURES" in line:
            break
        m = re.match(r"^([A-Z]{3}\d{2})\s+.*?(\d\.\d{4,5})\s+\(\s*[\d.]+\)", line)
        if m:
            out.setdefault(m.group(1), float(m.group(2)))
    return out


def classify(strike: float, fut: float, delta: Optional[float]) -> str:
    """No CALL/PUT label: a call is in the money below the future (delta > .5), a put above.
    Without a delta (cabinet rows), the cheap out-of-the-money side is assumed."""
    below = strike < fut
    if delta is None:
        return "PUT" if below else "CALL"
    return "CALL" if (delta > 0.5) == below else "PUT"


def parse_eur_options(text: str) -> List[Dict]:
    rows, code, month = [], None, None
    seen: set = set()
    nth: Dict[Tuple, int] = {}
    for line in text.splitlines():
        if "ADDITIONAL" in line and "**" in line:
            nth = {}
            continue
        mm = _EUR_MONTH.match(line)
        if mm:
            new_code = mm.group(3) or mm.group(2)
            if mm.group(1):
                month = mm.group(1)
            elif new_code != code:            # a bare 'EUU OPT' after a page break keeps the month
                month = None
            code = new_code
            continue
        if line.startswith(("TOTAL", "THE INFORMATION", "IS ACCEPTED")) or not (code and month):
            continue
        r = _ROW.match(line)
        if not r:
            continue
        ois = _OI.findall(line)
        k0 = (code, month, float(r.group(1)))
        nth[k0] = nth.get(k0, -1) + 1
        key = (*k0, nth[k0])
        if not ois or key in seen:
            continue
        seen.add(key)
        d = next((float(t) for t in line.split() if _DELTA.match(t)), None)
        rows.append({"code": code, "month": month, "strike": float(r.group(1)), "delta": d,
                     "oi": int(ois[-1].replace(",", ""))})
    return rows


def eur_underlying(exp: dt.date, futs: Dict[str, float]) -> Optional[float]:
    """First futures month at/after the expiry (a mid-month expiry after the ~14th rolls on)."""
    def key(l):
        return (2000 + int(l[3:]), MONTHS[l[:3]])
    for l in sorted(futs, key=key):
        y, m = key(l)
        if (y, m) > (exp.year, exp.month) or ((y, m) == (exp.year, exp.month) and exp.day <= 14):
            return futs[l]
    return None


def eur_chain(text: str, max_days: int = 45, band_pct: float = FX_BAND_PCT) -> Dict:
    futs = parse_eur_futures(text)
    if not futs:
        return {}
    asof = bulletin_date(text) or dt.date.today()
    rows = []
    for r in parse_eur_options(text):
        exp = fx_expiry(r["code"], r["month"])
        if not exp:
            continue
        f = eur_underlying(exp, futs)
        if not f:
            continue
        k = r["strike"] * _scale(r["strike"], f)
        rows.append({**r, "fut": f, "type": classify(k, f, r["delta"])})
    return _fx_assemble("EURUSD", False, rows, futs, asof, max_days, band_pct,
                        fut_of_row=lambda r, exp: r["fut"])


FX_SEED = "data/cme_fx_seed.json"


async def refresh_fx(max_days: int = 45) -> Dict:
    from services import data_store
    out = {}
    for ccy in ("GBP", "CAD", "JPY", "CHF"):
        texts = []
        for side in ("C", "P"):
            name = FX_SECTIONS[f"{ccy}_{side}"]
            raw = await _fetch_pdf(BASE + name)
            local = sorted(glob.glob(os.path.join(CME_DIR, f"{name.split('_')[0]}_*.pdf")), key=os.path.getmtime)
            try:
                if raw:
                    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
                        f.write(raw)
                        f.flush()
                        texts.append(pdf_text(f.name))
                elif local:
                    texts.append(pdf_text(local[-1]))
            except Exception:
                pass
        if len(texts) == 2:
            ch = fx_chain(texts[0], texts[1], max_days)
            if ch.get("strikes"):
                await data_store.put(f"cme_fx_chain:{ch['pair']}",
                                     {**ch, "strikes": {str(k): v for k, v in ch["strikes"].items()}})
                out[ch["pair"]] = {"asof": ch["asof"], "strikes": len(ch["strikes"]),
                                   "put_call_ratio": ch["put_call_ratio"]}
        else:
            out[ccy] = "no bulletin (download refused and no PDFs in data/cme/)"
    try:                                       # Euro FX: one mixed section
        raw = await _fetch_pdf(BASE + EUR_SECTION)
        local = sorted(glob.glob(os.path.join(CME_DIR, "Section39_*.pdf")), key=os.path.getmtime)
        text = None
        if raw:
            with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
                f.write(raw)
                f.flush()
                text = pdf_text(f.name)
        elif local:
            text = pdf_text(local[-1])
        ch = eur_chain(text, max_days) if text else {}
        if ch.get("strikes"):
            await data_store.put("cme_fx_chain:EURUSD", {**ch, "strikes": {str(k): v for k, v in ch["strikes"].items()}})
            out["EURUSD"] = {"asof": ch["asof"], "strikes": len(ch["strikes"]), "put_call_ratio": ch["put_call_ratio"]}
        else:
            out["EUR"] = "no bulletin (download refused and no PDFs in data/cme/)"
    except Exception as e:
        out["EUR"] = f"{type(e).__name__}: {str(e)[:80]}"
    return out


async def cached_fx_chain(pair: str, max_age_days: int = 5) -> Optional[Dict]:
    import json
    from services import data_store
    p = pair.upper().replace("/", "")
    try:
        c, _ = await data_store.get(f"cme_fx_chain:{p}")
    except Exception:
        c = None
    if not c and os.path.exists(FX_SEED):
        with open(FX_SEED) as f:
            c = (json.load(f) or {}).get(p)
    if not c or not c.get("asof"):
        return None
    if (dt.date.today() - dt.date.fromisoformat(c["asof"])).days > max_age_days:
        return None
    return {**c, "strikes": {float(k): v for k, v in c["strikes"].items()}}
