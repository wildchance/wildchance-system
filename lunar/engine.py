"""Lunar-cycle regime engine — the WildChance New-Moon-long / Full-Moon-short overlay.

Pure + deterministic (no I/O). Phase times use Meeus, *Astronomical Algorithms*
ch. 49 (true New/Full Moon with the main periodic terms) — accurate to a few
minutes, versus up to ~14h error from the naive mean-synodic-month formula. That
matters because the observation windows are only ±24–36h wide.

Rule set (the user's v3.1 lunar swing rules):
  New Moon  → Waxing  (age 0 … Full):  LONG bias,  max hold NEW_MOON_HOLD_DAYS
  Full Moon → Waning  (Full … next New): SHORT bias, max hold FULL_MOON_HOLD_DAYS
  ±obs_hours around either syzygy = OBSERVATION window (pause or half risk)

HONESTY NOTE: lunar phase is NOT an established directional edge. It is wired in
as an OPTIONAL gate (default OFF in execution) — run ``phase_returns`` on your own
history (GET /lunar/backtest) and only enable LUNAR_GATE_ENABLED if it holds up.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence, Tuple

SYNODIC_MONTH = 29.530588853
_JD_UNIX_EPOCH = 2440587.5
_DELTA_T_DAYS = 69.0 / 86400.0        # TT − UTC (≈69 s in the 2020s)


def _jd_to_dt(jde: float) -> datetime:
    return datetime.fromtimestamp((jde - _DELTA_T_DAYS - _JD_UNIX_EPOCH) * 86400.0,
                                  tz=timezone.utc)


def _dt_to_k(dt: datetime) -> float:
    year = dt.year + (dt.timetuple().tm_yday - 0.5) / 365.25
    return (year - 2000.0) * 12.3685


def true_phase(k: float) -> datetime:
    """UTC time of the true New (k integer) or Full (k = n + 0.5) Moon."""
    t = k / 1236.85
    jde = (2451550.09766 + 29.530588861 * k + 0.00015437 * t * t
           - 0.000000150 * t ** 3 + 0.00000000073 * t ** 4)
    e = 1 - 0.002516 * t - 0.0000074 * t * t
    r = math.radians
    m = r(2.5534 + 29.10535670 * k - 0.0000014 * t * t - 0.00000011 * t ** 3)
    mp = r(201.5643 + 385.81693528 * k + 0.0107582 * t * t + 0.00001238 * t ** 3
           - 0.000000058 * t ** 4)
    f = r(160.7108 + 390.67050284 * k - 0.0016118 * t * t - 0.00000227 * t ** 3
          + 0.000000011 * t ** 4)
    om = r(124.7746 - 1.56375588 * k + 0.0020672 * t * t + 0.00000215 * t ** 3)
    full = abs(k - math.floor(k) - 0.5) < 1e-6
    c1, c2, c3, c4, c5 = ((-0.40614, 0.17302, 0.01614, 0.01043, 0.00734) if full
                          else (-0.40720, 0.17241, 0.01608, 0.01039, 0.00739))
    s = math.sin
    corr = (c1 * s(mp) + c2 * e * s(m) + c3 * s(2 * mp) + c4 * s(2 * f)
            + c5 * e * s(mp - m) - 0.00515 * e * s(mp + m) + 0.00209 * e * e * s(2 * m)
            - 0.00111 * s(mp - 2 * f) - 0.00057 * s(mp + 2 * f)
            + 0.00056 * e * s(2 * mp + m) - 0.00042 * s(3 * mp)
            + 0.00042 * e * s(m + 2 * f) + 0.00038 * e * s(m - 2 * f)
            - 0.00024 * e * s(2 * mp - m) - 0.00017 * s(om))
    return _jd_to_dt(jde + corr)


def _bracketing_new_moons(now: datetime) -> Tuple[datetime, datetime, datetime]:
    """(previous New Moon, the Full Moon between, next New Moon) around ``now``."""
    k = math.floor(_dt_to_k(now)) + 1
    while true_phase(k) > now:
        k -= 1
    while true_phase(k + 1) <= now:
        k += 1
    return true_phase(k), true_phase(k + 0.5), true_phase(k + 1)


def _aware(dt: Optional[datetime]) -> datetime:
    now = dt or datetime.now(timezone.utc)
    return now if now.tzinfo else now.replace(tzinfo=timezone.utc)


def lunar_state(dt: Optional[datetime] = None, observation_hours: float = 24.0,
                new_moon_hold_days: int = 14, full_moon_hold_days: int = 12) -> Dict:
    """Phase, bias and observation-window state at ``dt`` (default now, UTC)."""
    now = _aware(dt)
    prev_new, full, next_new = _bracketing_new_moons(now)
    hrs = lambda a, b: (a - b).total_seconds() / 3600.0
    obs = float(observation_hours)
    near_new = hrs(now, prev_new) <= obs or hrs(next_new, now) <= obs
    near_full = abs(hrs(now, full)) <= obs
    age = hrs(now, prev_new) / 24.0
    waxing = now < full
    if waxing:
        bias, hold = "BUY", new_moon_hold_days
        phase = ("NEW_MOON_OBSERVATION" if near_new else
                 "WAXING_CRESCENT" if age < SYNODIC_MONTH / 4 else "WAXING_GIBBOUS")
        next_pivot, next_name = full, "FULL_MOON"
    else:
        bias, hold = "SELL", full_moon_hold_days
        third_q = full + (next_new - full) / 2
        phase = ("FULL_MOON_OBSERVATION" if near_full else
                 "NEW_MOON_OBSERVATION" if near_new else
                 "WANING_GIBBOUS" if now < third_q else "WANING_CRESCENT")
        next_pivot, next_name = next_new, "NEW_MOON"
    in_obs = near_new or near_full
    return {
        "timestamp_utc": now.isoformat(),
        "age_days": round(age, 2),
        "cycle_progress_pct": round(100 * age / SYNODIC_MONTH, 1),
        "phase": phase,
        "allowed_direction": bias,
        "in_observation_window": in_obs,
        "risk_multiplier": 0.5 if in_obs else 1.0,
        "max_hold_days": hold,
        "last_new_moon_utc": prev_new.isoformat(timespec="minutes"),
        "full_moon_utc": full.isoformat(timespec="minutes"),
        "next_new_moon_utc": next_new.isoformat(timespec="minutes"),
        "next_pivot": next_name,
        "hours_to_next_pivot": round(hrs(next_pivot, now), 1),
    }


def evaluate_alignment(direction: str, dt: Optional[datetime] = None,
                       observation_hours: float = 24.0,
                       pause_during_observation: bool = False) -> Dict:
    """Is a BUY/SELL setup aligned with the active lunar half-cycle?"""
    st = lunar_state(dt, observation_hours)
    d = (direction or "").upper().strip()
    d = {"LONG": "BUY", "SHORT": "SELL"}.get(d, d)
    if d not in ("BUY", "SELL"):
        return {"allowed": False, "reason": f"invalid direction {direction!r}", "lunar": st}
    if st["in_observation_window"] and pause_during_observation:
        return {"allowed": False, "reason": f"LUNAR_OBSERVATION_PAUSE ({st['phase']})",
                "lunar": st}
    if d != st["allowed_direction"]:
        return {"allowed": False,
                "reason": f"LUNAR_BIAS_MISMATCH: {d} vs {st['allowed_direction']}-only ({st['phase']})",
                "lunar": st}
    return {"allowed": True, "reason": "ALIGNED", "risk_multiplier": st["risk_multiplier"],
            "max_hold_days": st["max_hold_days"], "lunar": st}


def phase_returns(bars: Sequence[Tuple], hold_days: int = 14) -> Dict:
    """Evidence check: does the lunar rule beat chance on THIS instrument?

    bars: daily (date, open, high, low, close) oldest-first (ohlc_service.fetch_ohlc).
    For each New Moon: return of a LONG held ``hold_days``; for each Full Moon: a
    SHORT. Compared to the average same-length return from ALL start days (the
    baseline any directional rule must beat)."""
    if len(bars) < hold_days + 40:
        return {"ok": False, "reason": "not enough daily history",
                "bars": len(bars)}
    dates = [b[0] for b in bars]
    closes = [float(b[4]) for b in bars]
    idx = {d: i for i, d in enumerate(dates)}

    def fwd(i: int) -> Optional[float]:
        j = i + hold_days
        return (closes[j] - closes[i]) / closes[i] if j < len(closes) else None

    def first_index_on_or_after(day) -> Optional[int]:
        for off in range(5):
            i = idx.get(day + timedelta(days=off))
            if i is not None:
                return i
        return None

    start = datetime(dates[0].year, dates[0].month, dates[0].day, tzinfo=timezone.utc)
    k = math.floor(_dt_to_k(start))
    longs: List[float] = []
    shorts: List[float] = []
    while True:
        nm, fm = true_phase(k), true_phase(k + 0.5)
        if nm.date() > dates[-1]:
            break
        for when, bucket, sign in ((nm, longs, 1), (fm, shorts, -1)):
            if when.date() < dates[0]:
                continue
            i = first_index_on_or_after(when.date())
            r = fwd(i) if i is not None else None
            if r is not None:
                bucket.append(sign * r)
        k += 1
    base = [r for r in (fwd(i) for i in range(len(closes))) if r is not None]

    def summ(xs: List[float]) -> Dict:
        if not xs:
            return {"n": 0}
        mean = sum(xs) / len(xs)
        sd = (sum((x - mean) ** 2 for x in xs) / max(1, len(xs) - 1)) ** 0.5
        return {"n": len(xs), "mean_pct": round(100 * mean, 3),
                "win_rate": round(sum(x > 0 for x in xs) / len(xs), 3),
                "t_stat": round(mean / (sd / len(xs) ** 0.5), 2) if sd > 0 else None}

    lunar = longs + shorts
    s_all, s_base = summ(lunar), summ(base)
    t = s_all.get("t_stat")
    verdict = ("NO EVIDENCE — keep LUNAR_GATE off" if t is None or abs(t) < 2 or
               s_all["mean_pct"] <= abs(s_base.get("mean_pct", 0)) else
               "POSITIVE — t ≥ 2 and beats buy-and-hold drift; still forward-test before enabling")
    return {"ok": True, "hold_days": hold_days, "new_moon_longs": summ(longs),
            "full_moon_shorts": summ(shorts), "lunar_rule_combined": s_all,
            "baseline_any_day_long": s_base, "verdict": verdict}
