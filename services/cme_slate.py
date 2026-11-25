"""CME Product Slate export (cmegroup.com → Product Slate → Export, .xlsx) → daily
volume + open interest per market we trade.

What it adds next to the bulletin (which gives strike-level OI):
  - participation: futures OI change day over day with the price change —
    OI up + price up = new longs, OI up + price down = new shorts,
    OI down = covering / liquidation (the move is less trusted)
  - liquidity: how deep each options market is (EUR ≈ 450k OI vs NZD ≈ 8k), shown in
    /audit so thin screens are not over-trusted

The owner sends the export(s) or a zip of them to the bot. No openpyxl needed —
an .xlsx is a zip of XML and only the first sheet is read.
"""

from __future__ import annotations

import io
import re
import time
import zipfile
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Tuple

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}

# our symbol → (futures clearing codes, options clearing codes) — monthly products only;
# weeklies are summed in by product name below.
MAP: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...]]] = {
    "EURUSD": (("EC",), ("EUU",)),
    "USDJPY": (("J1",), ("JPU",)),
    "GBPUSD": (("BP",), ("GBU",)),
    "AUDUSD": (("AD",), ("ADU",)),
    "USDCAD": (("C1",), ("CAU",)),
    "USDCHF": (("E1",), ("CHU",)),
    "NZDUSD": (("NE",), ("NE",)),
    "USDCNH": (("CNH",), ("6H",)),
    "EURGBP": (("RP",), ()),
    "EURJPY": (("RY",), ()),
    "EURCHF": (("RF",), ("RF",)),
    "XAUUSD": (("GC",), ("OG",)),
    "XAGUSD": (("SI",), ("SO",)),
    "SPX500": (("ES",), ("ES",)),
    "NAS100": (("NQ",), ("NQ",)),
    "US30": (("YM",), ("YM", "EYM")),
    "BTCUSD": (("BTC", "MBT"), ("BTC", "WM")),
    "ETHUSD": (("ETH",), ("ETH",)),
    "WTI": (("CL",), ("LO",)),
}
# weekly / EOM option series that belong to the same market (matched on product name)
WEEKLY_NAME = {
    "EURUSD": "EUR/USD Weekly", "USDJPY": "JPY/USD Weekly", "GBPUSD": "GBP/USD Weekly",
    "AUDUSD": "AUD/USD Weekly", "USDCAD": "CAD/USD Weekly", "USDCHF": "CHF/USD Weekly",
    "XAUUSD": "Gold Weekly", "SPX500": "E-mini S&P 500 ", "NAS100": "E-mini Nasdaq-100 ",
}
HEADER = ("Product Name", "Clearing")


def _num(v) -> int:
    s = re.sub(r"[^\d]", "", str(v or ""))
    return int(s) if s else 0


def read_xlsx(blob: bytes) -> List[List[str]]:
    """First sheet of an .xlsx as rows of strings (shared + inline strings, numbers)."""
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        shared: List[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall("m:si", NS):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")))
        sheets = sorted(n for n in z.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n))
        if not sheets:
            return []
        root = ET.fromstring(z.read(sheets[0]))
    rows: List[List[str]] = []
    for r in root.iter(f"{{{NS['m']}}}row"):
        cells: Dict[int, str] = {}
        for c in r.findall("m:c", NS):
            ref = re.match(r"([A-Z]+)", c.get("r", "A"))
            col = 0
            for ch in ref.group(1):
                col = col * 26 + (ord(ch) - 64)
            t, v = c.get("t"), c.find("m:v", NS)
            if t == "s" and v is not None:
                val = shared[int(v.text)]
            elif t == "inlineStr":
                val = "".join(x.text or "" for x in c.iter(f"{{{NS['m']}}}t"))
            else:
                val = v.text if v is not None else ""
            cells[col - 1] = val or ""
        if cells:
            rows.append([cells.get(i, "") for i in range(max(cells) + 1)])
    return rows


def is_slate(rows: List[List[str]]) -> bool:
    return any(r[:2] == list(HEADER) for r in rows[:15])


def parse_rows(rows: List[List[str]]) -> List[Dict]:
    out, cols = [], None
    for r in rows:
        if r[:2] == list(HEADER):
            cols = {name: i for i, name in enumerate(r)}
            continue
        if not cols or not r or not r[0]:
            continue
        g = lambda k: r[cols[k]] if k in cols and cols[k] < len(r) else ""
        out.append({"name": g("Product Name"), "code": g("Clearing"), "asset": g("Asset Class"),
                    "kind": g("Cleared As"), "volume": _num(g("Volume")), "oi": _num(g("Open Interest"))})
    return out


def summarise(products: List[Dict]) -> Dict[str, Dict]:
    """Per symbol: futures vol/OI and options OI (monthly + weeklies)."""
    seen, out = set(), {}
    uniq = []
    for p in products:                                    # the full export repeats the filtered ones
        k = (p["name"], p["code"], p["kind"])
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    for sym, (fut, opt) in MAP.items():
        f = [p for p in uniq if p["kind"] == "Futures" and p["code"] in fut]
        o = [p for p in uniq if p["kind"] == "Options" and p["code"] in opt]
        wk = WEEKLY_NAME.get(sym)
        if wk:
            o += [p for p in uniq if p["kind"] == "Options" and p["code"] not in opt
                  and p["name"].startswith(wk) and ("Weekly" in p["name"] or "EOM" in p["name"])]
        if not f and not o:
            continue
        out[sym] = {"fut_vol": sum(p["volume"] for p in f), "fut_oi": sum(p["oi"] for p in f),
                    "opt_vol": sum(p["volume"] for p in o), "opt_oi": sum(p["oi"] for p in o)}
    return out


def _xlsx_date(blob: bytes) -> Optional[str]:
    """The sheet is named 'Product Slate Nov 25 2026' — that is the trade date."""
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            return date_of(z.read("xl/workbook.xml").decode("utf-8", "ignore"))
    except Exception:
        return None


def slates_from(name: str, blob: bytes) -> List[Tuple[Optional[str], List[Dict]]]:
    """A slate .xlsx, or a zip holding several → (trade date, products) per export."""
    files: List[bytes] = []
    if name.lower().endswith(".xlsx"):
        files = [blob]
    elif blob[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(blob)) as z:
                files = [z.read(n) for n in z.namelist()
                         if n.lower().endswith(".xlsx") and not n.startswith("__MACOSX")]
        except zipfile.BadZipFile:
            return []
    out = []
    for f in files:
        try:
            rows = read_xlsx(f)
        except Exception:
            continue
        if is_slate(rows):
            out.append((_xlsx_date(f), parse_rows(rows)))
    return out


def date_of(rows_or_title: str) -> Optional[str]:
    m = re.search(r"([A-Z][a-z]{2}) (\d{1,2}) (\d{4})", rows_or_title or "")
    if not m:
        return None
    import datetime as dt
    return dt.datetime.strptime(" ".join(m.groups()), "%b %d %Y").date().isoformat()


def participation(prev: Optional[Dict], cur: Dict, price_chg: Optional[float] = None) -> Optional[str]:
    """Futures OI change vs the previous export (and the price direction when known)."""
    if not prev or not prev.get("fut_oi") or not cur.get("fut_oi"):
        return None
    d = cur["fut_oi"] - prev["fut_oi"]
    pct = d / prev["fut_oi"] * 100
    if abs(pct) < 0.5:
        return f"OI flat ({pct:+.1f}%)"
    if d < 0:
        return f"OI {pct:+.1f}% — covering / liquidation (move less trusted)"
    if price_chg is None:
        return f"OI {pct:+.1f}% — new positions"
    return f"OI {pct:+.1f}% — new {'longs' if price_chg > 0 else 'shorts'}"


def depth(opt_oi: int) -> str:
    return "deep" if opt_oi >= 100_000 else "ok" if opt_oi >= 20_000 else "thin"


async def ingest(files: List[Tuple[str, bytes]]) -> Dict:
    from services import data_store
    products: List[Dict] = []
    day = None
    for name, blob in files:
        for d, lst in slates_from(name, blob):
            products += lst
            day = day or d
    if not products:
        return {"ok": False, "reason": "no Product Slate export found"}
    day = day or time.strftime("%Y-%m-%d", time.gmtime())
    cur = summarise(products)
    hist, _ = await data_store.get("cme_slate")
    hist = dict(hist or {})
    prev_day = max((k for k in hist if k < day), default=None)
    prev = hist.get(prev_day) or {}
    hist[day] = cur
    for k in sorted(hist)[:-30]:                           # keep 30 sessions
        hist.pop(k, None)
    await data_store.put("cme_slate", hist)
    rows = []
    for sym, c in sorted(cur.items(), key=lambda kv: -kv[1]["opt_oi"]):
        rows.append({"symbol": sym, **c, "depth": depth(c["opt_oi"]),
                     "participation": participation(prev.get(sym), c)})
    return {"ok": True, "day": day, "previous": prev_day, "products": len(products), "markets": rows}


def reply_text(rep: Dict) -> str:
    if not rep.get("ok"):
        return f"⚠️ CME slate: {rep.get('reason')}"
    lines = [f"📊 CME Product Slate {rep['day']} — {rep['products']} products, {len(rep['markets'])} of our markets"]
    for r in rep["markets"]:
        p = f" · {r['participation']}" if r.get("participation") else ""
        lines.append(f"{r['symbol']}: fut OI {r['fut_oi']:,} · opt OI {r['opt_oi']:,} ({r['depth']}){p}")
    if not rep.get("previous"):
        lines.append("Send tomorrow's export too — OI change (new positions vs covering) needs two days.")
    return "\n".join(lines)
