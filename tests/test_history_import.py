import datetime as dt
import io
import zipfile

from wildchance import history_import as hi
from services import yearly_cbdr as yc


def _tv_csv(years, start=1.0):
    """TradingView-style daily export: Q1 box 1.00–1.10 each year, then a run to 1.25."""
    lines = ["time,open,high,low,close,Volume"]
    for y in years:
        d = dt.date(y, 1, 2)
        while d.year == y:
            if d.weekday() < 5:
                if d.month <= 3:
                    c = 1.00 + 0.10 * ((d - dt.date(y, 1, 2)).days / 88)
                else:
                    c = 1.10 + 0.15 * ((d - dt.date(y, 4, 1)).days / 274)
                ts = int(dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc).timestamp())
                lines.append(f"{ts},{c:.5f},{c + 0.001:.5f},{c - 0.001:.5f},{c:.5f},0")
            d += dt.timedelta(days=1)
    return "\n".join(lines)


def test_parse_mt5_and_tv_formats():
    mt5 = "<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\n2025.01.02\t00:00\t1.03\t1.04\t1.02\t1.035\n" \
          "2025.01.02\t12:00\t1.035\t1.05\t1.03\t1.045\n"
    bars = hi.parse(mt5)
    assert bars == [(dt.date(2025, 1, 2), 1.03, 1.05, 1.02, 1.045)]   # intraday rolled to daily
    assert hi.parse(_tv_csv([2024]))[0][0] == dt.date(2024, 1, 2)
    assert hi.guess_symbol("OANDA_EURUSD, 1D.csv") == "EUR/USD"


def test_zip_import_writes_boxes_stats_and_keeps_manual(tmp_path, monkeypatch):
    boxes, stats = tmp_path / "b.csv", tmp_path / "s.csv"
    boxes.write_text("symbol,year,low,high,source\nEUR/USD,2020,1.11,1.12,manual\n")
    z = tmp_path / "EURUSD.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("OANDA_EURUSD, 1D.csv", _tv_csv(range(2015, 2022)))
    rep = hi.import_files([str(z)], source="oanda", boxes_path=str(boxes), stats_path=str(stats))
    assert rep[0]["symbol"] == "EUR/USD" and rep[0]["manual_kept"] == 1 and rep[0]["spacing"] == "D"
    txt = boxes.read_text()
    assert "EUR/USD,2020,1.11,1.12,manual" in txt and "EUR/USD,2016," in txt
    monkeypatch.setattr(yc, "STATS_CSV", str(stats))
    yc._stats.update(mtime=None, rows={})
    h = yc.history_stats("EURUSD")
    assert h["years"] == 7 and h["median_up"] >= 2.4 and h["pct_reach_up"]["2.5"] >= 50 \
        and h["pct_reach_up"]["4"] == 0
