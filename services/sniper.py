"""SNIPER — precision entries at the artillery's levels.

The artillery (yearly / anchor / event boxes → multi-anchor golden clusters) marks
WHERE; the sniper decides WHEN, on the 1h chart, with the journal's trigger:

  1. RANGE   price is at a golden cluster (within its tolerance band)
  2. SWEEP   the 1h bars ran INTO the band (liquidity taken at the level)
  3. TRIGGER the fastest that fires, others listed as confirmations:
             1h CISD → 1h RTO → 4h CISD → 4h RTO → D1 CISD → D1 OB line → W1 CISD
             CISD = close back through the OPEN of the last opposing run of candles;
             RTO  = after the sweep, displacement ≥ its depth, then a hold at the origin;
             OB line = 'buy above / sell below D1 OB' (daily close through the last
             opposing candle before the latest structure break)
  4. GATE    trade_gate (session, ≥1.3R, weekly profile) + the case library: the
             cluster's best ROLE must have held ≥ SNIPER_MIN_RATE recently (once n ≥ 3)
  5. COUNCIL the agent council (services.council) must vote GO — no macro veto

Entry = the CISD close · stop = beyond the sweep extreme (≥ the minimum stop) ·
targets = the next clusters in the trade direction. Every fire is recorded as a live
case and later resolved (TP1 before stop = held), so the role stats keep learning.
"""

from __future__ import annotations

import datetime as dt
from typing import Dict, List, Optional, Sequence

from decouple import config

MIN_RATE = float(config("SNIPER_MIN_RATE", default="0.5"))
LOOKBACK = int(config("SNIPER_LOOKBACK_H", default="12"))
DEFAULT_SYMBOLS = str(config("SNIPER_SYMBOLS", default="EUR/USD,GBP/USD,USD/JPY,XAU/USD,BTC/USD"))
STOCKS = str(config("SNIPER_STOCKS", default="NVDA"))           # US stocks (TwelveData / Polygon)
ENTRY_TFS = tuple(x.strip() for x in str(config("SNIPER_ENTRY_TFS", default="1h,4h")).split(","))
POSITION_MIN_HIST = int(config("POSITION_MIN_HIST_PCT", default="15"))   # (kept for compatibility)
REALISTIC_MIN_HIST = int(config("REALISTIC_MIN_HIST", default="25"))   # aim only as far as ≥25 % of years go
SWING_HORIZON_D = int(config("SWING_HORIZON_DAYS", default="5"))      # expected-move horizon for TP3
MAX_TARGET_PCT = float(config("SNIPER_MAX_TARGET_PCT", default="0.12"))   # swing horizon;
#   anything further is a POSITION target from the yearly CBDR ranges (position_targets)


def roles_of(members: Sequence[str]) -> List[str]:
    from services.yearly_cbdr import ladder_name
    out = []
    for m in members:
        try:
            r = ladder_name(float(m.rsplit(" ", 1)[-1]))
        except ValueError:
            r = None
        if r and r not in out:
            out.append(r)
    return out


def cisd(bars: Sequence[Dict], side: str, band: Sequence[float]) -> Dict:
    """CISD on oldest-first bars {open, high, low, close}. ``band`` = (low, high)."""
    if len(bars) < 4:
        return {"fired": False, "why": "not enough bars"}
    buy = side == "BUY"
    opposing = (lambda b: b["close"] < b["open"]) if buy else (lambda b: b["close"] > b["open"])
    last = bars[-1]
    j = len(bars) - 2
    while j >= 0 and not opposing(bars[j]):
        j -= 1
    if j < 0:
        return {"fired": False, "why": "no opposing run"}
    s = j
    while s - 1 >= 0 and opposing(bars[s - 1]):
        s -= 1
    level = bars[s]["open"]
    seg = bars[s:]
    swept = (min(b["low"] for b in seg) <= band[1]) if buy else (max(b["high"] for b in seg) >= band[0])
    crossed = last["close"] > level if buy else last["close"] < level
    extreme = min(b["low"] for b in seg) if buy else max(b["high"] for b in seg)
    return {"fired": bool(swept and crossed), "level": level, "extreme": extreme,
            "entry": last["close"], "swept": swept, "crossed": crossed,
            "why": None if swept and crossed else ("no sweep into the zone" if not swept
                                                   else "no close through the run's open")}


def rto(bars: Sequence[Dict], side: str, band: Sequence[float]) -> Dict:
    """Return-to-origin: after the sweep INTO the zone, price displaces away from the
    origin (the open of the first candle that turned it) by ≥ the sweep depth, then
    comes back to that origin and holds it on the close → entry at the origin."""
    if len(bars) < 5:
        return {"fired": False, "why": "not enough bars"}
    buy = side == "BUY"
    idx = (min if buy else max)(range(len(bars) - 1),
                                key=lambda i: bars[i]["low"] if buy else bars[i]["high"])
    ext = bars[idx]["low"] if buy else bars[idx]["high"]
    if not ((ext <= band[1]) if buy else (ext >= band[0])):
        return {"fired": False, "why": "no sweep into the zone"}
    turn = next((j for j in range(idx, len(bars) - 1)
                 if (bars[j]["close"] > bars[j]["open"]) == buy), None)
    if turn is None:
        return {"fired": False, "why": "no turn after the sweep"}
    origin = bars[turn]["open"]
    depth = abs(origin - ext)
    after = bars[turn:-1]
    away = (max(b["high"] for b in after) - origin) if buy else (origin - min(b["low"] for b in after))
    last = bars[-1]
    back = (last["low"] <= origin < last["close"]) if buy else (last["high"] >= origin > last["close"])
    fired = bool(after and away >= depth and back)
    return {"fired": fired, "level": origin, "extreme": ext, "entry": last["close"],
            "why": None if fired else ("no displacement yet" if away < depth else "not back at the origin")}


def ob_line(daily: Sequence[Dict], side: str, lookback: int = 5) -> Dict:
    """'Buy above / sell below D1 OB': the last opposing daily candle before the latest
    structure break; the trigger is a daily close through that candle's far edge."""
    buy = side == "BUY"
    for i in range(len(daily) - 1, lookback, -1):
        prior = daily[i - lookback:i]
        broke = daily[i]["close"] > max(b["high"] for b in prior) if buy \
            else daily[i]["close"] < min(b["low"] for b in prior)
        if not broke:
            continue
        j = next((k for k in range(i - 1, -1, -1)
                  if (daily[k]["close"] < daily[k]["open"]) == buy), None)
        if j is None:
            break
        line = daily[j]["high"] if buy else daily[j]["low"]
        last, prev = daily[-1], daily[-2]
        crossed = (prev["close"] <= line < last["close"]) if buy else (prev["close"] >= line > last["close"])
        return {"fired": crossed, "level": line, "entry": last["close"],
                "extreme": min(b["low"] for b in daily[j:]) if buy else max(b["high"] for b in daily[j:]),
                "why": None if crossed else f"{'buy above' if buy else 'sell below'} {line}"}
    return {"fired": False, "why": "no D1 structure break"}


TRIGGER_ORDER = [("1h", "cisd"), ("1h", "rto"), ("4h", "cisd"), ("4h", "rto"),
                 ("1day", "cisd"), ("1day", "ob_line"), ("1week", "cisd")]


def triggers(by_tf: Dict[str, Sequence[Dict]], side: str, band: Sequence[float]) -> Dict:
    """Every trigger on every timeframe; the PRIMARY is the fastest that fired (its
    close is the entry, its extreme sets the stop); the rest are confirmations."""
    fns = {"cisd": lambda b: cisd(b, side, band), "rto": lambda b: rto(b, side, band),
           "ob_line": lambda b: ob_line(b, side)}
    out = []
    for tf, kind in TRIGGER_ORDER:
        bars = by_tf.get(tf) or []
        if len(bars) >= 4:
            out.append({"tf": tf, "type": kind, **fns[kind](bars)})
    fired = [t for t in out if t["fired"]]
    # only fast timeframes set entry/stop (a weekly sweep = a 26k BTC stop); D1/W1 confirm
    entry = [t for t in fired if t["tf"] in ENTRY_TFS]
    primary = entry[0] if entry else None
    return {"primary": primary, "confirmations": [t for t in fired if t is not primary], "all": out}


def candidates(r: Dict, bars: Sequence[Dict]) -> List[Dict]:
    """Golden clusters the recent 1h bars traded into; side from where price came from."""
    from services.yearly_cbdr import tolerance
    if not r.get("ok") or not bars:
        return []
    price = bars[-1]["close"]
    tol = tolerance(r["symbol"], price)
    recent = bars[-LOOKBACK:]
    lo, hi = min(b["low"] for b in recent), max(b["high"] for b in recent)
    before = bars[:-LOOKBACK] or bars[:1]
    came_from = sum(b["close"] for b in before) / len(before)
    out = []
    for c in r.get("clusters") or []:
        band = (c["low"] - tol / 2, c["high"] + tol / 2)
        if hi < band[0] or lo > band[1]:
            continue
        side = "BUY" if came_from > c["high"] else "SELL" if came_from < c["low"] else None
        if side:
            out.append({**c, "band": band, "side": side})
    return out


def position_targets(r: Dict, side: str, entry: float, stop: float, n: int = 3) -> List[Dict]:
    """Yearly-CBDR levels BEYOND the swing horizon (> SNIPER_MAX_TARGET_PCT): the
    position-trade objectives — yearly ranges move long-term, so they are held, not
    scalped. Next clusters in the trade direction, ≥ 1R apart, prices > 0."""
    risk = abs(entry - stop)
    if risk <= 0:
        return []
    far = sorted((c for c in r.get("clusters") or []
                  if (c["mid"] > entry if side == "BUY" else c["mid"] < entry)
                  and c["mid"] > 0 and abs(c["mid"] - entry) > MAX_TARGET_PCT * entry),
                 key=lambda c: abs(c["mid"] - entry))
    from services.yearly_cbdr import position as _pos, reach_pct
    box = (r.get("years") or [None])[-1]                # latest completed yearly box
    hist = r.get("history")
    out, last = [], None
    for c in far:
        if last is not None and abs(c["mid"] - last) < risk:
            continue
        move = abs(c["mid"] - entry)
        p = {"name": f"P{len(out) + 1}", "price": c["mid"], "rr": round(move / risk, 2),
             "pct": round(100 * move / entry, 1), "level": ", ".join(c["members"][:2])}
        if box and hist:
            k = _pos({"low": box["box_low"], "range": box["box_high"] - box["box_low"]}, c["mid"])
            p["hist"] = reach_pct(hist, k)              # % of years that travelled this far
        out.append(p)
        last = c["mid"]
        if len(out) == n:
            break
    return out


def realistic_cap(r: Dict, side: str, min_hist: int = None) -> Optional[Dict]:
    """The furthest yearly level history says is REALISTIC: the largest box multiple
    reached in ≥ min_hist % of years, priced on the latest yearly box. None without
    base rates. EUR/USD up → 1.5 (33 %), gold up → 3 (28 %), USD/JPY down → −0.5."""
    from services.yearly_cbdr import UP_STEPS, DOWN_STEPS
    min_hist = REALISTIC_MIN_HIST if min_hist is None else min_hist
    hist = r.get("history")
    box = (r.get("years") or [None])[-1]
    if not hist or not box:
        return None
    if side == "BUY":
        table, steps = hist.get("pct_reach_up") or {}, UP_STEPS
    else:
        table, steps = hist.get("pct_reach_down") or {}, DOWN_STEPS
    ok = [t for t in steps if (table.get(f"{t:g}") or 0) >= min_hist]
    k = (max(ok) if side == "BUY" else min(ok)) if ok else (1.0 if side == "BUY" else 0.0)
    pct = table.get(f"{k:g}", 100)
    rng = box["box_high"] - box["box_low"]
    return {"k": k, "price": box["box_low"] + k * rng, "hist": pct, "year": box["year"]}


def apply_cap(targets: List[Dict], cap: Optional[Dict], side: str) -> List[Dict]:
    if not cap:
        return targets
    return [t for t in targets if (t["price"] <= cap["price"] if side == "BUY" else t["price"] >= cap["price"])]


def targets_from(r: Dict, side: str, entry: float, stop: float) -> List[Dict]:
    from services.trade_gate import MIN_RR
    risk = abs(entry - stop)
    if risk <= 0:
        return []
    mids = sorted((c for c in r.get("clusters") or []
                   if (c["mid"] > entry if side == "BUY" else c["mid"] < entry)
                   and c["mid"] > 0 and abs(c["mid"] - entry) <= MAX_TARGET_PCT * entry),
                  key=lambda c: abs(c["mid"] - entry))
    out, last = [], entry
    for c in mids:
        move = abs(c["mid"] - entry)
        if move < MIN_RR * risk or (out and abs(c["mid"] - last) < risk):
            continue
        out.append({"name": f"TP{len(out) + 1}", "price": c["mid"], "rr": round(move / risk, 2),
                    "level": ", ".join(c["members"][:2])})
        last = c["mid"]
        if len(out) == 3:
            break
    return out


_held: set = set()                      # setups held today (council / gate) — promotion watch


def account_fit(sym: str, entry: float, stop: float) -> Optional[str]:
    """Hold the shot if even 0.01 lot over-risks the LARGEST card balance (the lot card
    would read '⛔ skip' on every account — no point sending it)."""
    try:
        from propfirm.target_lots import card_balances, pip_size, pip_value_per_lot
        ps, pv = pip_size(sym), pip_value_per_lot(sym, entry)
        if not ps or not pv:
            return None                       # stocks / indices: no lot ladder to check
        risk = 0.01 * abs(entry - stop) / ps * pv
        cap = max(card_balances()) * 0.5 / 100
        if risk > cap:
            return f"stop too wide for the accounts (0.01 lot risks ${risk:,.2f} > ${cap:,.2f})"
    except Exception:
        return None
    return None


def card(sym: str, s: Dict) -> str:
    from routes.cbdr import px
    rs = s.get("role_stats")
    trig = s.get("trigger") or "1h CISD"
    conf = f" + {', '.join(s['confirmations'])}" if s.get("confirmations") else ""
    mem = s["members"][:4] + ([f"+{len(s['members']) - 4} more"] if len(s["members"]) > 4 else [])
    head = ("📈 *POSITION TRADE" if s.get("trade_type") == "position" else "🎯 *SNIPER")
    lines = [f"{head} {s['side']} — {sym}* ({trig}{conf} at the artillery's level)",
             f"Zone {px(sym, s['zone'][0])}–{px(sym, s['zone'][1])}: " + ", ".join(mem),
             f"Roles: {', '.join(s['roles']) or '—'}"
             + (f" · {rs['role']} held {rs['held']}/{rs['n']} (recent {rs['recent_rate']:.0%})" if rs else ""),
             f"Entry {px(sym, s['entry'])} · SL {px(sym, s['stop'])} (sweep {px(sym, s['extreme'])})",
             "   " + " · ".join(f"{t['name']} {px(sym, t['price'])} ({t['rr']}R"
                                + (f", {t['pct']}%" if "pct" in t else "")
                                + (f", hist {t['hist']}%" if t.get("hist") is not None else "") + ")"
                                for t in s["targets"])]
    if s.get("trade_type") == "position" and any(t.get("level") == "1R" for t in s["targets"]):
        lines.append("🧭 Plan: ⅓ at TP1 (1R) → stop to breakeven · scale out at each P level · "
                     "hold the rest for the yearly objective")
    elif s.get("trade_type") != "position" and any(t["name"] == "TP1" and t.get("level") == "1R"
                                                   for t in s["targets"]):
        sw = s.get("sweet") or {}
        z = sw.get("zone")
        lines.append("🧭 Plan: ⅓ at TP1 (1R) → stop to breakeven · ⅓ at TP2 (2R) · runner to TP3"
                     + (f" — sweet spot {px(sym, min(z))}–{px(sym, max(z))} (1σ–2σ"
                        + (f", best {sw['best_k']:g}σ" if sw.get("best_k") else "") + ")" if z else ""))
    if s.get("cap"):
        cp = s["cap"]
        lines.append(f"📏 Realistic reach: {cp['year']} box {cp['k']:g} = {px(sym, cp['price'])} "
                     f"(reached in {cp['hist']}% of years) — targets beyond it trimmed")
    if s.get("trade_type") == "position":
        lines.append("   _yearly CBDR objectives — a long-term hold; scale out at each P level_")
    elif s.get("position_targets"):
        lines.append("📈 Position runner (yearly CBDR): " + " · ".join(
            f"{t['name']} {px(sym, t['price'])} ({t['pct']}%"
            + (f", hist {t['hist']}%" if t.get("hist") is not None else "") + ")"
            for t in s["position_targets"]))
    if s.get("intraday"):
        lines.append("⏱ also at: " + ", ".join(s["intraday"]))
    if s.get("council"):
        from services.council import summary
        lines.append(summary(s["council"]))
    try:
        from propfirm.target_lots import card_lines
        lines += card_lines(sym, s["entry"], s["stop"], s["targets"])
    except Exception:
        pass
    return "\n".join(lines)


async def _bars(symbol: str, interval: str, n: int, tz: str = "UTC") -> List[Dict]:
    from services.ohlc_service import fetch_ohlc_raw
    try:
        return await fetch_ohlc_raw(symbol, interval, n, timezone=tz)
    except Exception:
        return []


async def _bars_1h(symbol: str, n: int = 72, tz: str = "UTC") -> List[Dict]:
    return await _bars(symbol, "1h", n, tz)


async def resolve_pending() -> Dict:
    """held = TP1 reached before the stop; broke = stop first (1h bars since the fire)."""
    try:
        from sqlalchemy import select
        from database.db import AsyncSessionLocal
        from models.case_model import LiveCase
    except Exception:
        return {"resolved": 0}
    done = 0
    try:
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(select(LiveCase).where(LiveCase.status == "pending"))).scalars().all()
            for c in rows:
                bars = await _bars_1h(c.symbol, 200)
                since = c.created_at.replace(tzinfo=None) if c.created_at else None
                for b in bars:
                    t = dt.datetime.fromisoformat(str(b["time"])[:19]) if b.get("time") else None
                    if since and t and t <= since:
                        continue
                    hit_stop = b["low"] <= c.stop if c.side == "BUY" else b["high"] >= c.stop
                    hit_tp = b["high"] >= c.tp1 if c.side == "BUY" else b["low"] <= c.tp1
                    if hit_stop or hit_tp:
                        c.status = "broke" if hit_stop else "held"    # same bar → conservative
                        c.resolved_at = dt.datetime.now(dt.timezone.utc)
                        done += 1
                        if c.status == "held":                      # winners → flash card
                            try:
                                from services import flash_card as fc
                                if fc.ENABLED:
                                    await fc.post(c.symbol, c.side, c.entry, c.tp1, c.resolved_at)
                            except Exception:
                                pass
                        break
            await db.commit()
    except Exception:
        pass
    return {"resolved": done}


async def _record(sym: str, s: Dict) -> None:
    try:
        from database.db import AsyncSessionLocal
        from models.case_model import LiveCase
        async with AsyncSessionLocal() as db:
            import json
            votes = {v["agent"]: v["vote"] for v in (s.get("council") or {}).get("votes") or []}
            db.add(LiveCase(symbol=sym, side=s["side"], roles="|".join(s["roles"]), council=json.dumps(votes),
                            zone=f"{s['zone'][0]}-{s['zone'][1]}", entry=s["entry"], stop=s["stop"],
                            tp1=s["targets"][0]["price"], status="pending",
                            note=", ".join(s["members"])[:500]))
            await db.commit()
    except Exception:
        pass


async def scan(symbols: Optional[List[str]] = None, notify: bool = False) -> Dict:
    from services import yearly_cbdr as yc, case_library as cl, trade_gate as tg
    syms = symbols or [x.strip() for x in (DEFAULT_SYMBOLS + "," + STOCKS).split(",") if x.strip()]
    resolved = await resolve_pending()
    stats_all = await cl.all_cases()
    from services import council as _cn                   # grade agents once per scan
    seen, live = set(), []
    for c in stats_all:                                   # one entry per live shot (not per role)
        if c.get("source") == "live" and c.get("case_id") not in seen:
            seen.add(c.get("case_id"))
            live.append(c)
    agent_scores = _cn.agent_scores(live)
    out, sent = [], []
    for sym in syms:
        r = await yc.run(sym)
        if not r.get("ok"):
            out.append({"symbol": sym, "skip": r.get("reason")})
            continue
        tol = yc.tolerance(sym, r["price"])
        if not any(abs(c["mid"] - r["price"]) <= 3 * tol + (c["high"] - c["low"]) for c in r["clusters"]):
            out.append({"symbol": sym, "skip": "no golden cluster near price"})   # saves a credit
            continue
        bars = await _bars_1h(sym, tz="America/New_York")     # NY time → session levels too
        cands = candidates(r, bars)
        from services import session_levels as sl
        intraday = sl.all_levels(sym, bars)
        by_tf = {"1h": bars}
        if cands:                       # higher timeframes only when a zone is in play
            for tf, n in (("4h", 60), ("1day", 40), ("1week", 30)):
                by_tf[tf] = await _bars(sym, tf, n)
        for c in cands:
            tr = triggers(by_tf, c["side"], c["band"])
            sig = tr["primary"] or {"fired": False, "why": "; ".join(
                f"{t['tf']} {t['type']}: {t['why']}" for t in tr["all"][:3])}
            row = {"symbol": sym, "side": c["side"], "zone": [c["low"], c["high"]],
                   "members": c["members"], "roles": roles_of(c["members"]), "cisd": sig,
                   "trigger": sig.get("tf") and f"{sig['tf']} {sig['type'].upper()}",
                   "confirmations": [f"{t['tf']} {t['type'].upper()}" for t in tr["confirmations"]],
                   "intraday": sl.near(intraday, c["low"], c["high"], tol / 2)}
            if not sig["fired"]:
                row["held"] = sig["why"]
                out.append(row)
                continue
            buf = tol / 3
            stop = sig["extreme"] - buf if c["side"] == "BUY" else sig["extreme"] + buf
            stop = tg.widen_stop(sym, c["side"], sig["entry"], stop)
            tps = targets_from(r, c["side"], sig["entry"], stop)
            ptps = position_targets(r, c["side"], sig["entry"], stop)
            trade_type = "swing"
            # NEARER TARGETS: aim only as far as history usually travels (owner: still
            # trade the setup — just don't target what almost never happens)
            cap = realistic_cap(r, c["side"])
            tps = apply_cap(tps, cap, c["side"])
            ptps = apply_cap(ptps, cap, c["side"])
            held_reason = None
            # SWEET-SPOT LADDER: TP1 1R (⅓ + stop to BE) · TP2 2R (⅓) · TP3 inside the
            # 1σ–2σ expected move (68–95 % band) sized by this market's own volatility
            from services import tp_study as tps_mod
            closes = [b["close"] for b in by_tf.get("1day") or []]
            sig_d = tps_mod.sigma_daily(closes)
            unit = sig["entry"] * sig_d * (SWING_HORIZON_D ** 0.5) if sig_d else None
            best_k = None
            try:
                st = await tps_mod.study(sym, SWING_HORIZON_D)
                best_k = st and st.get("best_k")
            except Exception:
                st = None
            lad = tps_mod.ladder(c["side"], sig["entry"], stop, unit, best_k, structure=tps,
                                 cap_price=cap and cap["price"])
            sweet = {"zone": lad.get("sweet_zone"), "best_k": best_k, "unit": unit}
            if lad["targets"]:
                tps = lad["targets"]
            elif lad["hold"] and lad["hold"].startswith("stop wider") and not ptps:
                held_reason = lad["hold"]
            if not tps and not ptps and cap and not held_reason:
                from services.trade_gate import MIN_RR
                move = (cap["price"] - sig["entry"]) * (1 if c["side"] == "BUY" else -1)
                risk = abs(sig["entry"] - stop)
                if move > 0 and risk and move / risk >= MIN_RR:
                    t = {"name": "TP1", "price": cap["price"], "rr": round(move / risk, 2),
                         "pct": round(100 * move / sig["entry"], 1), "hist": cap["hist"],
                         "level": f"{cap['year']} {cap['k']:g} (realistic cap)"}
                    if t["pct"] <= MAX_TARGET_PCT * 100:
                        tps = [t]
                    else:
                        ptps = [{**t, "name": "P1"}]
                else:
                    held_reason = (f"realistic cap {cap['year']} {cap['k']:g} (hist {cap['hist']}%) "
                                   f"is under {MIN_RR}R from entry")
            if not tps and ptps:                # only yearly objectives → a POSITION trade
                tps, trade_type = ptps, "position"
                # bank ⅓ at 1R and move the stop to breakeven before the long hold
                one_r = sig["entry"] + (1 if c["side"] == "BUY" else -1) * abs(sig["entry"] - stop)
                if abs(one_r - sig["entry"]) < abs(tps[0]["price"] - sig["entry"]):
                    tps = [{"name": "TP1", "price": one_r, "rr": 1.0, "level": "1R"}] + tps
            stats = cl.score(stats_all, symbol=None)
            rs = cl.best_role(stats, row["roles"])
            weekly = await tg.weekly_profile(sym)
            g = tg.evaluate(sym, c["side"], sig["entry"], stop, [t["price"] for t in tps], weekly=weekly)
            if not tps:
                g["reasons"].append("no target ≥ 1.3R")
            if held_reason:
                g["reasons"].append(held_reason)
            if rs and rs["n"] >= 3 and rs["recent_rate"] < MIN_RATE:
                g["reasons"].append(f"role {rs['role']} recent hit-rate {rs['recent_rate']:.0%} < {MIN_RATE:.0%}")
            from services import council as cn
            cv = await cn.convene(sym, c["side"], yearly=r, role_stats=rs, trade_type=trade_type,
                                  scores=agent_scores)
            if cv["verdict"] != "GO":
                g["reasons"].append(f"council {cv['verdict']}: " + (cv.get("need") or
                                    (f"veto {','.join(cv['veto'])}" if cv["veto"] else f"score {cv['score']}")))
            if g["reasons"]:
                _held.add(f"{sym}:{c['side']}:{dt.date.today()}")      # watch it for promotion
            fit = account_fit(sym, sig["entry"], stop)
            if fit:
                g["reasons"].append(fit)
            g["allow"] = not g["reasons"]
            row.update(trade_type=trade_type, cap=cap, sweet=sweet,
                       position_targets=ptps if trade_type == "swing" else [],
                       entry=sig["entry"], extreme=sig["extreme"], stop=stop, targets=tps,
                       role_stats=rs, gate=g, council=cv)
            out.append(row)
        # one card per market + side: the best-backed shot (most council votes, most anchors)
        best: Dict[str, Dict] = {}
        for row in [x for x in out if x.get("symbol") == sym and x.get("gate", {}).get("allow")]:
            k = row["side"]
            score = (row["council"]["for"], len(row["members"]))
            if k not in best or score > (best[k]["council"]["for"], len(best[k]["members"])):
                best[k] = row
        for side, row in best.items():
            if notify:
                from services.realtime import alert_once
                key = f"sniper:{sym}:{side}:{dt.date.today()}"
                promoted = f"{sym}:{side}:{dt.date.today()}" in _held
                text = ("⬆️ *PROMOTED* — held earlier, now 4 agents agree\n" if promoted else "") + card(sym, row)
                if await alert_once(key, text, ttl_hours=24):
                    await _record(sym, row)
                    sent.append(sym)
    return {"resolved": resolved, "results": out, "notified": sent}
