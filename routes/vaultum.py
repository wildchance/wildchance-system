"""VAULTUM endpoints — the institutional feature-score board + risk gate (Phase 5/6/7/10).

Assembles the standardised gold-bias board from the reads the system already computes
(DXY regime, macro cycle, real rates, COT/liquidity, volatility regime, Venom AMD),
now with LIVE market-stress (VIX) + risk-appetite (SPX) feeds and a probabilistic HMM
regime folded into the macro-cycle score. Also exposes the portfolio VaR/ES risk gate.

Every engine/feed call is best-effort: a missing input degrades its score rather than
failing the board.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from database.db import get_db
from gold import vaultum_scores as vs

router = APIRouter(prefix="/vaultum", tags=["vaultum"])


async def _htf_bars():
    try:
        from services.ohlc_service import fetch_ohlc
        return await fetch_ohlc("XAU/USD", "4h", 60)
    except Exception:
        return None


async def _gather_scores() -> dict:
    scores: dict = {}
    bars = await _htf_bars()

    # --- DXY regime → dollar strength ---
    try:
        from gold import dxy as gdxy
        reg = {"sms": "weak", "bms": "strong"}.get(gdxy.dollar_regime().get("regime"), "neutral")
        scores["dollar_strength"] = vs.dollar_strength_score(regime=reg)
    except Exception:
        scores["dollar_strength"] = vs.dollar_strength_score()

    # --- macro cycle → gold bias + conviction; real-rate direction; liquidity ---
    real_rate_dir = None
    try:
        from gold import macro_cycle as mc
        rr = mc.regime_read()
        mag = abs(rr.get("confluence_score", 0))
        conviction = "high" if mag >= 3 else "medium" if mag == 2 else "low"
        scores["macro_cycle"] = vs.macro_cycle_score(gold_bias=rr.get("gold_bias"),
                                                     conviction=conviction)
        try:
            real_rate_dir = mc.INPUTS.get("real_rate_direction")
        except Exception:
            real_rate_dir = None
        liq = (rr.get("liquidity") or {}).get("state")
        scores["liquidity"] = vs.liquidity_score(
            direction={"impaired": "contracting", "normal": "expanding"}.get(liq))
    except Exception:
        scores["macro_cycle"] = vs.macro_cycle_score()
        scores["liquidity"] = vs.liquidity_score()

    # --- HMM probabilistic regime folded into macro_cycle (Phase 6) ---
    hmm = None
    try:
        from gold.hmm_regime import regime_hmm
        if bars and len(bars) >= 21:
            hmm = regime_hmm(bars)
            if hmm.get("available"):
                mc_env = scores["macro_cycle"]
                hmm_val = {"long": 68.0, "short": 32.0, "neutral": 50.0}[hmm["gold_bias"]]
                agree = (hmm_val - 50) * (mc_env["value"] - 50) >= 0
                scores["macro_cycle"] = {
                    **mc_env,
                    "value": round(mc_env["value"] * 0.6 + hmm_val * 0.4, 1),
                    "confidence": round(min(1.0, mc_env["confidence"] * (1.15 if agree else 0.9)), 2),
                    "drivers": mc_env["drivers"] + [hmm["explanation"]],
                }
    except Exception:
        hmm = None

    scores["inflation_pressure"] = vs.inflation_pressure_score(
        direction=None,
        real_rate_direction=("rising" if (real_rate_dir or "").startswith("rising")
                             else "falling" if real_rate_dir else None))

    # --- volatility regime ---
    try:
        from gold import volatility as gv
        vr = gv.vol_regime(bars) if bars and len(bars) >= 15 else {}
        atrp = gv.atr_percentile(bars) if bars and len(bars) >= 15 else None
        scores["vol_regime"] = vs.vol_regime_score(regime=vr.get("regime"), atr_pct=atrp)
    except Exception:
        scores["vol_regime"] = vs.vol_regime_score()

    # --- Venom AMD phase ---
    try:
        from gold.venom import venom_read
        scores["venom_phase"] = vs.venom_phase_score(venom_read())
    except Exception:
        scores["venom_phase"] = vs.venom_phase_score()

    # --- FREE macro feeds (Yahoo + alternative.me + GDELT) — no TwelveData quota ---
    # market stress (VIX), risk appetite (SPX + QQQ/XLP regime + Fear&Greed),
    # JPY-carry liquidity, geopolitical haven, and central-bank divergence.
    fm = {}
    try:
        from services.free_macro_feeds import free_macro
        fm = await free_macro()
    except Exception:
        fm = {}
    # fall back to the TwelveData risk_feeds only if the free VIX/SPX came back empty
    if fm.get("vix") is None or fm.get("spx_change_pct") is None:
        try:
            from services.risk_feeds import risk_feeds
            rf = await risk_feeds()
            fm.setdefault("vix", rf.get("vix"))
            fm.setdefault("spx_change_pct", rf.get("spx_change_pct"))
            fm.setdefault("risk_state", rf.get("risk_state"))
        except Exception:
            pass

    dollar_reg = "strong" if scores.get("dollar_strength", {}).get("value", 50) < 40 else None
    scores["market_stress"] = vs.market_stress_score(vix=fm.get("vix"), dollar_regime=dollar_reg)
    scores["risk_appetite"] = vs.risk_appetite_score(state=fm.get("risk_state"),
                                                     equity_change_pct=fm.get("spx_change_pct"))
    scores["jpy_liquidity"] = vs.jpy_liquidity_score(fm.get("jpy_usd_roc_30d"))
    scores["geopolitical"] = vs.geopolitical_score(fm.get("geopolitical_risk"))
    scores["cb_divergence"] = vs.cb_divergence_score(
        (fm.get("cb_divergence") or {}).get("fed_minus_peers"))
    return scores, hmm


@router.get("/scores")
async def scores():
    """The full VAULTUM feature-score board — every standardised 0-100 score (with its
    explainability envelope), the HMM regime, plus the blended GOLD BIAS & CONVICTION."""
    sc, hmm = await _gather_scores()
    board = vs.gold_bias_board(sc)
    board["hmm_regime"] = hmm
    board["display"] = vs.format_board(board)
    return board


@router.get("/bias")
async def bias():
    """Compact gate — direction + conviction only, for the Autobots to read."""
    sc, _ = await _gather_scores()
    b = vs.gold_bias_board(sc)
    return {"direction": b["direction"], "gold_bias": b["gold_bias"],
            "conviction_pct": b["conviction_pct"], "confidence": b["confidence"],
            "tag": b["tag"], "top_drivers": b["top_drivers"]}


@router.get("/regime")
async def regime(states: int = Query(3, ge=2, le=3)):
    """The probabilistic HMM regime read (Phase 6) on the 4H gold series."""
    from gold.hmm_regime import regime_hmm
    bars = await _htf_bars()
    if not bars or len(bars) < 21:
        return {"available": False, "reason": "insufficient bars"}
    return regime_hmm(bars, k=states)


async def _daily_returns(n: int = 40):
    try:
        from services.ohlc_service import fetch_ohlc
        bars = await fetch_ohlc("XAU/USD", "1day", n)
        closes = [float(b[4]) for b in bars] if bars else []
        return [(b - a) / a for a, b in zip(closes, closes[1:]) if a]
    except Exception:
        return []


@router.get("/portfolio-risk")
async def portfolio_risk_endpoint(equity: float = Query(..., description="account equity USD"),
                                  limit_pct: float = Query(5.0),
                                  conf: float = Query(0.95),
                                  db: AsyncSession = Depends(get_db)):
    """Portfolio VaR / Expected-Shortfall (Phase 10) across the open gold book + the
    APPROVE/BLOCK verdict against the risk budget."""
    from gold import portfolio_risk as pr
    positions = []
    try:
        from services.gold_positions import list_positions, _to_dict
        rows = await list_positions(db, status="open")
        for r in rows:
            d = _to_dict(r) if not isinstance(r, dict) else r
            positions.append({"side": d.get("side", d.get("trade_side", "buy")),
                              "lot": d.get("lot", d.get("size", 0.01)),
                              "entry": d.get("entry", d.get("entry_price")),
                              "price": d.get("current_price", d.get("entry", d.get("entry_price")))})
    except Exception:
        pass
    returns = await _daily_returns()
    return pr.risk_gate(positions, equity=equity, returns=returns,
                        limit_pct=limit_pct, conf=conf)


@router.get("/feeds")
async def feeds():
    """Diagnostic — the FREE macro feeds (Yahoo/alternative.me/GDELT, no key/quota) plus
    the TwelveData fallback probe, so you can verify every risk score is live."""
    out = {}
    try:
        from services.free_macro_feeds import free_feeds_diagnostic
        out["free"] = await free_feeds_diagnostic()
    except Exception as e:
        out["free"] = {"error": str(e)}
    try:
        from services.risk_feeds import feeds_diagnostic
        out["twelvedata_fallback"] = await feeds_diagnostic()
    except Exception:
        pass
    return out


@router.get("/policy-rates")
async def policy_rates(live: bool = Query(False, description="pull the latest rates from BIS")):
    """Central-bank policy-rate map + the Fed-vs-peers divergence (the cb_divergence score
    input). Pass live=true to refresh from the free BIS WS_CBPOL feed (degrades to the
    encoded map on outage)."""
    from services import free_macro_feeds as fmf
    out = {}
    if live:
        out["bis"] = await fmf.bis_policy_rates(apply=True)
    out["rates"] = dict(fmf.POLICY_RATES)
    out["divergence"] = fmf.cb_divergence()
    return out


@router.post("/policy-rates")
async def set_policy_rates_ep(rates: dict):
    """Operator override of the policy-rate map — feed today's rates, e.g.
    {"FED":4.50,"ECB":2.15,"BOJ":0.50}. Returns the updated map + divergence."""
    from services import free_macro_feeds as fmf
    updated = fmf.set_policy_rates(**{k: v for k, v in (rates or {}).items()})
    return {"rates": updated, "divergence": fmf.cb_divergence()}


@router.get("/allocate")
async def allocate(entry: float = Query(..., description="signal entry price"),
                   stop: float = Query(..., description="signal stop price"),
                   conviction: float = Query(None, description="0-100; live VAULTUM bias if omitted"),
                   budget_pct: float = Query(3.0, description="aggregate fleet risk budget %"),
                   max_risk_pct: float = Query(2.0)):
    """Phase 11 — conviction-scaled, risk-budgeted allocation across the fleet accounts.
    Uses the live VAULTUM conviction unless one is passed."""
    from gold import portfolio_opt as po
    from services import trade_executor as te
    conv = conviction
    if conv is None:
        try:
            sc, _ = await _gather_scores()
            conv = vs.gold_bias_board(sc)["conviction_pct"]
        except Exception:
            conv = 60.0
    accounts = te.fleet_accounts()
    out = po.optimise(accounts, entry=entry, stop=stop, conviction_pct=conv,
                      budget_pct=budget_pct, max_risk_pct=max_risk_pct)
    out["conviction_source"] = "live VAULTUM board" if conviction is None else "override"
    return out


@router.get("/readiness")
async def readiness(db: AsyncSession = Depends(get_db)):
    """Go-live preflight — execution mode, fleet config, VaR-gate status, feed status,
    and pending-order count. The checklist before flipping EXECUTION_ENABLED."""
    from services import trade_executor as te
    checks = {}
    checks["execution_enabled"] = te.EXECUTION_ENABLED
    checks["fleet_enabled"] = te.FLEET_ENABLED
    checks["fleet_accounts"] = len(te.fleet_accounts())
    checks["var_gate_enabled"] = te.PORTFOLIO_VAR_GATE_ENABLED
    checks["var_gate_equity_set"] = te.PORTFOLIO_EQUITY_USD > 0
    checks["var_limit_pct"] = te.PORTFOLIO_VAR_LIMIT_PCT
    try:
        from services.risk_feeds import feeds_diagnostic
        fd = await feeds_diagnostic()
        checks["market_stress_feed_live"] = fd["market_stress_live"]
        checks["risk_appetite_feed_live"] = fd["risk_appetite_live"]
    except Exception:
        checks["market_stress_feed_live"] = checks["risk_appetite_feed_live"] = None
    try:
        pending = await te.pending(db, limit=100)
        checks["pending_orders"] = len(pending)
    except Exception:
        checks["pending_orders"] = None
    mode = "LIVE" if te.EXECUTION_ENABLED else "PAPER"
    gate = ("armed" if (te.PORTFOLIO_VAR_GATE_ENABLED and te.PORTFOLIO_EQUITY_USD > 0)
            else "dormant")
    return {"mode": mode, "var_gate": gate, "checks": checks,
            "ready_to_go_live": bool(te.FLEET_ENABLED and checks["fleet_accounts"] > 0),
            "note": (f"{mode} mode; VaR gate {gate}. Flip EXECUTION_ENABLED + FLEET_ENABLED "
                     "(and arm the VaR gate) once the MT5 VPS bridge is up.")}


@router.get("/dashboard.json")
async def dashboard_json(db: AsyncSession = Depends(get_db)):
    """One aggregated read for the live status page — bias, free feeds, execution mode +
    queued orders, and open positions. Every section is best-effort so a single dead feed
    never blanks the board."""
    import datetime as _dt
    from services import trade_executor as te
    out: dict = {"ts": _dt.datetime.now(_dt.timezone.utc).isoformat()}

    try:
        sc, hmm = await _gather_scores()
        b = vs.gold_bias_board(sc)
        out["bias"] = {"direction": b["direction"], "gold_bias": b["gold_bias"],
                       "conviction_pct": b["conviction_pct"], "confidence": b["confidence"],
                       "tag": b["tag"], "top_drivers": b["top_drivers"]}
    except Exception as e:
        out["bias"] = {"error": str(e)}
    try:
        from services.free_macro_feeds import free_macro
        out["feeds"] = await free_macro()
    except Exception as e:
        out["feeds"] = {"error": str(e)}
    try:
        pend = await te.pending(db, limit=100)
        out["execution"] = {"mode": "LIVE" if te.EXECUTION_ENABLED else "PAPER",
                            "execution_enabled": te.EXECUTION_ENABLED,
                            "fleet_enabled": te.FLEET_ENABLED,
                            "queued_orders": len(pend)}
        out["orders"] = await te.recent(db, 15)
    except Exception as e:
        out["execution"] = {"error": str(e)}
        out["orders"] = []
    try:
        from services import gold_positions as gp
        out["positions"] = await gp.list_positions(db, status="OPEN", limit=25)
    except Exception as e:
        out["positions"] = []
        out["positions_error"] = str(e)
    return out


@router.get("/dashboard")
async def dashboard():
    """Live VAULTUM status page — bias, feeds, queued orders and open positions in one
    view, polling /vaultum/dashboard.json so you're not hitting five endpoints. Open it
    in a browser; it refreshes itself."""
    from fastapi.responses import HTMLResponse
    return HTMLResponse(_DASHBOARD_HTML)


_DASHBOARD_HTML = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>VAULTUM — live</title><style>
:root{color-scheme:dark}body{margin:0;background:#0b0e14;color:#e6edf3;
font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}
header{padding:16px 20px;border-bottom:1px solid #1c2430;display:flex;
justify-content:space-between;align-items:center}
h1{font-size:16px;margin:0;letter-spacing:.14em;color:#f5c451}
#ts{color:#6b7686;font-size:12px}
main{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px;padding:16px}
.card{background:#111722;border:1px solid #1c2430;border-radius:10px;padding:14px}
.card h2{font-size:12px;letter-spacing:.1em;color:#8b95a5;margin:0 0 10px;text-transform:uppercase}
.big{font-size:26px;font-weight:700}
.up{color:#3fb950}.down{color:#f85149}.flat{color:#d29922}
table{width:100%;border-collapse:collapse;font-size:12.5px}
td,th{text-align:left;padding:4px 6px;border-bottom:1px solid #1c2430}
th{color:#6b7686;font-weight:600}.muted{color:#6b7686}
.pill{display:inline-block;padding:2px 8px;border-radius:999px;font-size:11px;font-weight:700}
.live{background:#12261a;color:#3fb950}.paper{background:#2a2410;color:#d29922}
.row{display:flex;justify-content:space-between;padding:3px 0}
</style></head><body>
<header><h1>VAULTUM · LIVE</h1><span id=ts>loading…</span></header>
<main>
<div class=card id=bias><h2>Gold bias</h2><div class=muted>…</div></div>
<div class=card id=exec><h2>Execution</h2><div class=muted>…</div></div>
<div class=card id=feeds><h2>Free feeds</h2><div class=muted>…</div></div>
<div class=card id=orders><h2>Queued orders</h2><div class=muted>…</div></div>
<div class=card id=positions><h2>Open positions</h2><div class=muted>…</div></div>
</main>
<script>
const dir=v=>v>55?'up':v<45?'down':'flat';
const cls=x=>x==null?'flat':x>0?'up':x<0?'down':'flat';
const f=(x,d=2)=>x==null?'—':(+x).toFixed(d);
async function tick(){
 let d;try{d=await(await fetch('dashboard.json',{cache:'no-store'})).json()}catch(e){return}
 document.getElementById('ts').textContent=new Date(d.ts).toLocaleTimeString();
 const b=d.bias||{};
 document.getElementById('bias').innerHTML=`<h2>Gold bias</h2>`+(b.error?`<div class=down>${b.error}</div>`:
  `<div class="big ${dir(b.conviction_pct)}">${b.direction||'—'}</div>
   <div class=row><span class=muted>conviction</span><b>${f(b.conviction_pct,0)}%</b></div>
   <div class=row><span class=muted>confidence</span><b>${f(b.confidence,2)}</b></div>
   <div class=row><span class=muted>tag</span><b>${b.tag||'—'}</b></div>
   <div class=muted style=margin-top:8px>${(b.top_drivers||[]).slice(0,3).join(' · ')}</div>`);
 const e=d.execution||{};
 document.getElementById('exec').innerHTML=`<h2>Execution</h2>`+(e.error?`<div class=down>${e.error}</div>`:
  `<div class=big><span class="pill ${e.execution_enabled?'live':'paper'}">${e.mode}</span></div>
   <div class=row><span class=muted>fleet</span><b>${e.fleet_enabled?'on':'off'}</b></div>
   <div class=row><span class=muted>queued orders</span><b>${e.queued_orders}</b></div>`);
 const m=d.feeds||{};
 document.getElementById('feeds').innerHTML=`<h2>Free feeds</h2>`+(m.error?`<div class=down>${m.error}</div>`:
  `<div class=row><span class=muted>VIX</span><b>${f(m.vix,1)}</b></div>
   <div class=row><span class=muted>SPX %</span><b class="${cls(m.spx_change_pct)}">${f(m.spx_change_pct,2)}</b></div>
   <div class=row><span class=muted>risk state</span><b>${m.risk_state||'—'}</b></div>
   <div class=row><span class=muted>JPY 30d %</span><b>${f(m.jpy_usd_roc_30d,2)}</b></div>
   <div class=row><span class=muted>Fear&Greed</span><b>${m.fear_greed??'—'}</b></div>
   <div class=row><span class=muted>geo risk</span><b>${f(m.geopolitical_risk,1)}</b></div>
   <div class=row><span class=muted>Fed−peers</span><b>${f((m.cb_divergence||{}).fed_minus_peers,2)}</b></div>`);
 const o=d.orders||[];
 document.getElementById('orders').innerHTML=`<h2>Queued orders (${o.length})</h2>`+(o.length?
  `<table><tr><th>side</th><th>role</th><th>vol</th><th>sl</th><th>tp</th><th>status</th></tr>`+
   o.map(x=>`<tr><td class="${x.side=='sell'?'down':'up'}">${x.side}</td><td>${x.scale_role||'—'}</td>
   <td>${f(x.volume,2)}</td><td>${f(x.sl,2)}</td><td>${f(x.tp,2)}</td><td class=muted>${x.status}</td></tr>`).join('')+
   `</table>`:`<div class=muted>none</div>`);
 const p=d.positions||[];
 document.getElementById('positions').innerHTML=`<h2>Open positions (${p.length})</h2>`+(p.length?
  `<table><tr><th>side</th><th>entry</th><th>stop</th><th>lot</th><th>src</th></tr>`+
   p.map(x=>`<tr><td class="${(x.side||'').toLowerCase().includes('s')?'down':'up'}">${x.side}</td>
   <td>${f(x.entry,2)}</td><td>${f(x.stop,2)}</td><td>${f(x.lot,2)}</td><td class=muted>${x.source||'—'}</td></tr>`).join('')+
   `</table>`:`<div class=muted>flat</div>`);
}
tick();setInterval(tick,30000);
</script></body></html>"""
