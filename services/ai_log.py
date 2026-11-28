"""AI call log — the "data flywheel" input (NVIDIA's model-distillation blueprint).

Every NVIDIA chat call is kept here: job (fast/deep), model, prompt, reply, latency.
Exported as JSONL (/ops/ai/log?format=jsonl) it is exactly what NeMo Customizer needs
to fine-tune a small model on our own traffic later — big-model replies are the labels.
Kept in memory and flushed to data_store every 25 calls (newest MAX_ROWS rows).
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional

KEY = "ai_calls"
MAX_ROWS = 3000
FLUSH_EVERY = 25
_rows: List[Dict] = []
_loaded = {"done": False}
_pending = {"n": 0}


def _trim(messages: List[Dict]) -> List[Dict]:
    return [{"role": m.get("role"), "content": str(m.get("content") or "")[:4000]} for m in messages]


async def _ensure_loaded() -> None:
    if _loaded["done"]:
        return
    _loaded["done"] = True
    try:
        from services import data_store
        v, _ = await data_store.get(KEY)
        if v:
            _rows[:0] = list(v)[-MAX_ROWS:]
    except Exception:
        pass


async def record(role: str, model: str, messages: List[Dict], reply: Optional[str], ms: float) -> None:
    """Never raises — logging must not break an AI call."""
    try:
        await _ensure_loaded()
        _rows.append({"t": round(time.time()), "job": role, "model": model, "ms": round(ms),
                      "messages": _trim(messages), "reply": (reply or "")[:4000]})
        del _rows[:-MAX_ROWS]
        _pending["n"] += 1
        if _pending["n"] >= FLUSH_EVERY:
            await flush()
    except Exception:
        pass


async def flush() -> None:
    from services import data_store
    _pending["n"] = 0
    await data_store.put(KEY, _rows[-MAX_ROWS:])


async def rows(limit: int = 200) -> List[Dict]:
    await _ensure_loaded()
    return _rows[-max(1, min(limit, MAX_ROWS)):]


def stats() -> Dict:
    by: Dict[str, Dict] = {}
    for r in _rows:
        b = by.setdefault(f"{r['job']}:{r['model']}", {"calls": 0, "ms": 0})
        b["calls"] += 1
        b["ms"] += r.get("ms", 0)
    return {k: {"calls": v["calls"], "avg_ms": round(v["ms"] / v["calls"])} for k, v in by.items()}
