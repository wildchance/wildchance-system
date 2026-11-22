"""Prop-firm equity kill-switch for the MT5 VPS connector (stdlib only).

Mirrors propfirm/challenge.py so the app, the bridge and the EA share one rule:
  • DAILY guard  — equity ≤ day_ref − initial × daily_loss × daily_buffer
                   (day_ref = max(balance, equity) at server-day start, FundingPips)
  • MAX guard    — equity ≤ initial × (1 − max_loss × max_buffer)   → permanent stop
  • TARGET lock  — equity ≥ initial × (1 + phase target)            → stop, phase passed

State persists to a JSON file so a connector RESTART cannot reset the starting
balance or the day reference (a restart-reset is how EAs silently loosen the
max-drawdown line). Env (all optional):
  CHALLENGE_INITIAL_BALANCE  the challenge's starting balance (recommended — set it!)
  CHALLENGE_DAILY_LOSS=0.05  CHALLENGE_MAX_LOSS=0.10  CHALLENGE_TARGET=0.08 (0 = off)
  GUARD_DAILY_BUFFER=0.70    GUARD_MAX_BUFFER=0.80    GUARD_STATE_FILE=guard_state.json
"""

from __future__ import annotations

import json
import os
from typing import Dict, Optional


def _f(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, default))
    except (TypeError, ValueError):
        return float(default)


class EquityGuard:
    def __init__(self, initial_balance: Optional[float] = None, daily_loss: float = 0.05,
                 max_loss: float = 0.10, target: float = 0.08, daily_buffer: float = 0.70,
                 max_buffer: float = 0.80, state_file: Optional[str] = None):
        self.daily_loss, self.max_loss, self.target = daily_loss, max_loss, target
        self.daily_buffer, self.max_buffer = daily_buffer, max_buffer
        self.state_file = state_file
        self.state: Dict = {"initial_balance": initial_balance, "day_key": None,
                            "day_ref": None, "max_locked": False, "target_locked": False,
                            "daily_locked_day": None}
        self._load()
        if initial_balance:                     # explicit env/arg always wins
            self.state["initial_balance"] = float(initial_balance)

    @classmethod
    def from_env(cls) -> "EquityGuard":
        ib = os.environ.get("CHALLENGE_INITIAL_BALANCE")
        return cls(initial_balance=float(ib) if ib else None,
                   daily_loss=_f("CHALLENGE_DAILY_LOSS", 0.05),
                   max_loss=_f("CHALLENGE_MAX_LOSS", 0.10),
                   target=_f("CHALLENGE_TARGET", 0.08),
                   daily_buffer=_f("GUARD_DAILY_BUFFER", 0.70),
                   max_buffer=_f("GUARD_MAX_BUFFER", 0.80),
                   state_file=os.environ.get("GUARD_STATE_FILE", "guard_state.json"))

    # -- persistence ---------------------------------------------------------------
    def _load(self) -> None:
        if not self.state_file or not os.path.exists(self.state_file):
            return
        try:
            with open(self.state_file) as fh:
                self.state.update(json.load(fh))
        except Exception:
            pass

    def _save(self) -> None:
        if not self.state_file:
            return
        try:
            tmp = self.state_file + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(self.state, fh)
            os.replace(tmp, self.state_file)
        except Exception:
            pass

    # -- the verdict ---------------------------------------------------------------
    def check(self, balance: float, equity: float, day_key: str) -> Dict:
        """day_key: the broker SERVER date (YYYY-MM-DD) — firms reset on server midnight."""
        st = self.state
        if not st.get("initial_balance"):
            st["initial_balance"] = float(balance)        # first run without env
        if st.get("day_key") != day_key:
            st["day_key"] = day_key
            st["day_ref"] = max(float(balance), float(equity))
        ib, ref = float(st["initial_balance"]), float(st["day_ref"])
        daily_floor = ref - ib * self.daily_loss * self.daily_buffer
        max_floor = ib * (1 - self.max_loss * self.max_buffer)
        target_eq = ib * (1 + self.target) if self.target > 0 else None

        if st.get("max_locked") or equity <= max_floor:
            st["max_locked"] = True
            action = "MAX_LOCK"
        elif st.get("target_locked") or (target_eq is not None and equity >= target_eq):
            st["target_locked"] = True
            action = "TARGET_LOCK"
        elif st.get("daily_locked_day") == day_key or equity <= daily_floor:
            st["daily_locked_day"] = day_key
            action = "DAILY_LOCK"
        else:
            action = "TRADE"
        self._save()
        return {"action": action, "equity": round(equity, 2),
                "daily_floor": round(daily_floor, 2), "max_floor": round(max_floor, 2),
                "target_equity": round(target_eq, 2) if target_eq else None,
                "initial_balance": ib, "day_ref": round(ref, 2)}
