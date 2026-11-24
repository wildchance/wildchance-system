"""Access gate for the whole app — public surface vs operator-only endpoints.

The engines (gold scans, deploys, execute=true, /send_alert, DB writes…) used to be
open to anyone who found the URL. With the public members site live, everything that
isn't on the PUBLIC list now needs one of:
  • X-Internal-Token  — per-process secret the in-app scheduler adds (never leaves the box)
  • X-Ops-Token header or ?token=  — OPS_TOKEN (falls back to CRON_TOKEN) for you / TradingView
Public surface keeps its own auth: members (API key), billing (provider signatures),
bridge (BRIDGE_TOKEN), cron (CRON_TOKEN), execution (EXECUTION_TOKEN), alertatron (secret).
If neither OPS_TOKEN nor CRON_TOKEN is set the gate is OFF (local dev / tests).
"""

from __future__ import annotations

import hmac
import secrets
from typing import Optional

from decouple import config

INTERNAL_TOKEN = secrets.token_hex(24)

PUBLIC_EXACT = {"/", "/pricing", "/members", "/status", "/health", "/favicon.ico", "/terminal",
                "/telegram/webhook", "/webhook/alertatron", "/macro/brief"}
PUBLIC_PREFIX = ("/static/", "/saas/", "/members/", "/billing/", "/bridge/", "/cron/",
                 "/execution/")


def ops_token() -> Optional[str]:
    return config("OPS_TOKEN", default=None) or config("CRON_TOKEN", default=None)


# Under a public prefix but NOT public: they expose account data / run jobs without
# their own auth (audit Nov-24). The ops gate (or the scheduler's internal token) applies.
PRIVATE_EXACT = {"/bridge/status", "/billing/expire", "/cron/jobs", "/execution/status",
                 "/execution/routing",
                 "/journal/stats", "/journal/trades", "/journal/report"}  # /ops/* is never public


def is_public(path: str) -> bool:
    if path.rstrip("/") in PRIVATE_EXACT:
        return False
    return path in PUBLIC_EXACT or path.startswith(PUBLIC_PREFIX)


def _eq(a: Optional[str], b: Optional[str]) -> bool:
    return bool(a and b) and hmac.compare_digest(a, b)


def allowed(path: str, headers, query) -> bool:
    if is_public(path):
        return True
    ops = ops_token()
    if not ops:                       # gate disabled until a token exists
        return True
    if _eq(headers.get("x-internal-token"), INTERNAL_TOKEN):
        return True
    return _eq(headers.get("x-ops-token"), ops) or _eq(query.get("token"), ops)
