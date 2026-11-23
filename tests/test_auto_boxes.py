import datetime as dt

from services import auto_boxes as ab


def _h(day, hour, lo, hi):
    return {"time": f"{day} {hour:02d}:00:00", "open": lo, "high": hi, "low": lo, "close": hi}


def test_fomc_box_is_the_decision_day_cbdr():
    bars = [_h("2026-09-16", 13, 4200, 4400)]                         # before 14:00 — ignored
    bars += [_h("2026-09-16", h, 4253.246, 4359.689) for h in range(14, 20)]
    bars += [_h("2026-09-16", 21, 4100, 4500)]                        # after 20:00 — ignored
    by_day = ab.cbdr_by_day(bars)
    assert by_day[dt.date(2026, 9, 16)] == (4253.246, 4359.689)
    boxes = ab.fomc_boxes(by_day, dt.date(2026, 11, 23), 120)
    assert boxes and boxes[0]["year"] == "FOMC auto Sep-16" and boxes[0]["source"] == "auto-fomc"
    assert ab.fomc_boxes(by_day, dt.date(2026, 11, 23), 120, skip=[dt.date(2026, 9, 16)]) == []


def test_chained_box_on_exhausted_minus_four():
    parent = {"year": "FOMC hold Oct-28", "date": "2026-10-28", "low": 4033.198, "high": 4075.173}
    # old −4 = 4033.198 − 4·41.975 = 3865.298 ; a later CBDR whose LOW sits on it
    by_day = {dt.date(2026, 11, 2): (3866.0, 3905.0), dt.date(2026, 11, 3): (3950.0, 3990.0)}
    ch = ab.chained([parent], by_day, tol=15.0)
    assert any(c["year"].startswith("chain FOMC hold Oct-28 -4") and c["low"] == 3866.0 for c in ch)


def test_one_child_per_parent():
    parent = {"year": "FOMC hold Oct-28", "date": "2026-10-28", "low": 4033.198, "high": 4075.173}
    by_day = {dt.date(2026, 11, 2): (3866.0, 3905.0), dt.date(2026, 11, 3): (3865.5, 3990.0),
              dt.date(2026, 11, 9): (4220.0, 4180.0 + 22)}
    assert len(ab.chained([parent], by_day, tol=15.0)) == 1
