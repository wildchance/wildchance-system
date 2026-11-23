"""In-app scheduler — the single source of truth for every timed job.

Called by /cron/tick (cron-job.org every 5 min, GitHub Actions hourly as backup).
Each job declares its OWN time zone, so New York / London jobs follow daylight saving
automatically (zoneinfo), and UTC jobs stay fixed. A tick runs every job whose
scheduled time fell inside the look-back window and hasn't run for that slot yet —
slots are claimed in the DB (unique key), so overlapping ticks or restarts never
double-fire, and a missed tick is caught up by the next one.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from decouple import config

LOOKBACK_MIN = 70           # covers the hourly GitHub backup if cron-job.org is down
WEEKDAYS = (0, 1, 2, 3, 4)  # Mon–Fri (Python weekday())


@dataclass(frozen=True)
class Job:
    name: str
    tz: str                                   # "UTC" | "America/New_York" | "Europe/London"
    hours: Optional[Tuple[int, ...]]          # None = every hour
    minute: int
    calls: Tuple[Tuple[str, str], ...]        # (METHOD, path)
    days: Optional[Tuple[int, ...]] = None    # local weekday filter (Mon=0)
    dom: Optional[int] = None                 # day of month
    months: Optional[Tuple[int, ...]] = None


UTC, NY, LDN = "UTC", "America/New_York", "Europe/London"
P, G = "POST", "GET"


def _acct() -> str:
    return str(config("USDJPY_ACCOUNT_SIZE", default="0") or "0")


def jobs() -> List[Job]:
    """The schedule (ported 1:1 from .github/workflows/scheduled-scans.yml)."""
    hourly = [(P, "/intraday/scan"), (G, "/zconfluence/scan?notify=true"), (P, "/gold/monitor"),
              (P, "/gold/zones/digest?touch_only=true"), (P, "/gold/recon?armed_only=true"),
              (P, "/gold/retracement"), (P, "/gold/confirm?deploy=true"),
              (P, "/gold/levels/refresh?interval=4h"), (P, "/gold/alerter/scan?notify=true"),
              (P, "/sniper/scan?notify=true")]
    exec_token = config("EXECUTION_TOKEN", default=None)
    if exec_token:
        hourly.insert(1, (P, f"/execution/reconcile?token={exec_token}"))
    newday = ((P, "/gold/recon?force=true"), (P, "/gold/retracement?deploy=true"),
              (G, "/swing/scan?notify=true"))
    return [
        # --- session digests + scrapes (UTC) ---
        Job("digest_before_ny", UTC, (5,), 0, ((P, f"/usdjpy/scan?account_size={_acct()}&notify=true"),
            (P, "/wildchance/scrape?tier=6h"), (P, "/alerts/session?label=Before%20New%20York"))),
        Job("digest_ny", UTC, (9,), 0, ((P, "/wildchance/scrape?tier=6h"),
            (P, "/alerts/session?label=New%20York"))),
        Job("digest_after_open", UTC, (17,), 0, ((P, "/wildchance/scrape?tier=6h"),
            (P, "/alerts/session?label=After%20open"))),
        Job("digest_asian_open", UTC, (21,), 0, ((P, "/wildchance/scrape?tier=6h"),
            (P, "/alerts/session?label=Asian%20open"))),
        Job("daily_0600", UTC, (6,), 0, ((P, "/wildchance/scrape?tier=daily"), (G, "/gold/news"),
            (G, "/macro/dashboard?force=true"), (G, "/macro/brief?force=true"),
            (G, "/billing/expire"), (G, "/data/refresh?part=macro"),
            (P, "/gold/dxy/flip"), (P, "/gold/kingdom-report?notify=true"))),
        Job("six_hourly", UTC, (0, 12, 18), 0, ((P, "/wildchance/scrape?tier=6h"),
            (P, "/autoalert/confluence?min_confidence=85"), (G, "/sentiment/scan"),
            (G, "/data/refresh?part=sentiment"),
            (G, "/swing/scan?notify=true"))),
        Job("weekly_cot_fri", UTC, (21,), 0, ((P, "/wildchance/scrape?tier=weekly"),
            (P, "/gold/regime/refresh"), (P, "/gold/dxy/flip")), days=(4,)),
        Job("monthly_scorecard", UTC, (8,), 0, ((P, "/scorecard/digest"),), dom=1),
        Job("hourly", UTC, None, 0, tuple(hourly)),
        # Sunday weekly open — 20:05 New York, once Sunday's CBDR box (17:00 open → 20:00)
        # is complete. 22:00 UTC was 17:00 NY in winter: inside the thin open hour AND the
        # forming box, so the weekly gold swing scan was held every week.
        Job("weekly_review_sun", NY, (20,), 5, ((P, "/scorecard/weekly"),
            (P, "/gold/scan?balance=5000&notify=true"), (P, "/cbdr/alert/XAU/USD?window=trade4"),
            (P, "/gold/monitor"), (P, "/gold/scorecard/digest"), (P, "/gold/stratops/fit"),
            (P, "/yearly/alert")), days=(6,)),
        Job("asia_accumulation", UTC, (2,), 0, ((P, "/gold/intraday?balance=5000&notify=true"),
            (P, "/gold/stratops/deploy")), days=WEEKDAYS),
        Job("crt_asia", UTC, (5,), 0, ((P, "/candlerange/crt?session=asia&execute=true"),), days=WEEKDAYS),
        Job("crt_ny", UTC, (17,), 0, ((P, "/candlerange/crt?session=ny&execute=true"),), days=WEEKDAYS),
        Job("candlerange_0205", UTC, (2,), 5, ((P, "/candlerange/scan"),)),
        Job("candlerange_1405", UTC, (14,), 5, ((P, "/candlerange/scan"),)),
        Job("quarterly_theory", UTC, (7,), 0, ((P, "/quarterly/alert/USD/JPY"),
            (P, "/quarterly/alert/EUR/USD"), (P, "/quarterly/alert/XAU/USD"),
            (P, "/quarterly/alert/NAS100")), dom=1, months=(1, 4, 7, 10)),
        Job("benner_mon", UTC, (8,), 30, ((P, "/benner/alert"),), days=(0,)),
        Job("edgefinder", UTC, (6,), 30, ((P, "/edgefinder/snapshot"), (P, "/edgefinder/digest"))),
        # --- New York / London local time (DST-aware) ---
        Job("prelondon_0300ny", NY, (3,), 0, ((P, "/cbdr/alert/USD/JPY?window=prelondon"),
            (P, "/cbdr/alert/EUR/USD?window=prelondon"), (P, "/cbdr/alert/GBP/USD?window=prelondon"),
            (P, "/cbdr/limits/XAU/USD?window=prelondon&notify=true"),
            (P, "/gold/prelondon?track=true&execute=true"),
            (P, "/gold/zones/digest?touch_only=false"))),
        Job("london_open_0800", LDN, (8,), 0, ((P, "/gold/options/refresh"),
            (P, "/gold/scan?balance=5000&notify=true"),
            (P, "/gold/intraday?balance=5000&notify=true")), days=WEEKDAYS),
        Job("ny_open_0900", NY, (9,), 0, ((P, "/gold/options/refresh"),
            (P, "/gold/scan?balance=5000&notify=true"), (P, "/gold/intraday?balance=5000&notify=true"),
            (P, "/gold/zones/digest?touch_only=false"), (P, "/gold/stratops/deploy")), days=WEEKDAYS),
        Job("ny_am_0930", NY, (9,), 30, ((P, "/gold/intraday?balance=5000&require_distribution=true&notify=true"),),
            days=WEEKDAYS),
        Job("newday_0000ny", NY, (0,), 0, newday),
        # The CBDR box forms 14:00–20:00 NY (entries are held meanwhile); trade it once
        # it is COMPLETE: box levels for the majors + gold, then the swing/recon reads.
        Job("cbdr_complete_2000ny", NY, (20,), 5, (
            (P, "/cbdr/alert/XAU/USD?window=cbdr"), (P, "/cbdr/alert/EUR/USD?window=cbdr"),
            (P, "/cbdr/alert/GBP/USD?window=cbdr"), (P, "/cbdr/alert/USD/JPY?window=cbdr"))
            + newday),
        Job("friday_preclose", NY, (16,), 55, ((P, "/candlerange/friday"),), days=(4,)),
    ]


def slots(job: Job, now: dt.datetime, lookback_min: int = LOOKBACK_MIN) -> List[dt.datetime]:
    """Scheduled UTC instants of ``job`` inside (now − lookback, now]."""
    tz = ZoneInfo(job.tz)
    start = now - dt.timedelta(minutes=lookback_min)
    out = []
    local_now = now.astimezone(tz)
    for day_off in (-1, 0):
        d = (local_now + dt.timedelta(days=day_off)).date()
        if job.days is not None and d.weekday() not in job.days:
            continue
        if job.dom is not None and d.day != job.dom:
            continue
        if job.months is not None and d.month not in job.months:
            continue
        for h in (job.hours if job.hours is not None else range(24)):
            local = dt.datetime(d.year, d.month, d.day, h, job.minute, tzinfo=tz)
            u = local.astimezone(dt.timezone.utc)
            if start < u <= now:
                out.append(u)
    return out


def due(now: dt.datetime, done: Iterable[str] = (), job_list: Optional[Sequence[Job]] = None,
        lookback_min: int = LOOKBACK_MIN) -> List[Tuple[str, Job]]:
    """[(slot_key, job)] due at ``now`` and not yet in ``done``."""
    done = set(done)
    res = []
    for j in job_list or jobs():
        for s in slots(j, now, lookback_min):
            key = f"{j.name}@{s:%Y-%m-%dT%H:%MZ}"
            if key not in done:
                res.append((key, j))
    return res


# Call classes for the central trade gate (services.trade_gate.session_block):
#   ENTRY  → new trades / deploys / trade alerts: held on weekends AND while the CBDR box forms
#   DIGEST → setup / breach / box digests: held on weekends only (Friday's bars)
#   other  → manage open trades, scrapes, COT, refreshes, reports: always run
import re as _re

_ENTRY = _re.compile(r"execute=true|deploy=true|/stratops/deploy|/gold/scan|/gold/intraday|"
                     r"/gold/alerter|/swing/scan|/zconfluence|/cbdr/limits|/gold/prelondon|"
                     r"/gold/recon|/candlerange/crt|/autoalert|/sniper/scan")
_DIGEST = _re.compile(r"/alerts/session|/intraday/scan|/cbdr/alert|/candlerange/(scan|friday)|"
                      r"/gold/zones/digest|/gold/kingdom-report|/gold/dxy/flip|/usdjpy/scan|/yearly/alert")


def call_class(path: str) -> str:
    if _ENTRY.search(path):
        return "entry"
    if _DIGEST.search(path):
        return "digest"
    return "other"


def hold_reason(path: str, now: dt.datetime) -> Optional[str]:
    """Why the central gate holds this call right now (None = run it)."""
    from services.freshness import market_open
    from services.trade_gate import session_block
    cls = call_class(path)
    if cls == "other":
        return None
    if not market_open(now):
        return "market closed"
    if cls == "entry":
        return session_block(now)
    return None


def redact(path: str) -> str:
    import re
    return re.sub(r"(token=)[^&]+", r"\1***", path)
