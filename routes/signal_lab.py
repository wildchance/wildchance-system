"""Signal lab — AI-discovered signals on our 15 markets (ops token required).

  GET  /signal-lab                 accepted signals + today's BUY/SELL tilt per market
  POST /signal-lab/run?theme=...   start a discovery run in the background (rounds 1..5)
  GET  /signal-lab/status          the running / last run
  POST /signal-lab/score?formula=  score one formula by hand (in / out-of-sample rank IC)
  POST /signal-lab/remove?formula= drop an accepted signal
"""

import asyncio
import time

from fastapi import APIRouter

router = APIRouter(prefix="/signal-lab", tags=["signal-lab"])
_state = {"running": False, "started": None, "theme": None, "result": None}
_bg: set = set()


@router.get("")
async def overview():
    from services import signal_lab as sl
    return {"signals": await sl.load(), "tilts": await sl.tilts(), "spotter_min_tilt": sl.SPOT_TILT,
            "universe": sl.UNIVERSE, "min_ic": sl.MIN_IC, "min_t": sl.MIN_T}


@router.post("/run")
async def run(theme: str = "momentum and mean reversion", rounds: int = 3, per_round: int = 5):
    if _state["running"]:
        return {"ok": False, "reason": "a run is already going", "started": _state["started"]}
    from services import signal_lab as sl

    async def job():
        try:
            _state["result"] = await sl.run(theme, rounds=rounds, per_round=per_round)
        except Exception as e:                       # never leave the flag stuck
            _state["result"] = {"ok": False, "reason": f"{type(e).__name__}: {e}"}
        finally:
            _state["running"] = False
            try:
                r = _state["result"] or {}
                from services.owner_notify import send
                await send(f"🧪 Signal lab '{theme}': tried {r.get('tried', 0)}, accepted "
                           f"{len(r.get('accepted') or [])}, library {r.get('saved_total', 0)}"
                           if r.get("ok") else f"🧪 Signal lab failed: {r.get('reason')}")
            except Exception:
                pass

    _state.update(running=True, started=time.time(), theme=theme, result=None)
    t = asyncio.create_task(job())
    _bg.add(t)
    t.add_done_callback(_bg.discard)
    return {"ok": True, "started": theme, "check": "/signal-lab/status"}


@router.get("/status")
async def status():
    return _state


@router.post("/score")
async def score(formula: str):
    from services import signal_lab as sl
    _, syms, fields = await sl.panel()
    return {"markets": syms, **sl.score(formula, fields)}


@router.post("/remove")
async def remove(formula: str):
    from services import data_store, signal_lab as sl
    keep = [s for s in await sl.load() if s["formula"] != formula]
    await data_store.put(sl.KEY, keep)
    sl._tilt_cache.update(t=0.0, v=None)
    return {"ok": True, "left": len(keep)}
