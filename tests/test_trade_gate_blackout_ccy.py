import datetime as dt

from services.trade_gate import blackout


def test_all_ccy_blackouts(tmp_path):
    p = tmp_path / "b.csv"
    p.write_text("start_utc,end_utc,symbols,label\n2026-12-17T12:45,2026-12-17T15:00,ALL_EUR,ECB\n"
                 "2026-12-04T13:00,2026-12-04T14:30,ALL_USD,NFP\n")
    t = lambda s: dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc)
    assert blackout("EUR/GBP", t("2026-12-17T13:00"), str(p))
    assert not blackout("GBP/JPY", t("2026-12-17T13:00"), str(p))
    assert blackout("XAU/USD", t("2026-12-04T13:30"), str(p))
    assert not blackout("EUR/USD", t("2026-12-04T15:00"), str(p))
