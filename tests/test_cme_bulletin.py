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


YEN_CALLS = """33                    JAPANESE YEN CALL OPTIONS                    33
PG33 BULLETIN # 226@          JAPANESE YEN CALL OPTIONS          Mon, Nov 23, 2026          PG33
JPU OPT
DEC26     JPU OPT                      (FUTURES SETT.   .006303 -              17.5 )
6300      0.31                         .36B        .23A        ----                                   .33-     13    .517       ----       22       949 -        4     5.03B     .61A
6300      ----                         ----        ----        ----                                   .71-     16    .705       ----     ----         1       UNCH     ----      ----
TOTAL                                                                                                                                     22       950
DEC26     JPU OPT - 2JY                (FUTURES SETT.   .006303 -              17.5 )
6350      ----                    .45B        .36A        ----                       .44-    11    .511    ----       ----      32     UNCH   ----      ----
                    ADDITIONAL JAPANESE YEN CALLS **SETT.PRICE**
JPU OPT
DEC26     JPU OPT                      (FUTURES SETT.   .006303 -              17.5 )
6300      0.31                         .36B        .23A        ----                                   .33-     13    .517       ----       22       949 -        4     5.03B     .61A
"""
YEN_PUTS = """34                    JAPANESE YEN PUT OPTIONS                    34
PG34 BULLETIN # 226@          JAPANESE YEN PUT OPTIONS          Mon, Nov 23, 2026          PG34
JPU OPT
DEC26     JPU OPT - JPU                (FUTURES SETT.   .006303 -              17.5 )
6200      ----                         ----        ----        ----                                   .20-     13    .200       ----       22       500 -        4     ----      ----
"""


def test_fx_expiry_rules():
    assert cb.fx_expiry("JPU", "DEC26") == dt.date(2026, 12, 4)      # 2nd Friday before 3rd Wed (16 Dec)
    assert cb.fx_expiry("2JY", "DEC26") == dt.date(2026, 12, 11)
    assert cb.fx_expiry("SJP", "DEC26") is None


def test_fx_rows_dedupe_additional_but_keep_style_twins():
    rows = cb.parse_fx_options(YEN_CALLS)
    assert [r["oi"] for r in rows] == [949, 1, 32]                   # twin 6300 kept, ADDITIONAL repeat dropped
    assert rows[2]["code"] == "2JY"


def test_fx_chain_inverts_yen_to_usdjpy():
    ch = cb.fx_chain(YEN_CALLS, YEN_PUTS, max_days=45)
    assert ch["pair"] == "USDJPY" and round(ch["spot"], 2) == 158.65
    k_call = round(1 / 0.0063, 3)                                     # yen CALL 6300 → USD/JPY PUT ≈ 158.73
    assert ch["strikes"][k_call]["put_oi"] == 950 and ch["strikes"][k_call]["call_oi"] == 0
    k_put = round(1 / 0.0062, 3)                                      # yen PUT 6200 → USD/JPY CALL ≈ 161.29
    assert ch["strikes"][k_put]["call_oi"] == 500


EUR = """PG39 BULLETIN # 226@          EURO FX & CME$INDEX OPTIONS          Mon, Nov 23, 2026       PG39
                                     EURO FX & CME$INDEX FUTURES
DEC26               1.09805              1.10150             1.08910          1.09135     (     .9163) -         66             26163     287015    791583 -      7215     1.22095    1.14085
MAR27               1.10270              1.10590B            1.09360A         1.09580     (     .9126) -         66             25023       8404     95918 +     20467     1.22840B   1.14605
                              ADDITIONAL EURO FX & CME$INDEX -FUTURES-
DEC26             .15003                   .15028             .14994A              ----                0.000180                   ----        516      2206 +       240   .15017B     .14840
DEC26     EUU OPT
10800     0.50                    2.00        .50         ----                       1.60+      9   .204    ----       187     616 +     33   7.70      .30
10800     ----                    20.80B      11.40A      ----                      12.90-     58   .794    ----      ----       7     UNCH   93.00B    63.90A
10950     3.20                    8.50        2.50A       ----                       7.10+     34   .611    ----      1542    4333 +    694   9.90B     .50A
10950     7.30                    8.60B       2.70        ----                       3.40-     33   .387    ----       111      73 +     70   78.40B    50.40A
EUU OPT
11000     5.10                    12.20       4.20A       ----                      10.50+     44   .750    ----       284    3169 -     58   12.40     .50
TOTAL                                                                                                                    0       0
                     ADDITIONAL EUROFX & CME$INDEX CALLS & PUTS **SETT. PRICE**
DEC26     EUU OPT
10800     0.50                    2.00        .50         ----                       1.60+      9   .204    ----       187     616 +     33   7.70      .30
"""


def test_eur_futures_and_classification():
    assert cb.parse_eur_futures(EUR) == {"DEC26": 1.09135, "MAR27": 1.0958}
    assert cb.classify(1.08, 1.09135, 0.204) == "PUT" and cb.classify(1.08, 1.09135, 0.794) == "CALL"
    assert cb.classify(1.095, 1.09135, 0.611) == "PUT" and cb.classify(1.095, 1.09135, 0.387) == "CALL"
    assert cb.classify(1.20, 1.09135, None) == "CALL" and cb.classify(1.00, 1.09135, None) == "PUT"


def test_eur_rows_page_break_and_additional():
    rows = cb.parse_eur_options(EUR)
    assert [r["oi"] for r in rows] == [616, 7, 4333, 73, 3169]      # bare 'EUU OPT' keeps DEC26; repeat dropped
    ch = cb.eur_chain(EUR, max_days=45)
    assert ch["pair"] == "EURUSD" and ch["put_oi"] == 616 + 4333 + 3169 and ch["call_oi"] == 7 + 73


def test_owner_reminder_message():
    from services.owner_notify import bulletin_message
    m = bulletin_message({"gold": 4, "EURUSD": 4})
    assert "sections 62 + 64" in m and "data/cme" in m and "gold 4 days" in m


def test_index_expiry_and_month_codes():
    assert cb.index_expiry("EOW2 MINI S&P", "DEC26") == dt.date(2026, 12, 11)
    assert cb.index_expiry("MINI NSDQ EOM", "NOV26") == dt.date(2026, 11, 30)
    assert cb.index_expiry("QN1", "DEC26") == dt.date(2026, 12, 4)
    assert cb.index_expiry("MINI $5 DOW", "DEC26") == dt.date(2026, 12, 18)


IDX = """PG40 BULLETIN # 226@      NASDAQ      Mon, Nov 23, 2026     PG40
DEC26               25561.50          27137.00          25426.75          26808.0     +   168150          4049     1075586   308808 +    2566      31385.50B      15760.00A
TOTAL
MINI NSDQ EOM C
DEC26     MINI NSDQ EOM C
26800     ----     ----     ----     ----          500.00+     100     .520    ----     ----        900     UNCH
                     NASDAQ 100 WEEKLY-1 CALLS
DEC26
27500      ----                   2.00232.00B    1.00218.00A----               204.75+    96500     .326     ----    -     1,200 +   5
20261100 MINI NSDQ EOM P
26500    ----     ----     ----     ----          10.00-     5     .020    ----     ----        700     UNCH
"""


def test_index_chain_parses_all_three_layouts():
    ch = cb.index_chain("NAS100", [IDX], max_days=45)
    assert ch["spot"] == 26808.0
    assert ch["call_oi"] == 900 + 1200 and ch["put_oi"] == 700
