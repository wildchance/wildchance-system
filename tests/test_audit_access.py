from services import access


def test_sensitive_routes_are_behind_the_gate():
    for p in ("/bridge/status", "/billing/expire", "/cron/jobs", "/execution/status", "/execution/routing"):
        assert not access.is_public(p), p
    # their siblings keep their own auth and stay reachable
    for p in ("/bridge/bars", "/bridge/deals", "/billing/paystack/webhook", "/cron/tick", "/execution/pending",
              "/members/login", "/health"):
        assert access.is_public(p), p
