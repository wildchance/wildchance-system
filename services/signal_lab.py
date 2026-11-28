"""Signal lab — AI-discovered alpha signals on our own markets.

Adapted from NVIDIA's Quantitative Signal Discovery Agent (Apache-2.0) idea: an LLM
proposes signal formulas, they are evaluated by rank IC (Spearman correlation of the
signal with the NEXT day's return across markets), and weak ones are sent back with
advice for another round. Differences, for safety and for our universe:

  • formulas are a small whitelisted expression language parsed with ``ast`` —
    LLM text is NEVER executed as Python;
  • universe = the EA's 15 markets (daily bars: broker feed first, then the free pool);
  • a signal must hold up OUT OF SAMPLE (last 30 % of days) to be accepted;
  • accepted signals give each market a BUY / SELL tilt; the council's SPOTTER agent
    (the sniper's look-out) votes FOR / AGAINST a card from it — graded and muted like
    every other agent if it proves unreliable.

Grammar (x, y = expressions, n = integer window 2..120):
  fields     open high low close ret (= close / delay(close,1) - 1) range (= high - low)
  arithmetic + - * / unary -  numbers
  time       delay(x,n) delta(x,n) ts_return(x,n) ts_mean ts_std ts_sum ts_min ts_max
             ts_rank ts_zscore ema (x,n)   ts_corr(x,y,n)
  cross      rank(x) demean(x)     elementwise  abs sign log sqrt max(x,y) min(x,y)
"""

from __future__ import annotations

import ast
import json
import math
import re
import time
from typing import Dict, List, Optional, Tuple

from decouple import config

UNIVERSE = [s.strip() for s in str(config(
    "SIGNAL_LAB_UNIVERSE", default="BTCUSD,XAUUSD,XAGUSD,USOIL,US30,US100,US500,GER40,EURUSD,GBPUSD,"
                                   "USDJPY,USDCHF,EURJPY,GBPJPY,AUDUSD")).split(",") if s.strip()]
DAYS = int(config("SIGNAL_LAB_DAYS", default="500"))
MIN_IC = float(config("SIGNAL_LAB_MIN_IC", default="0.03"))
MIN_T = float(config("SIGNAL_LAB_MIN_T", default="2.0"))
SPOT_TILT = float(config("SPOTTER_MIN_TILT", default="0.34"))   # |tilt| needed for the Spotter to vote
KEY = "signal_lab"
MAX_SIGNALS = 30
OOS_SHARE = 0.3
MIN_CROSS = 8                       # markets needed on a day for a cross-sectional IC

Panel = List[List[Optional[float]]]  # [day][market]


# ── data ──────────────────────────────────────────────────────────────────────────
async def _daily(symbol: str, n: int) -> List[Dict]:
    """Oldest-first daily bars {d, open, high, low, close}."""
    d = None
    try:
        from services import broker_feed
        d = await broker_feed.td_values(symbol, "1day", n, "UTC")
    except Exception:
        d = None
    if not d or len(d.get("values") or []) < 60:
        try:
            from services.price_pool import alt_values
            d = await alt_values(symbol, "1day", n)
        except Exception:
            d = None
    out = []
    for v in (d or {}).get("values") or []:
        try:
            out.append({"d": str(v["datetime"])[:10], "open": float(v["open"]), "high": float(v["high"]),
                        "low": float(v["low"]), "close": float(v["close"])})
        except (KeyError, TypeError, ValueError):
            continue
    return sorted({b["d"]: b for b in out}.values(), key=lambda b: b["d"])


def build_panel(bars: Dict[str, List[Dict]]) -> Tuple[List[str], List[str], Dict[str, Panel]]:
    """Align markets on the union of dates (missing → None)."""
    syms = [s for s in bars if len(bars[s]) >= 60]
    dates = sorted({b["d"] for s in syms for b in bars[s]})
    idx = {d: i for i, d in enumerate(dates)}
    fields = {f: [[None] * len(syms) for _ in dates] for f in ("open", "high", "low", "close")}
    for j, s in enumerate(syms):
        for b in bars[s]:
            for f in fields:
                fields[f][idx[b["d"]]][j] = b[f]
    return dates, syms, fields


_panel_cache: Dict = {"t": 0.0, "v": None}


async def panel(force: bool = False):
    if not force and _panel_cache["v"] and time.time() - _panel_cache["t"] < 6 * 3600:
        return _panel_cache["v"]
    import asyncio
    got = await asyncio.gather(*(_daily(s, DAYS) for s in UNIVERSE), return_exceptions=True)
    bars = {s: g for s, g in zip(UNIVERSE, got) if isinstance(g, list)}
    v = build_panel(bars)
    _panel_cache.update(t=time.time(), v=v)
    return v


# ── formula language ──────────────────────────────────────────────────────────────
class FormulaError(ValueError):
    pass


def _cols(x: Panel) -> int:
    return len(x[0]) if x else 0


def _map(x: Panel, f) -> Panel:
    return [[(None if v is None else _safe(f, v)) for v in row] for row in x]


def _safe(f, *a):
    try:
        r = f(*a)
        return r if r is not None and math.isfinite(r) else None
    except (ValueError, ZeroDivisionError, OverflowError, TypeError):
        return None


def _zip(x: Panel, y: Panel, f) -> Panel:
    return [[(None if a is None or b is None else _safe(f, a, b)) for a, b in zip(rx, ry)] for rx, ry in zip(x, y)]


def _const(c: float, like: Panel) -> Panel:
    return [[c] * _cols(like) for _ in like]


def _ts(x: Panel, n: int, f) -> Panel:
    """Rolling window of the last n values per market (all present) → f(window)."""
    T, J = len(x), _cols(x)
    out: Panel = [[None] * J for _ in range(T)]
    for j in range(J):
        col = [row[j] for row in x]
        for t in range(n - 1, T):
            w = col[t - n + 1:t + 1]
            if None not in w:
                out[t][j] = _safe(f, w)
    return out


def _mean(w):
    return sum(w) / len(w)


def _std(w):
    m = _mean(w)
    return math.sqrt(sum((v - m) ** 2 for v in w) / (len(w) - 1)) if len(w) > 1 else None


def _rank_last(w):
    last = w[-1]
    return sum(1 for v in w if v <= last) / len(w)


def _z(w):
    s = _std(w)
    return (w[-1] - _mean(w)) / s if s else None


def _ema(x: Panel, n: int) -> Panel:
    a = 2 / (n + 1)
    out: Panel = [[None] * _cols(x) for _ in x]
    for j in range(_cols(x)):
        prev = None
        for t, row in enumerate(x):
            v = row[j]
            if v is None:
                prev = None
                continue
            prev = v if prev is None else a * v + (1 - a) * prev
            out[t][j] = prev
    return out


def _shift(x: Panel, n: int) -> Panel:
    J = _cols(x)
    return [[None] * J for _ in range(min(n, len(x)))] + x[:max(0, len(x) - n)]


def _corr(a: List[float], b: List[float]) -> Optional[float]:
    if len(a) < 3:
        return None
    ma, mb = _mean(a), _mean(b)
    va = sum((v - ma) ** 2 for v in a)
    vb = sum((v - mb) ** 2 for v in b)
    if va <= 0 or vb <= 0:
        return None
    return sum((p - ma) * (q - mb) for p, q in zip(a, b)) / math.sqrt(va * vb)


def _ts_corr(x: Panel, y: Panel, n: int) -> Panel:
    T, J = len(x), _cols(x)
    out: Panel = [[None] * J for _ in range(T)]
    for j in range(J):
        cx, cy = [r[j] for r in x], [r[j] for r in y]
        for t in range(n - 1, T):
            wx, wy = cx[t - n + 1:t + 1], cy[t - n + 1:t + 1]
            if None not in wx and None not in wy:
                out[t][j] = _corr(wx, wy)
    return out


def _ranks(vals: List[float]) -> List[float]:
    """Average ranks (ties share), scaled 0..1."""
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    r = [0.0] * len(vals)
    i = 0
    while i < len(order):
        k = i
        while k + 1 < len(order) and vals[order[k + 1]] == vals[order[i]]:
            k += 1
        avg = (i + k) / 2
        for m in range(i, k + 1):
            r[order[m]] = avg
        i = k + 1
    d = max(1, len(vals) - 1)
    return [v / d for v in r]


def _cs(x: Panel, f) -> Panel:
    out = []
    for row in x:
        idx = [j for j, v in enumerate(row) if v is not None]
        new = [None] * len(row)
        if idx:
            res = f([row[j] for j in idx])
            for j, v in zip(idx, res):
                new[j] = v
        out.append(new)
    return out


_TS1 = {"ts_mean": _mean, "ts_std": _std, "ts_sum": sum, "ts_min": min, "ts_max": max,
        "ts_rank": _rank_last, "ts_zscore": _z}
_UNARY = {"abs": abs, "sign": lambda v: (v > 0) - (v < 0), "log": lambda v: math.log(v) if v > 0 else None,
          "sqrt": lambda v: math.sqrt(v) if v >= 0 else None}
FUNCS = set(_TS1) | set(_UNARY) | {"delay", "delta", "ts_return", "ema", "ts_corr", "rank", "demean", "max", "min"}
FIELDS = ("open", "high", "low", "close", "ret", "range")
MAX_NODES = 60


def _window(node) -> int:
    if not (isinstance(node, ast.Constant) and isinstance(node.value, (int, float))):
        raise FormulaError("window must be a number")
    n = int(node.value)
    if not 1 <= n <= 120:
        raise FormulaError("window must be 1..120")
    return n


def evaluate(formula: str, fields: Dict[str, Panel]) -> Panel:
    """Parse ``formula`` with the whitelisted grammar and compute it on the panel."""
    formula = str(formula or "").strip()
    if not formula or len(formula) > 400:
        raise FormulaError("empty or too long")
    try:
        tree = ast.parse(formula, mode="eval")
    except SyntaxError as e:
        raise FormulaError(f"syntax: {e.msg}")
    if sum(1 for _ in ast.walk(tree)) > MAX_NODES:
        raise FormulaError("formula too complex")
    close = fields["close"]
    env = dict(fields)
    env["ret"] = _zip(close, _shift(close, 1), lambda a, b: a / b - 1)
    env["range"] = _zip(fields["high"], fields["low"], lambda a, b: a - b)

    def ev(n) -> Panel:
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
            return _const(float(n.value), close)
        if isinstance(n, ast.Name):
            if n.id not in FIELDS:
                raise FormulaError(f"unknown field '{n.id}'")
            return env[n.id]
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
            v = ev(n.operand)
            return _map(v, lambda a: -a) if isinstance(n.op, ast.USub) else v
        if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            a, b = ev(n.left), ev(n.right)
            f = {ast.Add: lambda p, q: p + q, ast.Sub: lambda p, q: p - q, ast.Mult: lambda p, q: p * q,
                 ast.Div: lambda p, q: p / q if q else None}[type(n.op)]
            return _zip(a, b, f)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and not n.keywords:
            name, args = n.func.id, n.args
            if name not in FUNCS:
                raise FormulaError(f"unknown function '{name}'")
            if name in _TS1:
                _arity(name, args, 2)
                f = _TS1[name]
                return _ts(ev(args[0]), max(2, _window(args[1])), f)
            if name in _UNARY:
                _arity(name, args, 1)
                return _map(ev(args[0]), _UNARY[name])
            if name == "delay":
                _arity(name, args, 2)
                return _shift(ev(args[0]), _window(args[1]))
            if name == "delta":
                _arity(name, args, 2)
                x = ev(args[0])
                return _zip(x, _shift(x, _window(args[1])), lambda p, q: p - q)
            if name == "ts_return":
                _arity(name, args, 2)
                x = ev(args[0])
                return _zip(x, _shift(x, _window(args[1])), lambda p, q: p / q - 1 if q else None)
            if name == "ema":
                _arity(name, args, 2)
                return _ema(ev(args[0]), max(2, _window(args[1])))
            if name == "ts_corr":
                _arity(name, args, 3)
                return _ts_corr(ev(args[0]), ev(args[1]), max(3, _window(args[2])))
            if name == "rank":
                _arity(name, args, 1)
                return _cs(ev(args[0]), _ranks)
            if name == "demean":
                _arity(name, args, 1)
                return _cs(ev(args[0]), lambda v: [a - _mean(v) for a in v])
            if name in ("max", "min"):
                _arity(name, args, 2)
                return _zip(ev(args[0]), ev(args[1]), max if name == "max" else min)
        raise FormulaError(f"not allowed: {type(n).__name__}")

    return ev(tree)


def _arity(name: str, args, k: int) -> None:
    if len(args) != k:
        raise FormulaError(f"{name} takes {k} argument(s)")


# ── scoring ───────────────────────────────────────────────────────────────────────
def forward_returns(close: Panel, h: int = 1) -> Panel:
    return _zip(_shift_back(close, h), close, lambda a, b: a / b - 1 if b else None)


def _shift_back(x: Panel, n: int) -> Panel:
    J = _cols(x)
    return x[n:] + [[None] * J for _ in range(min(n, len(x)))]


def daily_ic(sig: Panel, fwd: Panel) -> List[Optional[float]]:
    out = []
    for rs, rf in zip(sig, fwd):
        pairs = [(a, b) for a, b in zip(rs, rf) if a is not None and b is not None]
        if len(pairs) < MIN_CROSS or len({a for a, _ in pairs}) < 3:
            out.append(None)
            continue
        out.append(_corr(_ranks([a for a, _ in pairs]), _ranks([b for _, b in pairs])))
    return out


def _stats(ics: List[float]) -> Dict:
    if len(ics) < 20:
        return {"ic": None, "t": None, "days": len(ics)}
    m, s = _mean(ics), _std(ics)
    return {"ic": round(m, 4), "t": round(m / (s / math.sqrt(len(ics))), 2) if s else None,
            "ir": round(m / s, 3) if s else None, "hit": round(sum(1 for v in ics if v * m > 0) / len(ics), 3),
            "days": len(ics)}


def score(formula: str, fields: Dict[str, Panel]) -> Dict:
    """In-sample / out-of-sample rank IC of a formula vs next-day return."""
    try:
        sig = evaluate(formula, fields)
    except FormulaError as e:
        return {"formula": formula, "error": str(e)}
    ics = daily_ic(sig, forward_returns(fields["close"]))
    valid = [(i, v) for i, v in enumerate(ics) if v is not None]
    if len(valid) < 60:
        return {"formula": formula, "error": f"too few scored days ({len(valid)})"}
    cut = int(len(valid) * (1 - OOS_SHARE))
    ins, oos = [v for _, v in valid[:cut]], [v for _, v in valid[cut:]]
    r = {"formula": formula, "in": _stats(ins), "oos": _stats(oos), "all": _stats([v for _, v in valid])}
    r["accepted"], r["direction"] = judge(r)
    return r


def judge(r: Dict) -> Tuple[bool, int]:
    """Strong in sample AND the same sign, at least half as strong, out of sample.
    A reliably NEGATIVE IC is accepted too, traded inverted (direction −1)."""
    ins, oos = r["in"], r["oos"]
    if ins.get("ic") is None or oos.get("ic") is None or ins.get("t") is None:
        return False, 0
    d = 1 if ins["ic"] > 0 else -1
    ok = abs(ins["ic"]) >= MIN_IC and abs(ins["t"]) >= MIN_T and oos["ic"] * d >= MIN_IC / 2
    return ok, d if ok else 0


# ── the LLM loop ──────────────────────────────────────────────────────────────────
SYSTEM = (
    "You are a quantitative researcher. Propose daily cross-market alpha signals for "
    f"{len(UNIVERSE)} markets (FX, metals, oil, indices, BTC). A signal is ONE formula in this exact "
    "language (anything else is rejected):\n"
    "fields: open high low close ret range   (no volume)\n"
    "operators: + - * / and unary minus, numbers\n"
    "time-series (n = integer 1..120): delay(x,n) delta(x,n) ts_return(x,n) ts_mean(x,n) ts_std(x,n) "
    "ts_sum(x,n) ts_min(x,n) ts_max(x,n) ts_rank(x,n) ts_zscore(x,n) ema(x,n) ts_corr(x,y,n)\n"
    "cross-section: rank(x) demean(x)   elementwise: abs sign log sqrt max(x,y) min(x,y)\n"
    "Prices differ wildly in scale between markets, so use scale-free forms (returns, ratios, "
    "z-scores, ranks), never raw price levels. Higher signal = expect higher next-day return.\n"
    'Reply with JSON only: [{"name": "...", "formula": "...", "idea": "one line"}]')


def parse_ideas(text: str) -> List[Dict]:
    m = re.search(r"\[.*\]", text or "", re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return []
    out = []
    for d in data if isinstance(data, list) else []:
        if isinstance(d, dict) and isinstance(d.get("formula"), str):
            out.append({"name": str(d.get("name") or "signal")[:60], "formula": d["formula"][:400],
                        "idea": str(d.get("idea") or "")[:200]})
    return out


def _feedback(results: List[Dict]) -> str:
    lines = []
    for r in results:
        if r.get("error"):
            lines.append(f"- {r['formula']} → REJECTED: {r['error']}")
        else:
            lines.append(f"- {r['formula']} → in-sample IC {r['in']['ic']} (t {r['in']['t']}), "
                         f"out-of-sample IC {r['oos']['ic']}{' ✅ accepted' if r['accepted'] else ''}")
    return "\n".join(lines)


async def run(theme: str = "momentum and mean reversion", rounds: int = 3, per_round: int = 5,
              ask=None) -> Dict:
    """Generate → score → advise → retry. ``ask`` (tests) replaces the NVIDIA call."""
    if ask is None:
        from services import ai_sentiment as ai

        async def ask(msgs):
            return await ai.ask_model(msgs, role="deep")
    dates, syms, fields = await panel()
    if len(syms) < MIN_CROSS or len(dates) < 120:
        return {"ok": False, "reason": f"not enough data ({len(syms)} markets, {len(dates)} days)"}
    msgs = [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"Theme: {theme}. Give {per_round} different signals."}]
    tried: List[Dict] = []
    for _ in range(max(1, min(rounds, 5))):
        txt = await ask(msgs)
        if not txt:
            break
        batch = [score(i["formula"], fields) | {"name": i["name"], "idea": i["idea"]}
                 for i in parse_ideas(txt)[:per_round] if i["formula"] not in {t["formula"] for t in tried}]
        if not batch:
            break
        tried += batch
        msgs += [{"role": "assistant", "content": txt},
                 {"role": "user", "content": "Results (rank IC vs next-day return):\n" + _feedback(batch) +
                  f"\nKeep what works, fix errors, and give {per_round} NEW improved signals."}]
    accepted = [t for t in tried if t.get("accepted")]
    saved = await save(accepted, theme)
    _tilt_cache.update(t=0.0, v=None)
    return {"ok": True, "theme": theme, "markets": syms, "days": len(dates), "tried": len(tried),
            "accepted": accepted, "saved_total": saved,
            "best": sorted([t for t in tried if not t.get("error")],
                           key=lambda t: -abs(t["all"].get("ic") or 0))[:5]}


async def load() -> List[Dict]:
    from services import data_store
    v, _ = await data_store.get(KEY)
    return list(v or [])


async def save(new: List[Dict], theme: str) -> int:
    if not new:
        return len(await load())
    from services import data_store
    cur = {s["formula"]: s for s in await load()}
    for s in new:
        cur[s["formula"]] = {"formula": s["formula"], "name": s.get("name"), "idea": s.get("idea"),
                             "direction": s["direction"], "in": s["in"], "oos": s["oos"], "theme": theme,
                             "t": round(time.time())}
    keep = sorted(cur.values(), key=lambda s: -abs(s["oos"].get("ic") or 0))[:MAX_SIGNALS]
    await data_store.put(KEY, keep)
    return len(keep)


# ── votes ─────────────────────────────────────────────────────────────────────────
def tilt_from(signals: List[Dict], fields: Dict[str, Panel], syms: List[str]) -> Dict[str, Dict]:
    """Per market: mean over accepted signals of +1 (top third today) / −1 (bottom third)."""
    votes: Dict[str, List[int]] = {s: [] for s in syms}
    for s in signals:
        try:
            row = evaluate(s["formula"], fields)[-1]
        except FormulaError:
            continue
        idx = [j for j, v in enumerate(row) if v is not None]
        if len(idx) < MIN_CROSS:
            continue
        rk = _ranks([row[j] for j in idx])
        for j, r in zip(idx, rk):
            votes[syms[j]].append(s["direction"] * (1 if r >= 2 / 3 else -1 if r <= 1 / 3 else 0))
    return {sym: {"tilt": round(_mean(v), 2) if v else 0.0, "signals": len(v)} for sym, v in votes.items()}


_tilt_cache: Dict = {"t": 0.0, "v": None}


async def tilts(force: bool = False) -> Dict[str, Dict]:
    if not force and _tilt_cache["v"] is not None and time.time() - _tilt_cache["t"] < 3600:
        return _tilt_cache["v"]
    sigs = await load()
    v: Dict[str, Dict] = {}
    if sigs:
        _, syms, fields = await panel()
        v = tilt_from(sigs, fields, syms)
    _tilt_cache.update(t=time.time(), v=v)
    return v


def _key(symbol: str) -> str:
    return symbol.upper().replace("/", "").split(".")[0]


async def spotter(symbol: str) -> Optional[Dict]:
    """Today's tilt for ``symbol`` (None when the lab has no accepted signals)."""
    try:
        t = (await tilts()).get(_key(symbol))
    except Exception:
        return None
    return t if t and t.get("signals") else None
