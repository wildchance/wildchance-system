"""CME Daily Bulletin by hand → the owner sends the PDFs (or a .zip of them) to the bot.

CME refuses Render's downloads, so the bulletin arrives through the owner's private bot chat.
Each PDF is identified by its section number (file name 'Section64_…' or the 'PG64' page
header), converted with pdftotext, kept for 3 days so pairs can arrive in separate messages,
and parsed as soon as a group is complete:
  gold 62 + 64 · GBP 27 + 28 · CAD 29 + 30 · JPY 33 + 34 · CHF 35 + 36 ·
  AUD 38 · EUR 39 · S&P 47 (+ 48) · Nasdaq 40 · Dow 60
Results are stored exactly where the daily refresh stores them, so every engine, terminal and
the data audit see the new chains at once.
"""

from __future__ import annotations

import io
import os
import re
import tempfile
import time
import zipfile
from typing import Dict, List, Optional, Tuple

_STAGE: Dict[int, Tuple[float, str]] = {}            # section → (received, text)
KEEP_S = 3 * 86400
FX_PAIRS = {27: (27, 28), 28: (27, 28), 29: (29, 30), 30: (29, 30), 33: (33, 34), 34: (33, 34),
            35: (35, 36), 36: (35, 36)}


def section_of(name: str, text: str) -> Optional[int]:
    m = re.search(r"Section(\d{2})", name or "")
    if m:
        return int(m.group(1))
    m = re.search(r"\bPG(\d{2})\b", text[:3000])
    return int(m.group(1)) if m else None


def pdfs_from(name: str, blob: bytes) -> List[Tuple[str, bytes]]:
    if name.lower().endswith(".zip") or blob[:2] == b"PK":
        out = []
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            for n in z.namelist():
                if n.lower().endswith(".pdf") and not n.startswith("__MACOSX"):
                    out.append((os.path.basename(n), z.read(n)))
        return out
    return [(name, blob)] if blob[:4] == b"%PDF" else []


def _text(pdf: bytes) -> str:
    from services.cme_bulletin import pdf_text
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(pdf)
        f.flush()
        return pdf_text(f.name)


def _have(*secs: int) -> Optional[List[str]]:
    now = time.time()
    got = [(_STAGE.get(s) or (0, None)) for s in secs]
    if all(t and now - t < KEEP_S for t, _ in got):
        return [x for _, x in got]
    return None


async def _store(key: str, ch: Dict) -> None:
    from services import data_store
    await data_store.put(key, {**ch, "strikes": {str(k): v for k, v in ch["strikes"].items()}})


async def ingest(files: List[Tuple[str, bytes]], max_days: int = 45) -> Dict:
    """Stage every PDF, then parse each group that is now complete. Returns a report."""
    from services import cme_bulletin as cb
    staged, unknown = [], []
    for name, blob in files:
        for n, pdf in pdfs_from(name, blob):
            try:
                txt = _text(pdf)
            except Exception as e:
                unknown.append(f"{n}: {type(e).__name__}")
                continue
            sec = section_of(n, txt)
            if sec is None:
                unknown.append(n)
                continue
            _STAGE[sec] = (time.time(), txt)
            staged.append(sec)
    done, waiting = [], []
    touched = set(staged)
    if touched & {62, 64}:
        t = _have(62, 64)
        if t:
            ch = cb.gold_from_texts(t[1], t[0], max_days)
            if ch.get("strikes"):
                await _store("cme_gold_chain", ch)
                done.append(f"gold (as of {ch['asof']}, P/C {ch.get('put_call_ratio')}, {len(ch['strikes'])} strikes)")
        else:
            waiting.append("gold needs sections 62 + 64")
    for a, b in {FX_PAIRS[s] for s in touched if s in FX_PAIRS}:
        t = _have(a, b)
        if not t:
            waiting.append(f"send section {b if a in touched else a} too (pair {a}+{b})")
            continue
        ch = cb.fx_chain(t[0], t[1], max_days)
        if ch.get("strikes"):
            await _store(f"cme_fx_chain:{ch['pair']}", ch)
            done.append(f"{ch['pair']} (P/C {ch.get('put_call_ratio')}, {len(ch['strikes'])} strikes)")
    singles = {38: ("AUDUSD", lambda t: cb.aud_chain(t, max_days)), 39: ("EURUSD", lambda t: cb.eur_chain(t, max_days)),
               40: ("NAS100", lambda t: cb.index_chain("NAS100", [t], max_days)),
               60: ("US30", lambda t: cb.index_chain("US30", [t], max_days))}
    for sec, (sym, fn) in singles.items():
        if sec in touched:
            ch = fn(_STAGE[sec][1])
            if ch.get("strikes"):
                await _store(f"cme_fx_chain:{sym}", ch)
                done.append(f"{sym} (P/C {ch.get('put_call_ratio')}, {len(ch['strikes'])} strikes)")
    if touched & {47, 48}:
        texts = [_STAGE[s][1] for s in (47, 48) if s in _STAGE and time.time() - _STAGE[s][0] < KEEP_S]
        ch = cb.index_chain("SPX500", texts, max_days)
        if ch.get("strikes"):
            await _store("cme_fx_chain:SPX500", ch)
            done.append(f"SPX500 (P/C {ch.get('put_call_ratio')}, {len(ch['strikes'])} strikes)")
    try:                                                  # drop the 30-min level cache so cards use the new chains
        from services import options_levels as ol
        ol._cache.clear()
    except Exception:
        pass
    return {"staged": sorted(set(staged)), "updated": done, "waiting": waiting, "unrecognised": unknown}


def reply_text(rep: Dict) -> str:
    lines = [f"📥 CME bulletin: received sections {', '.join(map(str, rep['staged'])) or '—'}"]
    lines += [f"✅ {d}" for d in rep["updated"]]
    lines += [f"⏳ {w}" for w in rep["waiting"]]
    if rep["unrecognised"]:
        lines.append("❓ not recognised: " + ", ".join(rep["unrecognised"][:5]))
    if not rep["updated"] and not rep["waiting"] and rep["staged"]:
        lines.append("Those sections are not used (gold 62+64, FX 27–39, indices 40/47/48/60).")
    return "\n".join(lines)
