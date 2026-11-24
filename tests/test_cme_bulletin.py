import datetime as dt

from services import cme_bulletin as cb

OPT = """PG64 BULLETIN # 226@                     METALS OPTION PRODUCTS                 Mon, Nov 23, 2026 PG64
                                 METAL FUTURES CONTRACTS LAST TRADE DATES
  EXPIRATION:             NOV26       NOV26   NOV26     DEC26        JAN27    FEB27
  OG CALL                                               11/24        12/28    01/26
  OG PUT                                                11/24        12/28    01/26
  OG1 CALL                                              12/04
  OG1 PUT                                               12/04

                                         GOLD OPTIONS ON FUTURES
OG CALL          COMEX GOLD OPTIONS
DEC26
3600   62.00     62.00           62.00/14.90A     71.90B/14.90A       ----                20.50   - 78.10   .2571   ----    ----      218    ----     252 +       119
3700   45.00     45.00           57.50/42.80A     135.40B/42.80A      ----                57.30   -113.40   .5544   ----    ----       13    ----      94 +         1
20000   ----    ----             ----              ----/0.10A          ----                0.10     UNCH    ----    ----    ----      ----   ----     38128     UNCH
TOTAL                                                                                                                                          4                   7+       4
OG PUT           COMEX GOLD OPTIONS
DEC26
3500    ----     ----             ----            ----/0.10A          ----                 0.10    UNCH    ----   ----    ----    ----     ----      219       UNCH
3650 128.80     128.80           128.80/94.10A    165.10B/94.10A      ----               109.10   - 83.40   .4413   ----    ----      113    ----     356 -      7
JAN27
3400    ----     ----             ----            ----/0.10A          ----                 1.10    UNCH    ----   ----    ----    ----     ----      1,500     UNCH
OG1 CALL         GOLD OPTIONS
DEC26
3800    ----    ----             ----             ----                ----               20.00    UNCH     ----   ----    ----     ----    ----         40   UNCH
"""
FUT = """GC FUT               COMEX GOLD FUTURES
NOV26                        3569.00             3665.40 /3569.00        3649.20 +   136.90                     240           ----               228    +      186
DEC26                        3570.50             3691.00 /3552.30        3651.40 +   136.00                  257514           5624            107337    -    42128
FEB27                        3596.70             3720.90 /3582.70        3681.70 +   137.60                  105582           5742            193969    +    33141
TOTAL GC FUT                                                                                                 389302          11712            383585    -    3663
MGC FUT              MICRO GOLD FUTURES
DEC26                        3570.50             3691.00 /3552.30        9999.40 +   136.00                  257514           5624            107337    -    42128
"""


def test_date_and_futures():
    assert cb.bulletin_date(OPT) == dt.date(2026, 11, 23)
    assert cb.parse_futures(FUT) == {"NOV26": 3649.2, "DEC26": 3651.4, "FEB27": 3681.7}


def test_expiry_table():
    ex = cb.expiry_table(OPT)
    assert ex[("OG", "CALL", "DEC26")] == dt.date(2026, 11, 24)
    assert ex[("OG", "PUT", "JAN27")] == dt.date(2026, 12, 28)
    assert ex[("OG1", "CALL", "DEC26")] == dt.date(2026, 12, 4)


def test_rows_open_interest():
    rows = cb.parse_options(OPT)
    oi = {(r["type"], r["month"], r["strike"]): r["oi"] for r in rows}
    assert oi[("CALL", "DEC26", 3600.0)] == 252 and oi[("CALL", "DEC26", 3700.0)] == 94
    assert oi[("PUT", "DEC26", 3650.0)] == 356 and oi[("PUT", "JAN27", 3400.0)] == 1500
    assert oi[("CALL", "DEC26", 20000.0)] == 38128


def test_chain_band_and_underlying_mapping():
    ch = cb.gold_from_texts(OPT, FUT, max_days=45)
    ks = set(ch["strikes"])
    assert all(k < 4100 for k in ks)                              # 20,000 lottery calls dropped
    # JAN27 options (exp 28 Dec) sit on the FEB27 future → shifted onto the front future
    assert round(3400 * 3649.2 / 3681.7, 1) in ks
    assert ch["put_call_ratio"] is not None and ch["asof"] == "2026-11-23"


def test_underlying_rule():
    fut = {"DEC26": 1.0, "FEB27": 2.0}
    assert cb.gold_underlying(dt.date(2026, 11, 24), fut) == 1.0
    assert cb.gold_underlying(dt.date(2026, 12, 4), fut) == 2.0
