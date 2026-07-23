"""Boot smoke-test — the whole app must mount, not just 7 built-in routes.

Regression guard for the 'system not running' incident: an unpinned fastapi
resolved to fastapi 0.139 + starlette 1.3, an incompatible pair where
include_router() silently dropped every sub-router — the app booted with only
the FastAPI built-ins (/docs, /health, …) and none of the trading endpoints.
This asserts the real router surface is actually mounted.
"""

import os

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/test")


def test_app_mounts_full_router_surface():
    import main
    paths = {r.path for r in main.app.routes if hasattr(r, "path")}
    # Far more than the ~7 FastAPI built-ins — the trading routers must be on.
    assert len(paths) > 100, f"only {len(paths)} routes mounted — include_router dropped routers"


def test_core_endpoints_present():
    import main
    paths = {r.path for r in main.app.routes if hasattr(r, "path")}
    for p in ("/health", "/gold/scorecard", "/gold/stratops",
              "/gold/retracement", "/execution/pending"):
        assert p in paths, f"missing endpoint {p}"


def test_telegram_bot_import_is_defensive():
    # The optional inbound bot must never be able to take down boot — the flag
    # exists whether or not python-telegram-bot / cryptography imported cleanly.
    import main
    assert hasattr(main, "_TELEGRAM_BOT_OK")


def test_gold_scan_has_no_toplevel_ohlc_import():
    # ohlc_service imports gold_scan (its Telegram sender); if gold_scan imports
    # fetch_ohlc back at MODULE TOP the two form a circular import that crashes
    # boot ('partially initialized module'). fetch_ohlc must be imported lazily
    # inside scan(), so it must NOT be a module-level attribute of gold_scan.
    import services.gold_scan as gs
    assert not hasattr(gs, "fetch_ohlc"), (
        "gold_scan imports fetch_ohlc at module top — reintroduces the "
        "ohlc_service<->gold_scan circular import")


def test_ohlc_and_gold_scan_import_both_orders():
    # Importing either module first must succeed (no order-dependent cycle).
    import importlib
    import services.ohlc_service, services.gold_scan
    importlib.reload(services.ohlc_service)
    importlib.reload(services.gold_scan)
    assert hasattr(services.ohlc_service, "fetch_ohlc")
