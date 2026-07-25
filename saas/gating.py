"""SaaS tier-gating middleware — OPT-IN, non-breaking.

Disabled by default (SAAS_GATING_ENABLED=false) so the current system + internal
crons run unchanged. When enabled, it gates the premium /gold and /execution prefixes
by tier: a caller needs a valid X-API-Key whose tier meets the path's minimum. An
internal SERVICE_KEY (the crons/VPS) bypasses gating entirely.
"""

from __future__ import annotations

from decouple import config
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from saas.auth import tier_for_path
from saas import tiers as st


def gating_enabled() -> bool:
    try:
        return config("SAAS_GATING_ENABLED", default=False, cast=bool)
    except Exception:
        return False


class TierGatingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if not gating_enabled():
            return await call_next(request)
        path = request.url.path
        need = tier_for_path(path)
        if not need:
            return await call_next(request)               # open endpoint
        # internal service bypass (crons / VPS bridge)
        service_key = config("SERVICE_KEY", default=None)
        key = request.headers.get("x-api-key") or request.query_params.get("api_key")
        if service_key and key == service_key:
            return await call_next(request)
        if not key:
            return JSONResponse({"detail": "API key required", "need_tier": need},
                                status_code=401)
        # resolve the user's tier (best-effort DB read)
        try:
            from database.db import AsyncSessionLocal
            from services import saas_service as svc
            async with AsyncSessionLocal() as db:
                user = await svc.by_api_key(db, key)
        except Exception:
            user = None
        if not user:
            return JSONResponse({"detail": "invalid API key"}, status_code=401)
        if not st.tier_allows(user.tier, need):
            return JSONResponse(
                {"detail": f"upgrade required — {need} tier (you are on {user.tier})",
                 "need_tier": need, "your_tier": user.tier}, status_code=402)
        return await call_next(request)
