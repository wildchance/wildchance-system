//+------------------------------------------------------------------+
//|                                       WildChance_PropEA_v4.mq5   |
//|  WildChance BTMM -> live prop-challenge Expert Advisor            |
//|                                                                    |
//|  Built on WildChance_BTMM_Dashboard v2.2 logic (EMA 13/50/200/800 |
//|  structure, M/W scoring at HOD/LOD/PDH/PDL, ID50 retest, Asia/PD  |
//|  stop hunts, TDI, D1 trend-day, ADR) with:                        |
//|   - Prop guard: daily + max equity kill-switch, PERSISTED across  |
//|     terminal restarts (GlobalVariables), phase-target lock         |
//|   - ADX [CHOP-TRAP] filter, MQL5 economic-calendar news pause      |
//|   - Lunar overlay (Meeus true New/Full Moon, no API) - ON          |
//|   - FundingPips plan picker (Std/Flex/Pro/1-Step/LABS/Zero), with  |
//|     trailing (Zero) and end-of-day (LABS001) max-loss modes         |
//|   - HTF Bollinger +-1/+-2 sigma confluence for M/W reversals        |
//|   - Pushes broker bars, account snapshot and the MT5 economic      |
//|     calendar to the app (/bridge); token sent as a header          |
//|   - v4.54 APP GATE: before every entry asks GET /bridge/gate       |
//|     (council GO + currency exposure caps + regime + app calendar)  |
//|     and caps risk at the app's risk budget                          |
//|   - Risk sizing that SKIPS (never rounds up) when min lot over-    |
//|     risks; auto filling mode; 1/3-1/3-1/3 R ladder + BE after TP1  |
//|                                                                    |
//|  Same guard math as the Python app (propfirm/challenge.py) and    |
//|  the VPS bridge (mt5_bridge/equity_guard.py).                      |
//|  NOT compiled/tested by the author of this revision: compile in   |
//|  MetaEditor (F7), run in the Strategy Tester, then a DEMO first.  |
//+------------------------------------------------------------------+
#property copyright "WildChance"
#property version   "4.65"

#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>

CTrade        trade;
CPositionInfo posInfo;

enum ENUM_MACRO_BIAS
  {
   MACRO_BOTH      = 0, // Both directions
   MACRO_BUY_ONLY  = 1, // COT / currency-meter lock: BUY only
   MACRO_SELL_ONLY = 2  // COT / currency-meter lock: SELL only
  };

enum ENUM_FP_PRESET
  {
   PLAN_CUSTOM        = 0, // Custom (use the % inputs below as-is)
   PLAN_2STEP_STD_5   = 1, // FundingPips 2-Step Standard 8%/5%, 5% daily, 10% static
   PLAN_2STEP_STD_3   = 2, // FundingPips 2-Step Standard 8%/5%, 3% daily, 10% static
   PLAN_2STEP_FLEX    = 3, // FundingPips 2-Step Flex 10%/8%, 4% daily, 12% static
   PLAN_2STEP_PRO     = 4, // FundingPips 2-Step Pro 6%/6%, 3% daily, 6% static
   PLAN_1STEP_3       = 5, // FundingPips 1-Step Flex 12%, 3% daily, 12% static
   PLAN_1STEP_2       = 6, // FundingPips 1-Step Flex 12%, 2% daily, 12% static
   PLAN_1STEP_LABS    = 7, // FundingPips 1-Step LABS001 5%, 1% daily, 3% EOD max
   PLAN_ZERO          = 8  // FundingPips Zero: no target, 3% daily, 5% trailing max
  };

enum ENUM_MAX_REF { MAXREF_STATIC=0, MAXREF_TRAILING=1, MAXREF_EOD=2 };

enum ENUM_SIZING_MODE
  {
   SIZING_TARGET = 0, // Target $: lot so the chosen TP pays the target (owner's sheet), capped by risk %
   SIZING_RISK   = 1  // Risk %: lot so the stop loses InpRiskPct
  };

enum ENUM_LUNAR_SHIFT_MODE
  {
   LUNAR_OBSERVE_PAUSE = 0, // Pause entries in the observation window
   LUNAR_HALF_RISK     = 1  // Trade at 50% risk in the observation window
  };

//----------------------------------------------------------------- inputs

input group "=== 1. ACCOUNT GUARD (defaults suit a normal account; pick a preset for a prop challenge) ==="
input ENUM_FP_PRESET InpPreset      = PLAN_CUSTOM; // Account plan — CUSTOM = normal account (3% day / 8% max, no target)
input int      InpPhase             = 0;      // Prop presets only: 1 / 2 = evaluation phase, 0 = funded
input double   InpDailyBufferPct    = 70;     // Stop at this % of the firm's daily limit (5% -> 3.5%)
input double   InpMaxBufferPct      = 80;     // Stop at this % of the firm's max loss (10% -> 8%)
input double   InpInitialBalance    = 0;      // Account starting balance (0 = capture once & persist)
input double   InpDailyLossLimitPct = 3.0;    // CUSTOM only: our daily stop, % of initial
input double   InpMaxLossLimitPct   = 8.0;    // CUSTOM only: our max-loss stop, % of initial
input double   InpProfitTargetPct   = 0.0;    // CUSTOM only: target % (0 = off)
input int      InpMinTradingDays    = 0;      // CUSTOM only: min trading days before the target counts
input bool     InpFlattenAllOnBreach= true;   // On a guard hit close ALL account positions (not only this EA's)
input double   InpRiskPct           = 0.50;   // Risk % of equity per trade (0.4-0.5 for challenges)
input ENUM_SIZING_MODE InpSizingMode = SIZING_RISK; // Lot sizing method (RISK = InpRiskPct of equity at the stop)
input double   InpTargetPct         = 0.52;   // SIZING_TARGET: target per trade, % of initial balance ($13 on 2.5k)
input double   InpTargetUSD         = 0;      // SIZING_TARGET: fixed $ target instead (0 = use %)
input int      InpTargetTP          = 3;      // SIZING_TARGET: which TP must pay the target (1, 2 or 3)
input double   InpFixedSLPips       = 0;      // 0 = structural stop; 10 = owner's sheet (100 points)
input bool     InpUsePartials       = true;   // false = single TP (matches the sheet's math exactly)
input int      InpMaxConcurrent     = 2;      // Max open trades for this magic across all charts
input int      InpMaxSpreadPips     = 3;      // Skip entry if spread is wider (pips)
input ulong    InpMagicNumber       = 408812; // Magic number (use the same one on every chart)
input double   InpMaxSpreadATR      = 0.15;   // Non-FX (BTC, indices, metals, oil): max spread as a share of ATR
input bool     InpPhonePush         = true;   // MT5 phone push (Tools > Options > Notifications > MetaQuotes ID) on entries + strong setups
input int      InpPhoneMinQuality   = 7;      // ...and on blocked setups at/above this quality
input bool     InpRandomizeSL       = false;  // Widen SL by a random 1-3 pips (fill diversity only)

input group "=== 2. LUNAR OVERLAY (ON - verify with GET /lunar/backtest on your symbols) ==="
input bool     InpUseLunarEngine    = true;   // New Moon->Full: BUY only | Full->New: SELL only
input int      InpLunarObsHours     = 24;     // Observation window +/- hours around New/Full Moon
input ENUM_LUNAR_SHIFT_MODE InpLunarShiftMode = LUNAR_HALF_RISK;
input int      InpNewMoonHoldDays   = 14;     // Max hold for lunar longs (days)
input int      InpFullMoonHoldDays  = 12;     // Max hold for lunar shorts (days)

input group "=== 3. MACRO BIAS, CHOP-TRAP, NEWS, WEEKEND ==="
input ENUM_MACRO_BIAS InpMacroBias  = MACRO_BOTH; // Weekly COT / currency-meter direction lock
input bool     InpUseChopFilter     = true;   // Block when ADX(14) < InpMinADX  [CHOP-TRAP]
input double   InpMinADX            = 20.0;
input bool     InpUseNewsFilter     = true;   // Pause around HIGH-impact events (live only; not in tester)
input int      InpNewsMinsBefore    = 30;
input int      InpNewsMinsAfter     = 30;
input bool     InpCloseBeforeWknd   = false;  // Flatten this EA's trades Friday from InpFridayCloseHour
input int      InpFridayCloseHour   = 21;     // GMT hour (any broker — converted automatically)

input group "=== 4. BTMM EMA STRUCTURE, M/W, ID50 (v2.2) ==="
input int      InpEmaFast           = 13;
input int      InpEmaSlow           = 50;
input int      InpEmaMid            = 200;
input int      InpEmaLong           = 800;
input int      InpSwingLookback     = 5;
input int      InpMaxBarsBetween    = 40;
input double   InpEqualityXATR      = 0.6;
input int      InpMinQualityScore   = 6;
input int      InpMinLevelsForSig   = 1;
input int      InpHuntWindowMult    = 5;
input int      InpConfirmLookback   = 3;
input double   InpLocTolXATR        = 0.35;
input double   InpID50ToleranceXATR = 0.25;

input group "=== 5. ADR, SESSIONS (GMT hours — any broker), SIGNAL FILTERS ==="
input int      InpADRDays           = 20;
input int      InpSessionAsiaStart  = 0;
input int      InpSessionAsiaEnd    = 8;
input int      InpSessionLondonStart= 8;
input int      InpSessionLondonEnd  = 13;
input int      InpSessionNYStart    = 13;
input int      InpSessionNYEnd      = 21;
input bool     InpSessionFilter     = true;   // Only enter in London / New York
input double   InpMaxADRUsed        = 80.0;   // Block once ADR used % exceeds (0 = off)
input bool     InpRequireSweepOrLoc = true;
input bool     InpRequireBiasAlign  = true;
input bool     InpRequireTDI        = false;
input int      InpID50MaxTrendDay   = 0;      // Block ID50 once D1 trend is older (0 = off)
input bool     InpTradeID50         = true;

input group "=== 6. TRADE PLAN (1/3 at each TP) ==="
input double   InpSLBufferXATR      = 0.3;
input double   InpMinSLxATR         = 1.0;
input double   InpMaxSLxATR         = 4.0;
input double   InpTP1R              = 1.0;
input double   InpTP2R              = 1.8;    // +-1 sigma core target
input double   InpTP3R              = 3.0;    // runner
input bool     InpBEAfterTP1        = true;
input int      InpMaxBarsInTrade    = 300;    // Time exit when the lunar engine is OFF
input bool     InpDrawTradePlan     = true;
input bool     InpAlertOnEntry      = true;
input double   InpPipSize           = 0;      // 0 = auto

input group "=== 7. BOLLINGER HTF EXTREMES (mean-reversion confluence) ==="
input bool     InpUseBBConfluence   = true;   // +2 M/W score when HTF price is beyond +-1 sigma the reversal way
input bool     InpRequireBBStretch  = false;  // Block M/W unless an HTF is stretched (|z| >= 1) the right way
input int      InpBBPeriod          = 20;
input ENUM_TIMEFRAMES InpBBTF1      = PERIOD_H4;
input ENUM_TIMEFRAMES InpBBTF2      = PERIOD_D1;

input group "=== 8. SEND DATA TO THE APP (EA -> /bridge) ==="
input bool     InpSendData          = true;   // Push closed bars + account snapshot to the app
input string   InpAppUrl            = "https://wildchance-system.onrender.com"; // Add it in Tools > Options > Expert Advisors > WebRequest
input string   InpBridgeToken       = "";     // OPTIONAL owner sync key (journal / flash cards) - leave blank: the EA works without it
input bool     InpPushDeals         = true;   // send closed trades → trade journal (W/L/BE) + flash cards (winners)
input int      InpDealPushSec       = 10;     // how often to check for new closes (seconds, min 5)
input int      InpDealBackfillDays  = 90;     // first run: send this many days of history to the journal
input string   InpAppSymbol         = "";     // Name to send (blank = chart symbol; app strips suffixes)
input string   InpSendTFs           = "M15,H1,H4,D1,W1";
input int      InpInitialBars       = 300;    // First push per timeframe; afterwards the last 3 bars
input bool     InpUseAppGate        = true;   // Ask the app (council/exposure/regime/calendar) before every entry
input bool     InpGateFailOpen      = false;  // App unreachable: false = no council answer, no trade (default) · true = trade on the EA's own filters
input bool     InpUseAppRisk        = true;   // Cap risk % at the app's regime risk budget (never raises it)
input int      InpGateTimeoutMs     = 8000;   // Gate request timeout (the app may be waking up)

input group "=== 9. HUD ==="
input ENUM_BASE_CORNER InpCorner    = CORNER_LEFT_UPPER;
input int      InpFontSize          = 9;
input int      InpXOffset           = 10;
input int      InpYOffset           = 20;

//----------------------------------------------------------------- handles
int hFast=INVALID_HANDLE, hSlow=INVALID_HANDLE, hMid=INVALID_HANDLE, hLong=INVALID_HANDLE;
int hATR=INVALID_HANDLE, hRSI=INVALID_HANDLE, hADX=INVALID_HANDLE;
int hD1F=INVALID_HANDLE, hD1S=INVALID_HANDLE;

//----------------------------------------------------------------- guard state (persisted)
string   g_gvp;                    // GlobalVariable prefix "WC4_<login>_"
double   g_initialBalance = 0;
double   g_dayRef         = 0;
double   g_dailyFloor     = 0, g_maxFloor = 0, g_targetEq = 0;
string   g_propStatus     = "ACTIVE";
double   g_dailyPct = 3.5, g_maxPct = 8.0, g_targetPct = 8.0;   // effective guard lines (% of initial)
int      g_maxRef = MAXREF_STATIC;
int      g_minDays = 0;                 // required trading days (Zero: profitable days, payout rule)
bool     g_minDaysProfitable = false;
int      g_tradeDays = 0, g_profitDays = 0;
datetime g_lastDayCount = 0;
string   g_presetName = "CUSTOM";

//----------------------------------------------------------------- bollinger + data push state
int      hBB1=INVALID_HANDLE, hBB2=INVALID_HANDLE;
double   g_bbZ1=0, g_bbZ2=0;
string   g_bbStr="-";
ENUM_TIMEFRAMES g_sendTF[];
datetime g_sentBar[];
bool     g_sentInit[];
datetime g_lastAcctPush=0, g_lastCalPush=0;
datetime g_lastDealCheck=0, g_lastDealPush=0;
string   g_sendStatus="OFF";

//----------------------------------------------------------------- lunar state
double   g_lunarAgeDays = 0;
string   g_lunarPhaseName = "OFF";
int      g_lunarAllowedDir = 0;
bool     g_inLunarObsWindow = false;
datetime g_nextNew = 0, g_nextFull = 0;

//----------------------------------------------------------------- BTMM state
string   g_signal="WAIT", g_id50Status="None yet", g_pattern="Watching next level...", g_location="N/A";
string   g_stopHunt="NONE YET", g_cyclePhase="Neutral", g_levelsCount="LEVEL 0";
string   g_marketState="-", g_sessionStr="", g_sessionBias="Neutral";
string   g_emaAlign="-", g_tdiState="N/A", g_trendDay="N/A", g_adrTag="NORMAL";
double   g_adrPips=0, g_adrUsedPct=0, g_adxVal=0, g_adrAvg=0, g_todayOpen=0;
int      g_quality=0, g_id50Watch=0, g_adrDayKey=0, g_huntDayKey=0, g_trendDayAge=0;
bool     g_newsBlocked=false, g_isChop=false, g_inAsia=false, g_inLondon=false, g_inNY=false;
string   g_newsWhat = "";          // the release behind a news pause, shown on the panel
datetime g_lastNewsCheck = 0;
bool     g_hAsiaH=false, g_hAsiaL=false, g_hPDH=false, g_hPDL=false;
datetime g_lastHighHuntT=0, g_lastLowHuntT=0, g_id50FiredT=0, g_lastEvalBar=0;
double   g_patExtreme=0, g_tdiPrice=50, g_tdiSignal=50, g_tdiBase=50;
string   g_patKey="", g_lastTradeKey="", g_newSignalTxt="NONE", g_lastResult="NONE";

struct LivePlan
  {
   bool     active;
   ulong    ticket;
   int      dir;
   string   setup;
   datetime openTime;
   int      maxHoldSeconds;
   double   entry, sl, risk, tp1, tp2, tp3, initLots;
   bool     h1, h2;
  };
LivePlan g_tr;

#define OBJ_PREFIX "wc_propea_"

//+------------------------------------------------------------------+
//| init / deinit                                                    |
//+------------------------------------------------------------------+
string g_token = "";
datetime g_lastHud = 0;

// Several charts on one account: ONE leader does the account-wide uploads (deal history, MT5
// calendar, account snapshot); every chart still sends its own symbol's bars. The leader keeps a
// heartbeat; if its chart closes, another chart takes over within 30 s.
bool IsLeader()
  {
   string k = g_gvp + "leader", kt = g_gvp + "leader_t";
   double me = (double)ChartID();
   datetime now = TimeLocal();
   bool stale = !GlobalVariableCheck(k) || !GlobalVariableCheck(kt) || now - (datetime)GlobalVariableGet(kt) > 30;
   if(stale || GlobalVariableGet(k) == me)
     {
      GlobalVariableSet(k, me);
      GlobalVariableSet(kt, (double)now);
      return true;
     }
   return false;
  }
string g_role = "";            // "owner" (full feed) · "member" (gate only) · "" (unknown yet)
string g_license = "";         // last license answer, shown by the SETUP line
datetime g_lastWho = 0;

// GET helper (license check) — returns the HTTP code, -1 when WebRequest is blocked
int AppGet(string path, string &reply)
  {
   string url = InpAppUrl; if(StringSubstr(url, StringLen(url)-1)=="/") url = StringSubstr(url,0,StringLen(url)-1);
   char data[], result[]; string resHeaders;
   string headers = "X-Bridge-Token: " + g_token + "\r\n";
   ResetLastError();
   int code = WebRequest("GET", url + path, headers, 8000, data, result, resHeaders);
   reply = (code > 0) ? CharArrayToString(result, 0, WHOLE_ARRAY, CP_UTF8) : IntegerToString(GetLastError());
   return code;
  }

// App reachable? (the council gate needs no key). With the optional owner sync key, also learn
// whether this terminal may push the owner's journal / bars.
void CheckLicense()
  {
   if(MQLInfoInteger(MQL_TESTER)) return;
   g_lastWho = TimeCurrent();
   string r;
   int code = AppGet("/health", r);
   if(code == -1)
     {
      g_license = (r == "4014") ? "SETUP: Tools > Options > Expert Advisors > allow " + InpAppUrl
                                : "";                      // offline for a moment: no nagging, retried
      return;
     }
   g_license = "";
   g_role = "public";
   if(g_token != "")
     {
      string w;
      if(AppGet("/bridge/whoami", w) == 200 && StringFind(w, "\"role\":\"owner\"") >= 0) g_role = "owner";
     }
  }

// The one line a new user reads: the next thing to do, or READY.
string SetupState()
  {
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED)) return "SETUP: click Algo Trading in the toolbar (turn it green)";
   if(!MQLInfoInteger(MQL_TRADE_ALLOWED))          return "SETUP: EA properties > Common > tick Allow Algo Trading";
   if(g_license != "")                             return g_license;
   if(g_role == "public")                          return "READY - council gate on";
   return "";
  }

// The token from the input, else from MQL5\Files\wildchance_token.txt (one line). A MetaEditor
// DEBUG run never shows the Inputs window — the file makes the feed work there too, and keeps
// the token out of every chart's settings.
string CleanKey(string t)
  {
   // drop a UTF-8 BOM / quotes / spaces that Notepad or copy-paste can add
   while(StringLen(t) > 0 && (StringGetCharacter(t, 0) > 126 || StringGetCharacter(t, 0) <= 32
                              || StringGetCharacter(t, 0) == '"' || StringGetCharacter(t, 0) == '\''))
      t = StringSubstr(t, 1);
   while(StringLen(t) > 0)
     {
      ushort c = StringGetCharacter(t, StringLen(t) - 1);
      if(c > 126 || c <= 32 || c == '"' || c == '\'') t = StringSubstr(t, 0, StringLen(t) - 1); else break;
     }
   return t;
  }

string ReadKeyFile(string name, int common)
  {
   int h = FileOpen(name, FILE_READ|FILE_TXT|FILE_ANSI|common);
   if(h == INVALID_HANDLE) return "";
   string line = FileReadString(h);
   FileClose(h);
   return CleanKey(line);
  }

// The License key from the input, else from a key file in MQL5\Files (this terminal) or the
// common Files folder. "wildchance_token.txt.txt" is accepted: Windows hides extensions.
string LoadToken()
  {
   string t = CleanKey(InpBridgeToken);
   if(t != "") return t;
   string names[] = {"wildchance_token.txt", "wildchance_token.txt.txt", "wildchance_token"};
   for(int i = 0; i < ArraySize(names); i++)
     {
      t = ReadKeyFile(names[i], 0);
      if(t == "") t = ReadKeyFile(names[i], FILE_COMMON);
      if(t != "") { Print("WildChance: owner sync key read from ", names[i]); return t; }
     }
   // no owner sync key: fine — the EA trades with the council gate, it just doesn't sync a journal
   return "";
  }

int OnInit()
  {
   g_token = LoadToken();
   if(MQLInfoInteger(MQL_DEBUG))
      Print("WildChance: running under the MetaEditor DEBUGGER - inputs are the defaults. "
            "Close the debug run and drag the EA from Navigator onto the chart instead.");
   trade.SetExpertMagicNumber(InpMagicNumber);
   trade.SetDeviationInPoints(20);
   trade.SetTypeFillingBySymbol(_Symbol);          // FOK/IOC/RETURN per broker

   g_gvp = "WC4_" + IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)) + "_";
   ApplyPreset();
   LoadGuardState();
   if(InpUseBBConfluence || InpRequireBBStretch)
     {
      hBB1 = iBands(_Symbol, InpBBTF1, InpBBPeriod, 0, 2.0, PRICE_CLOSE);
      hBB2 = iBands(_Symbol, InpBBTF2, InpBBPeriod, 0, 2.0, PRICE_CLOSE);
     }
   SetupSendTFs();

   hFast = iMA(_Symbol, _Period, InpEmaFast, 0, MODE_EMA, PRICE_CLOSE);
   hSlow = iMA(_Symbol, _Period, InpEmaSlow, 0, MODE_EMA, PRICE_CLOSE);
   hMid  = iMA(_Symbol, _Period, InpEmaMid,  0, MODE_EMA, PRICE_CLOSE);
   hLong = iMA(_Symbol, _Period, InpEmaLong, 0, MODE_EMA, PRICE_CLOSE);
   hATR  = iATR(_Symbol, _Period, 14);
   hRSI  = iRSI(_Symbol, _Period, 13, PRICE_CLOSE);
   hADX  = iADX(_Symbol, _Period, 14);
   hD1F  = iMA(_Symbol, PERIOD_D1, 13, 0, MODE_EMA, PRICE_CLOSE);
   hD1S  = iMA(_Symbol, PERIOD_D1, 50, 0, MODE_EMA, PRICE_CLOSE);
   if(hFast==INVALID_HANDLE || hSlow==INVALID_HANDLE || hMid==INVALID_HANDLE ||
      hLong==INVALID_HANDLE || hATR==INVALID_HANDLE  || hADX==INVALID_HANDLE)
     {
      Print("WildChance PropEA: indicator handle init failed");
      return(INIT_FAILED);
     }
   MathSrand((uint)GetTickCount());
   SyncExistingPosition();
   CreateHUD();
   EventSetTimer(1);                               // guard runs even without ticks
   return(INIT_SUCCEEDED);
  }

void OnDeinit(const int reason)
  {
   EventKillTimer();
   ObjectsDeleteAll(0, OBJ_PREFIX);
   int hs[11]; hs[0]=hFast; hs[1]=hSlow; hs[2]=hMid; hs[3]=hLong; hs[4]=hATR;
   hs[5]=hRSI; hs[6]=hADX; hs[7]=hD1F; hs[8]=hD1S; hs[9]=hBB1; hs[10]=hBB2;
   for(int i=0;i<11;i++) if(hs[i]!=INVALID_HANDLE) IndicatorRelease(hs[i]);
  }

void OnTimer()
  {
   CheckPropGuard();
   if(TimeLocal() - g_lastHud >= 2) { UpdateHUD(); g_lastHud = TimeLocal(); }
   if(InpUseNewsFilter && !MQLInfoInteger(MQL_TESTER) && TimeCurrent() - g_lastNewsCheck >= 60)
     { g_newsBlocked = IsHighImpactNewsWindow(); g_lastNewsCheck = TimeCurrent(); }
   if(!MQLInfoInteger(MQL_TESTER) && (g_lastWho == 0 || TimeCurrent() - g_lastWho >= (g_role == "" ? 60 : 3600)))
      CheckLicense();
   bool owner = (g_role == "owner");                 // a member's EA never writes into the owner's feed
   if(InpSendData && owner && !MQLInfoInteger(MQL_TESTER)) PushData();
   if(InpSendData && owner && InpPushDeals && !MQLInfoInteger(MQL_TESTER) && IsLeader()
      && TimeCurrent() - g_lastDealPush >= MathMax(5, InpDealPushSec))
     {
      g_lastDealPush = TimeCurrent();
      PushClosedDeals();
     }
  }

//+------------------------------------------------------------------+
//| Closed trades → app (profit flash cards). Whole account, any source |
//| (EA, signals, manual). First run backfills today; the app posts each |
//| ticket once and only for net-profitable closes.                      |
//+------------------------------------------------------------------+
void PushClosedDeals()
  {
   if(g_token == "") return;
   datetime from = (g_lastDealCheck > 0) ? g_lastDealCheck : TimeCurrent() - MathMax(1, InpDealBackfillDays) * 86400;
   datetime to   = TimeCurrent() + 60;
   if(!HistorySelect(from - 30 * 86400, to)) return;      // wide window so opening deals are found
   int total = HistoryDealsTotal();
   string arr = ""; int n = 0; datetime newest = from; bool more = false;
   for(int i = 0; i < total; i++)
     {
      ulong t = HistoryDealGetTicket(i);
      if(t == 0) continue;
      long entry = HistoryDealGetInteger(t, DEAL_ENTRY);
      if(entry != DEAL_ENTRY_OUT && entry != DEAL_ENTRY_OUT_BY) continue;
      datetime ct = (datetime)HistoryDealGetInteger(t, DEAL_TIME);
      if(ct < from) continue;
      if(n >= 200) { more = true; break; }                // batch the backfill (deals are in time order)
      long   pos    = HistoryDealGetInteger(t, DEAL_POSITION_ID);
      string sym    = HistoryDealGetString(t, DEAL_SYMBOL);
      double cprice = HistoryDealGetDouble(t, DEAL_PRICE);
      double vol    = HistoryDealGetDouble(t, DEAL_VOLUME);
      double net    = HistoryDealGetDouble(t, DEAL_PROFIT) + HistoryDealGetDouble(t, DEAL_COMMISSION)
                    + HistoryDealGetDouble(t, DEAL_SWAP);
      long   dtype  = HistoryDealGetInteger(t, DEAL_TYPE);   // a SELL deal closes a BUY position
      double oprice = 0; datetime ot = 0;
      for(int j = 0; j < total; j++)
        {
         ulong u = HistoryDealGetTicket(j);
         if(u == 0) continue;
         if(HistoryDealGetInteger(u, DEAL_POSITION_ID) == pos && HistoryDealGetInteger(u, DEAL_ENTRY) == DEAL_ENTRY_IN)
           { oprice = HistoryDealGetDouble(u, DEAL_PRICE); ot = (datetime)HistoryDealGetInteger(u, DEAL_TIME); break; }
        }
      if(oprice <= 0) continue;
      int dg = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
      if(n > 0) arr += ",";
      arr += StringFormat("{\"ticket\":%I64u,\"symbol\":\"%s\",\"side\":\"%s\",\"volume\":%.2f,"
                          "\"open_price\":%s,\"close_price\":%s,\"open_time\":%I64d,\"close_time\":%I64d,"
                          "\"net\":%.2f,\"magic\":%I64d,\"comment\":\"%s\"}",
                          t, sym, (dtype == DEAL_TYPE_SELL ? "BUY" : "SELL"), vol,
                          DoubleToString(oprice, dg), DoubleToString(cprice, dg),
                          (long)ot, (long)ct, net, HistoryDealGetInteger(t, DEAL_MAGIC), JsonSafe(HistoryDealGetString(t, DEAL_COMMENT)));
      n++;
      if(ct > newest) newest = ct;
     }
   if(n == 0) return;
   if(PostJson("/bridge/deals", "{\"deals\":[" + arr + "]}"))
     {
      g_lastDealCheck = newest + 1;
      if(more) g_lastDealPush = 0;                        // next batch on the next timer tick
     }
  }

string JsonSafe(string s)
  {
   StringReplace(s, "\\", "/"); StringReplace(s, "\"", "'");
   return StringSubstr(s, 0, 60);
  }

void OnTick()
  {
   bool canTrade = CheckPropGuard();
   ManageLivePosition();

   datetime barTime = iTime(_Symbol, _Period, 1);
   if(barTime != g_lastEvalBar)
     {
      bool first = (g_lastEvalBar == 0);
      g_lastEvalBar = barTime;
      UpdateLunarState(TimeGMT());
      g_newsBlocked = (InpUseNewsFilter && !MQLInfoInteger(MQL_TESTER)) ? IsHighImpactNewsWindow() : false;
      g_lastNewsCheck = TimeCurrent();
      EvaluatePipeline(first, canTrade);
     }
   UpdateHUD();
  }

//+------------------------------------------------------------------+
//| 1. PROP GUARD (persisted)                                        |
//+------------------------------------------------------------------+
double GVGet(string k, double def) { return GlobalVariableCheck(g_gvp+k) ? GlobalVariableGet(g_gvp+k) : def; }
void   GVSet(string k, double v)   { GlobalVariableSet(g_gvp+k, v); }

// firm limits -> our guard lines (% of initial), buffered; CUSTOM uses the raw inputs
void ApplyPreset()
  {
   double d=0, m=0, t1=0, t2=0; int ref=MAXREF_STATIC, md=0; bool prof=false; string nm="CUSTOM";
   switch(InpPreset)
     {
      case PLAN_2STEP_STD_5: d=5; m=10; t1=8;  t2=5; md=3; nm="FP 2-Step Std 5%";   break;
      case PLAN_2STEP_STD_3: d=3; m=10; t1=8;  t2=5; md=3; nm="FP 2-Step Std 3%";   break;
      case PLAN_2STEP_FLEX:  d=4; m=12; t1=10; t2=8; md=1; nm="FP 2-Step Flex";     break;
      case PLAN_2STEP_PRO:   d=3; m=6;  t1=6;  t2=6; nm="FP 2-Step Pro";      break;
      case PLAN_1STEP_3:     d=3; m=12; t1=12; t2=0; nm="FP 1-Step 3%";       break;
      case PLAN_1STEP_2:     d=2; m=12; t1=12; t2=0; nm="FP 1-Step 2%";       break;
      case PLAN_1STEP_LABS:  d=1; m=3;  t1=5;  t2=0; ref=MAXREF_EOD; nm="FP LABS001"; break;
      case PLAN_ZERO:        d=3; m=5;  t1=0;  t2=0; ref=MAXREF_TRAILING; md=7; prof=true; nm="FP Zero"; break;
      default: break;
     }
   if(InpPreset == PLAN_CUSTOM)
     { g_dailyPct=InpDailyLossLimitPct; g_maxPct=InpMaxLossLimitPct; g_targetPct=InpProfitTargetPct; }
   else
     {
      g_dailyPct  = d * InpDailyBufferPct / 100.0;
      g_maxPct    = m * InpMaxBufferPct / 100.0;
      g_targetPct = (InpPhase<=0) ? 0 : (InpPhase==1 ? t1 : t2);
     }
   g_maxRef = ref; g_presetName = nm;
   g_minDays = (InpPreset == PLAN_CUSTOM) ? InpMinTradingDays : ((InpPhase<=0 && !prof) ? 0 : md);
   g_minDaysProfitable = prof;
   if(InpRiskPct > g_dailyPct)
      Print("WildChance WARNING: risk/trade ",InpRiskPct,"% exceeds the daily guard ",g_dailyPct,"%");
  }

// Count trading days (any deal opened) and profitable days (net closed P&L > 0) on
// this ACCOUNT since the guard started (GV "start"), by broker server date.
void CountTradingDays()
  {
   if(TimeCurrent() - g_lastDayCount < 60) return;
   g_lastDayCount = TimeCurrent();
   datetime start = (datetime)(long)GVGet("start", 0);
   if(start == 0) { start = TimeCurrent(); GVSet("start", (double)(long)start); }
   if(!HistorySelect(start - 86400, TimeCurrent() + 86400)) return;
   string days[]; string pdays[]; double pnl[];
   int n = HistoryDealsTotal();
   for(int i=0; i<n; i++)
     {
      ulong t = HistoryDealGetTicket(i);
      if(t == 0) continue;
      long type = HistoryDealGetInteger(t, DEAL_TYPE);
      if(type != DEAL_TYPE_BUY && type != DEAL_TYPE_SELL) continue;
      datetime when = (datetime)HistoryDealGetInteger(t, DEAL_TIME);
      if(when < start) continue;
      string key = TimeToString(when, TIME_DATE);
      long entry = HistoryDealGetInteger(t, DEAL_ENTRY);
      if(entry == DEAL_ENTRY_IN)
        {
         bool seen=false; for(int k=0;k<ArraySize(days);k++) if(days[k]==key){seen=true;break;}
         if(!seen){ int z=ArraySize(days); ArrayResize(days,z+1); days[z]=key; }
        }
      else
        {
         double net = HistoryDealGetDouble(t, DEAL_PROFIT) + HistoryDealGetDouble(t, DEAL_SWAP)
                    + HistoryDealGetDouble(t, DEAL_COMMISSION);
         int idx=-1; for(int k=0;k<ArraySize(pdays);k++) if(pdays[k]==key){idx=k;break;}
         if(idx<0){ idx=ArraySize(pdays); ArrayResize(pdays,idx+1); ArrayResize(pnl,idx+1); pdays[idx]=key; pnl[idx]=0; }
         pnl[idx] += net;
        }
     }
   g_tradeDays = ArraySize(days);
   g_profitDays = 0;
   for(int k=0;k<ArraySize(pnl);k++) if(pnl[k] > 0) g_profitDays++;
  }

bool g_guardLoaded = false;
int  g_breachCount = 0;

// MT5 reports balance / equity as 0 for a moment after a restart or reconnect. Nothing in the
// guard may be decided (or remembered) from those zeros.
bool AccountReady()
  {
   return TerminalInfoInteger(TERMINAL_CONNECTED) != 0
          && AccountInfoDouble(ACCOUNT_BALANCE) > 0 && AccountInfoDouble(ACCOUNT_EQUITY) > 0;
  }

void LoadGuardState()
  {
   if(!AccountReady()) { g_guardLoaded = false; return; }          // retried from CheckPropGuard
   // the per-account key prefix also needs the real login (0 before the account connects)
   g_gvp = "WC4_" + IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)) + "_";
   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   double saved = GVGet("init", 0);
   if(saved <= 0)
     {
      // a 0 start balance (captured while disconnected) poisons every floor → it and any lock
      // recorded with it are void; start clean from the real balance
      GlobalVariableDel(g_gvp + "maxlock"); GlobalVariableDel(g_gvp + "dailylock");
      GlobalVariableDel(g_gvp + "targetlock"); GlobalVariableDel(g_gvp + "dayref");
      GlobalVariableDel(g_gvp + "peakeq"); GlobalVariableDel(g_gvp + "peakeod");
      if(saved < 0 || GlobalVariableCheck(g_gvp + "init"))
         Print("WildChance: start balance was 0 (captured while disconnected) - guard locks cleared, re-captured ", bal);
     }
   if(InpInitialBalance > 0)        g_initialBalance = InpInitialBalance;
   else                             g_initialBalance = (saved > 0) ? saved : bal;     // first run captures
   GVSet("init", g_initialBalance);
   double dr = GVGet("dayref", 0);
   g_dayRef = (dr > 0) ? dr : MathMax(bal, AccountInfoDouble(ACCOUNT_EQUITY));
   g_guardLoaded = true;
  }

int ServerDayKey()
  {
   MqlDateTime dt; TimeToStruct(TimeTradeServer(), dt);
   return dt.year*10000 + dt.mon*100 + dt.day;
  }

// Returns true when NEW entries are allowed.
bool CheckPropGuard()
  {
   if(!AccountReady())                              // disconnected / starting: decide nothing
     {
      g_propStatus = "WAITING FOR ACCOUNT";
      g_breachCount = 0;
      return false;
     }
   if(!g_guardLoaded) LoadGuardState();
   double eq  = AccountInfoDouble(ACCOUNT_EQUITY);
   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   int dayKey = ServerDayKey();

   if((int)GVGet("daykey", 0) != dayKey)            // server midnight: new day reference
     {
      GVSet("daykey", dayKey);
      g_dayRef = MathMax(bal, eq);                  // FundingPips: higher of balance/equity
      GVSet("dayref", g_dayRef);
     }
   // high-water marks for trailing (peak equity) and EOD (peak day-start value) max loss
   if(eq > GVGet("peakeq", 0))      GVSet("peakeq", eq);
   if(g_dayRef > GVGet("peakeod", 0)) GVSet("peakeod", g_dayRef);
   double maxBase = g_initialBalance;
   if(g_maxRef == MAXREF_TRAILING) maxBase = MathMax(g_initialBalance, GVGet("peakeq", g_initialBalance));
   if(g_maxRef == MAXREF_EOD)      maxBase = MathMax(g_initialBalance, GVGet("peakeod", g_initialBalance));
   g_dailyFloor = g_dayRef - g_initialBalance * g_dailyPct / 100.0;
   g_maxFloor   = maxBase  - g_initialBalance * g_maxPct / 100.0;
   g_targetEq   = (g_targetPct > 0) ? g_initialBalance * (1.0 + g_targetPct / 100.0) : 0;

   // a breach must persist for 3 consecutive checks (~3 s) — a one-tick glitch never locks
   bool belowMax = (eq <= g_maxFloor), belowDay = (eq <= g_dailyFloor);
   g_breachCount = (belowMax || belowDay) ? g_breachCount + 1 : 0;
   bool confirmed = (g_breachCount >= 3);
   if(GVGet("maxlock", 0) > 0 || (belowMax && confirmed))
     {
      if(GVGet("maxlock", 0) == 0) { GVSet("maxlock", 1); Flatten("MAX_DD_GUARD"); Alert("WildChance: MAX-LOSS guard hit - trading stopped"); }
      g_propStatus = "LOCKED: MAX DD";
      return false;
     }
   CountTradingDays();
   if(GVGet("targetlock", 0) > 0 || (g_targetEq > 0 && eq >= g_targetEq))
     {
      int need = (g_minDaysProfitable) ? 0 : MathMax(0, g_minDays - g_tradeDays);
      if(GVGet("targetlock", 0) == 0)
        {
         GVSet("targetlock", 1); Flatten("TARGET_LOCK");
         if(need > 0) Alert("WildChance: TARGET reached - profits locked. Need ", need,
                            " more trading day(s): place ONE minimal (0.01) trade per day manually.");
         else         Alert("WildChance: phase TARGET reached - flat & locked");
        }
      g_propStatus = (need > 0) ? "TARGET - NEED "+IntegerToString(need)+" DAY(S)" : "TARGET PASSED";
      return false;
     }
   if((int)GVGet("dailylock", 0) == dayKey || (belowDay && confirmed))
     {
      if((int)GVGet("dailylock", 0) != dayKey) { GVSet("dailylock", dayKey); Flatten("DAILY_DD_GUARD"); Alert("WildChance: DAILY guard hit - paused until server midnight"); }
      g_propStatus = "DAILY LOCK";
      return false;
     }
   g_propStatus = "ACTIVE";
   return true;
  }

void Flatten(string reason)
  {
   for(int i=PositionsTotal()-1; i>=0; i--)
      if(posInfo.SelectByIndex(i) && (InpFlattenAllOnBreach || posInfo.Magic()==InpMagicNumber))
         trade.PositionClose(posInfo.Ticket());
   for(int i=OrdersTotal()-1; i>=0; i--)
     {
      ulong t = OrderGetTicket(i);
      if(t>0 && (InpFlattenAllOnBreach || OrderGetInteger(ORDER_MAGIC)==(long)InpMagicNumber))
         trade.OrderDelete(t);
     }
   g_tr.active = false;
   ClearPlan();
   g_lastResult = reason;
  }

//+------------------------------------------------------------------+
//| 2. LUNAR ENGINE - Meeus ch.49 true New/Full Moon (UTC)           |
//+------------------------------------------------------------------+
datetime TruePhase(double k)
  {
   double T  = k/1236.85;
   double jde= 2451550.09766 + 29.530588861*k + 0.00015437*T*T - 0.000000150*T*T*T + 0.00000000073*T*T*T*T;
   double E  = 1 - 0.002516*T - 0.0000074*T*T;
   double d2r= M_PI/180.0;
   double M  = d2r*(2.5534 + 29.10535670*k - 0.0000014*T*T - 0.00000011*T*T*T);
   double Mp = d2r*(201.5643 + 385.81693528*k + 0.0107582*T*T + 0.00001238*T*T*T - 0.000000058*T*T*T*T);
   double F  = d2r*(160.7108 + 390.67050284*k - 0.0016118*T*T - 0.00000227*T*T*T + 0.000000011*T*T*T*T);
   double Om = d2r*(124.7746 - 1.56375588*k + 0.0020672*T*T + 0.00000215*T*T*T);
   bool full = MathAbs(k - MathFloor(k) - 0.5) < 1e-6;
   double c1 = full ? -0.40614 : -0.40720, c2 = full ? 0.17302 : 0.17241;
   double c3 = full ?  0.01614 :  0.01608, c4 = full ? 0.01043 : 0.01039, c5 = full ? 0.00734 : 0.00739;
   double corr = c1*MathSin(Mp) + c2*E*MathSin(M) + c3*MathSin(2*Mp) + c4*MathSin(2*F)
               + c5*E*MathSin(Mp-M) - 0.00515*E*MathSin(Mp+M) + 0.00209*E*E*MathSin(2*M)
               - 0.00111*MathSin(Mp-2*F) - 0.00057*MathSin(Mp+2*F) + 0.00056*E*MathSin(2*Mp+M)
               - 0.00042*MathSin(3*Mp) + 0.00042*E*MathSin(M+2*F) + 0.00038*E*MathSin(M-2*F)
               - 0.00024*E*MathSin(2*Mp-M) - 0.00017*MathSin(Om);
   return (datetime)(long)((jde + corr - 2440587.5)*86400.0 - 69.0);
  }

void UpdateLunarState(datetime utcNow)
  {
   if(!InpUseLunarEngine) { g_lunarAllowedDir=0; g_inLunarObsWindow=false; g_lunarPhaseName="OFF"; return; }
   MqlDateTime d; TimeToStruct(utcNow, d);
   double k = MathFloor(((d.year + d.day_of_year/365.25) - 2000.0) * 12.3685) + 1;
   while(TruePhase(k) > utcNow) k -= 1;
   while(TruePhase(k+1) <= utcNow) k += 1;
   datetime prevNew = TruePhase(k), full = TruePhase(k+0.5), nextNew = TruePhase(k+1);
   double obs = InpLunarObsHours*3600.0;
   bool nearNew  = (utcNow - prevNew) <= obs || (nextNew - utcNow) <= obs;
   bool nearFull = MathAbs((double)(utcNow - full)) <= obs;
   g_inLunarObsWindow = nearNew || nearFull;
   g_lunarAgeDays = (utcNow - prevNew)/86400.0;
   g_nextFull = full; g_nextNew = nextNew;
   if(utcNow < full)
     {
      g_lunarAllowedDir = 1;
      g_lunarPhaseName  = nearNew ? "NEW MOON (OBS)" : "WAXING -> LONG";
     }
   else
     {
      g_lunarAllowedDir = -1;
      g_lunarPhaseName  = nearFull ? "FULL MOON (OBS)" : (nearNew ? "PRE-NEW (OBS)" : "WANING -> SHORT");
     }
  }

//+------------------------------------------------------------------+
//| 3. NEWS GUARD (MQL5 economic calendar, live only)                |
//+------------------------------------------------------------------+
// High-impact TIMED releases for this pair's currencies (and USD) within the window. Holidays and
// all-day / tentative entries are not releases and never pause trading.
bool IsHighImpactNewsWindow()
  {
   g_newsWhat = "";
   string base  = SymbolInfoString(_Symbol, SYMBOL_CURRENCY_BASE);
   string quote = SymbolInfoString(_Symbol, SYMBOL_CURRENCY_PROFIT);
   datetime now = TimeTradeServer();
   MqlCalendarValue values[];
   if(CalendarValueHistory(values, now - InpNewsMinsAfter*60, now + InpNewsMinsBefore*60) <= 0) return false;
   for(int i=0; i<ArraySize(values); i++)
     {
      MqlCalendarEvent ev;
      if(!CalendarEventById(values[i].event_id, ev) || ev.importance != CALENDAR_IMPORTANCE_HIGH) continue;
      if(ev.type == CALENDAR_TYPE_HOLIDAY || ev.time_mode != CALENDAR_TIMEMODE_DATETIME) continue;
      MqlCalendarCountry c;
      if(!CalendarCountryById(ev.country_id, c)) continue;
      if(c.currency == base || c.currency == quote || c.currency == "USD")
        {
         g_newsWhat = c.currency + " " + ev.name + " " + TimeToString(values[i].time, TIME_MINUTES);
         return true;
        }
     }
   return false;
  }

//+------------------------------------------------------------------+
//| 4. PIPELINE                                                      |
//+------------------------------------------------------------------+
int BarsBetween(datetime a, datetime b) { return (int)MathAbs((double)(b - a)) / PeriodSeconds(); }

void UpdateBB()
  {
   g_bbZ1 = BBZ(hBB1); g_bbZ2 = BBZ(hBB2);
   g_bbStr = (hBB1==INVALID_HANDLE) ? "OFF" :
             StringFormat("%s z%+.1f | %s z%+.1f", EnumToString(InpBBTF1), g_bbZ1, EnumToString(InpBBTF2), g_bbZ2);
   StringReplace(g_bbStr, "PERIOD_", "");
  }

// live price position inside the HTF band: z = (price - mid) / sigma ; sigma = (upper - mid) / 2
double BBZ(int h)
  {
   if(h==INVALID_HANDLE) return 0;
   double mid[1], up[1];
   if(CopyBuffer(h,0,0,1,mid)!=1 || CopyBuffer(h,1,0,1,up)!=1) return 0;
   double sd = (up[0]-mid[0])/2.0;
   return (sd>0) ? (SymbolInfoDouble(_Symbol,SYMBOL_BID)-mid[0])/sd : 0;
  }

// HTF stretched the reversal way: M (sell) needs z >= +1, W (buy) needs z <= -1
bool BBStretched(bool forSell)
  {
   if(hBB1==INVALID_HANDLE) return false;
   return forSell ? (g_bbZ1>=1 || g_bbZ2>=1) : (g_bbZ1<=-1 || g_bbZ2<=-1);
  }

void EvaluatePipeline(bool warmup, bool canTrade)
  {
   UpdateBB();
   const int bars = 1000;
   MqlRates rates[];
   ArraySetAsSeries(rates, false);
   int copied = CopyRates(_Symbol, _Period, 1, bars, rates);         // closed bars, oldest first
   if(copied < InpEmaLong + 20) { g_newSignalTxt = "LOADING HISTORY ("+IntegerToString(copied)+" bars)"; g_lastEvalBar = 0; return; }

   double fast[], slow[], mid[], lng[], atr[], adx[], rsi[];
   ArraySetAsSeries(fast,false); ArraySetAsSeries(slow,false); ArraySetAsSeries(mid,false);
   ArraySetAsSeries(lng,false);  ArraySetAsSeries(atr,false);  ArraySetAsSeries(adx,false);
   ArraySetAsSeries(rsi,false);
   if(CopyBuffer(hFast,0,1,copied,fast)!=copied || CopyBuffer(hSlow,0,1,copied,slow)!=copied ||
      CopyBuffer(hMid ,0,1,copied,mid )!=copied || CopyBuffer(hLong,0,1,copied,lng )!=copied ||
      CopyBuffer(hATR ,0,1,copied,atr )!=copied || CopyBuffer(hADX ,0,1,copied,adx )!=copied)
     { g_newSignalTxt = "LOADING INDICATORS"; g_lastEvalBar = 0; return; }
   bool haveRsi = (hRSI!=INVALID_HANDLE && CopyBuffer(hRSI,0,1,copied,rsi)==copied);

   datetime time[]; double open[], high[], low[], close[];
   ArrayResize(time,copied); ArrayResize(open,copied); ArrayResize(high,copied);
   ArrayResize(low,copied);  ArrayResize(close,copied);
   for(int i=0;i<copied;i++)
     { time[i]=rates[i].time; open[i]=rates[i].open; high[i]=rates[i].high; low[i]=rates[i].low; close[i]=rates[i].close; }

   // On the first evaluation replay ~200 bars so ID50 watches / hunts are warm (no trading).
   int start = warmup ? MathMax(InpEmaLong+5, copied-200) : copied-1;
   for(int n=start; n<copied; n++)
      EvaluateBar(n, copied, time, open, high, low, close, fast, slow, mid, lng, atr, adx, rsi, haveRsi,
                  (n==copied-1) && canTrade);
  }

void EvaluateBar(int n, int total, const datetime &time[], const double &open[], const double &high[],
                 const double &low[], const double &close[], const double &fast[], const double &slow[],
                 const double &mid[], const double &lng[], const double &atr[], const double &adx[],
                 const double &rsi[], bool haveRsi, bool live)
  {
   double atrN = (atr[n] > 0) ? atr[n] : _Point*10;
   double pip  = PipSize();
   g_adxVal    = adx[n];

   bool bull3=fast[n]>slow[n]&&slow[n]>mid[n]&&mid[n]>lng[n], bear3=fast[n]<slow[n]&&slow[n]<mid[n]&&mid[n]<lng[n];
   bool bull2=fast[n]>slow[n]&&slow[n]>mid[n], bear2=fast[n]<slow[n]&&slow[n]<mid[n];
   int level=0; string dir="Neutral";
   if(bull3){level=3;dir="Bullish";} else if(bear3){level=3;dir="Bearish";}
   else if(bull2){level=2;dir="Bullish";} else if(bear2){level=2;dir="Bearish";}
   else if(fast[n]>slow[n]){level=1;dir="Bullish";} else if(fast[n]<slow[n]){level=1;dir="Bearish";}
   g_cyclePhase  = dir;
   g_levelsCount = "LEVEL "+IntegerToString(level)+(dir=="Bullish"?" UP":(dir=="Bearish"?" DOWN":""));
   int pairs = (dir=="Bullish") ? (fast[n]>slow[n]?1:0)+(slow[n]>mid[n]?1:0)+(mid[n]>lng[n]?1:0)
                                : (fast[n]<slow[n]?1:0)+(slow[n]<mid[n]?1:0)+(mid[n]<lng[n]?1:0);
   g_emaAlign = (dir=="Neutral") ? "MIXED" : (dir=="Bullish"?"UP ":"DOWN ")+IntegerToString(pairs)+"/3";

   // [CHOP-TRAP]: v2.2 consolidation (EMA13-800 spread < 1.2 ATR) OR ADX below threshold
   bool consolidating = MathAbs(fast[n]-lng[n]) < atrN*1.2;
   g_isChop = consolidating || (InpUseChopFilter && g_adxVal < InpMinADX);
   g_marketState = (g_isChop ? "[CHOP-TRAP] ADX " : "TRENDING ADX ") + DoubleToString(g_adxVal,1);

   if(haveRsi && n>=40)
     {
      g_tdiPrice=(rsi[n]+rsi[n-1])/2.0;
      double s=0; for(int i=0;i<7;i++)  s+=rsi[n-i]; g_tdiSignal=s/7.0;
      double b=0; for(int i=0;i<34;i++) b+=rsi[n-i]; g_tdiBase=b/34.0;
      string st=(g_tdiPrice>g_tdiSignal&&g_tdiPrice>g_tdiBase)?"BULLISH":((g_tdiPrice<g_tdiSignal&&g_tdiPrice<g_tdiBase)?"BEARISH":"MIXED");
      g_tdiState=st+" (RSI "+DoubleToString(rsi[n],0)+")";
     }

   MqlDateTime dt; TimeToStruct(time[n], dt);
   g_inAsia   = InRange(dt.hour, SH(InpSessionAsiaStart),   SH(InpSessionAsiaEnd));
   g_inLondon = InRange(dt.hour, SH(InpSessionLondonStart), SH(InpSessionLondonEnd));
   g_inNY     = InRange(dt.hour, SH(InpSessionNYStart),     SH(InpSessionNYEnd));
   g_sessionStr = (g_inAsia?"Asia ":"")+(g_inLondon?"London ":"")+(g_inNY?"NY ":"");
   if(g_sessionStr=="") g_sessionStr="Off-session";
   double psH=0, psL=0;
   GetPriorSessionRange(total, time, high, low, dt, g_inLondon, g_inNY, n, psH, psL);
   string act = g_inNY?"NY":(g_inLondon?"London":(g_inAsia?"Asia":""));
   g_sessionBias = (psH>0 && close[n]>psH) ? act+" BULLISH" : ((psL>0 && close[n]<psL) ? act+" BEARISH" : act+" NEUTRAL");

   int lb=InpSwingLookback, lastPH=-1, lastPL=-1;
   for(int i=n-lb; i>lb; i--)
     {
      if(lastPH<0 && IsPivotHigh(high,i,lb,n+1)) lastPH=i;
      if(lastPL<0 && IsPivotLow(low,i,lb,n+1))   lastPL=i;
      if(lastPH>=0 && lastPL>=0) break;
     }
   bool highHunted = lastPH>=0 && high[n]>high[lastPH] && close[n]<high[lastPH] && !((n-1>lastPH) && high[n-1]>high[lastPH]);
   bool lowHunted  = lastPL>=0 && low[n]<low[lastPL]   && close[n]>low[lastPL]  && !((n-1>lastPL) && low[n-1]<low[lastPL]);
   if(highHunted) g_lastHighHuntT=time[n];                 // TIME, not window index (stable)
   if(lowHunted)  g_lastLowHuntT =time[n];
   g_stopHunt = (highHunted&&lowHunted)?"BOTH HUNTED":(highHunted?"HIGH HUNTED":(lowHunted?"LOW HUNTED":"NONE YET"));

   double tH=0,tL=0,pdH=0,pdL=0;
   GetDayLevels(time, open, high, low, dt, n, tH, tL, pdH, pdL);
   UpdateHuntsToday(time, high, low, dt, pdH, pdL, n);
   ScanMWPattern(n, time, high, low, close, level, dir, atrN, tH, tL, pdH, pdL, highHunted, lowHunted);
   UpdateID50(n, time, fast, slow, high, low, close, atrN);
   ComputeADR(dt.year*10000+dt.mon*100+dt.day, tH, tL, close[n], pip);
   BuildSignal();
   if(live) { ComputeTrendDay(); ExecuteFilteredSetup(time, high, low, close, atrN, n, pip); }
  }

//+------------------------------------------------------------------+
//| 5. EXECUTION WITH ALL GATES                                      |
//+------------------------------------------------------------------+
void ExecuteFilteredSetup(const datetime &time[], const double &high[], const double &low[],
                          const double &close[], double atrN, int n, double pip)
  {
   bool mwSig = ((g_signal=="BUY" || g_signal=="SELL") && StringFind(g_pattern,"@")>=0);
   bool idSig = (g_signal=="ID50 BUY" || g_signal=="ID50 SELL");
   if(!mwSig && !idSig) return;
   string key = mwSig ? g_patKey : "I"+IntegerToString((long)g_id50FiredT);
   if(key == g_lastTradeKey) return;                       // one attempt per setup
   g_lastTradeKey = key;
   if(idSig && !InpTradeID50) { g_newSignalTxt="ID50 signal (not traded)"; return; }

   bool   isBuy = (g_signal=="BUY" || g_signal=="ID50 BUY");
   string setup = mwSig ? (isBuy?"W":"M") : "ID50";
   double base  = mwSig ? g_patExtreme : (isBuy ? low[n] : high[n]);
   if(!mwSig) for(int k=1; k<5 && n-k>=0; k++) base = isBuy ? MathMin(base, low[n-k]) : MathMax(base, high[n-k]);

   double ask=SymbolInfoDouble(_Symbol,SYMBOL_ASK), bid=SymbolInfoDouble(_Symbol,SYMBOL_BID);
   double entry = isBuy ? ask : bid;
   double sl    = isBuy ? base - InpSLBufferXATR*atrN : base + InpSLBufferXATR*atrN;
   double risk  = ((isBuy && sl>=entry) || (!isBuy && sl<=entry)) ? 0 : MathAbs(entry-sl);   // wrong side -> min risk
   if(risk < InpMinSLxATR*atrN) { risk = InpMinSLxATR*atrN; sl = isBuy ? entry-risk : entry+risk; }

   string why = "";
   double spreadPips = (ask-bid)/pip;
   bool   spreadBad  = IsFxSymbol() ? (spreadPips > InpMaxSpreadPips)
                                    : (atrN > 0 && (ask-bid) > InpMaxSpreadATR * atrN);
   if(g_tr.active || CountMagicPositions() >= InpMaxConcurrent) why = "MAX CONCURRENT";
   else if(spreadBad)                                          why = "SPREAD "+DoubleToString(spreadPips,1)+"p";
   else if(g_isChop)                                           why = "[CHOP-TRAP] ADX "+DoubleToString(g_adxVal,1);
   else if(g_newsBlocked)                                      why = "[NEWS] high-impact window";
   else if(InpSessionFilter && !(g_inLondon || g_inNY))        why = "OUTSIDE LONDON/NY";
   else if(InpMaxADRUsed>0 && g_adrUsedPct>InpMaxADRUsed)      why = "ADR "+DoubleToString(g_adrUsedPct,0)+"% USED";
   else if(InpMacroBias==MACRO_BUY_ONLY && !isBuy)             why = "MACRO LOCK BUY-ONLY";
   else if(InpMacroBias==MACRO_SELL_ONLY && isBuy)             why = "MACRO LOCK SELL-ONLY";
   else if(InpUseLunarEngine && ((isBuy && g_lunarAllowedDir<0) || (!isBuy && g_lunarAllowedDir>0))) why = "LUNAR MISMATCH";
   else if(InpUseLunarEngine && g_inLunarObsWindow && InpLunarShiftMode==LUNAR_OBSERVE_PAUSE)        why = "LUNAR OBSERVATION";
   else if(mwSig && InpRequireSweepOrLoc && g_location=="N/A" && !RecentHunt(isBuy, time[n]))      why = "NO SWEEP / LOCATION";
   else if(mwSig && InpRequireBBStretch && !BBStretched(!isBuy))  why = "HTF BB NOT STRETCHED";
   else if(idSig && InpRequireBiasAlign && ((isBuy && StringFind(g_sessionBias,"BEARISH")>=0) || (!isBuy && StringFind(g_sessionBias,"BULLISH")>=0))) why = "AGAINST SESSION BIAS";
   else if(InpRequireTDI && ((isBuy && g_tdiPrice<g_tdiSignal) || (!isBuy && g_tdiPrice>g_tdiSignal))) why = "TDI DISAGREES";
   else if(idSig && InpID50MaxTrendDay>0 && g_trendDayAge>InpID50MaxTrendDay) why = "D1 TREND DAY "+IntegerToString(g_trendDayAge);
   else if(InpMaxSLxATR>0 && risk > InpMaxSLxATR*atrN)         why = "SL TOO WIDE ("+DoubleToString(risk/atrN,1)+"xATR)";
   if(why != "") { g_newSignalTxt = "BLOCKED: "+why; Print("WildChance blocked ",_Symbol,": ",why);
                   LogSetup(isBuy, setup, entry, sl, "BLOCKED: "+why); return; }

   double appRisk = 0;
   if(InpUseAppGate && !MQLInfoInteger(MQL_TESTER))
     {
      string gwhy = "";
      int g = AppGate(isBuy, appRisk, gwhy);
      if(g < 0)                                             // one retry: a slow / waking server is not a "no"
        { Sleep(1500); g = AppGate(isBuy, appRisk, gwhy); }
      if(g == 0 || (g < 0 && !InpGateFailOpen))
        {
         g_newSignalTxt = "BLOCKED: APP "+gwhy;
         Print("WildChance blocked by app gate ",_Symbol,": ",gwhy);
         LogSetup(isBuy, setup, entry, sl, "COUNCIL: "+gwhy);
         return;
        }
      if(g < 0) Print("WildChance: app gate unreachable (",gwhy,") - fail-open, EA filters only");
     }

   if(InpFixedSLPips > 0) { risk = InpFixedSLPips * pip; sl = isBuy ? entry-risk : entry+risk; }
   if(InpRandomizeSL) { sl += (isBuy?-1:1) * (1 + MathRand()%3) * pip; risk = MathAbs(entry-sl); }
   // Respect the broker's minimum stop distance
   double minStop = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * _Point;
   if(risk < minStop) { risk = minStop; sl = isBuy ? entry-risk : entry+risk; }

   int d = isBuy ? 1 : -1;
   double riskPct = InpRiskPct;
   if(InpUseLunarEngine && g_inLunarObsWindow && InpLunarShiftMode==LUNAR_HALF_RISK) riskPct *= 0.5;
   if(InpUseAppRisk && appRisk > 0) riskPct = MathMin(riskPct, appRisk);   // app budget only lowers risk
   double tpR  = (InpTargetTP<=1) ? InpTP1R : (InpTargetTP==2 ? InpTP2R : InpTP3R);
   double lots = (InpSizingMode == SIZING_TARGET) ? TargetLots(risk*tpR, risk, riskPct)
                                                : CalculateLotSize(risk, riskPct);
   if(lots <= 0) { g_newSignalTxt = "BLOCKED: MIN LOT WOULD OVER-RISK"; return; }

   sl = NormalizeDouble(sl, _Digits);
   double tp1=NormalizeDouble(entry+d*risk*InpTP1R,_Digits), tp2=NormalizeDouble(entry+d*risk*InpTP2R,_Digits);
   double tp3=NormalizeDouble(entry+d*risk*InpTP3R,_Digits);
   string comment = "WC4_"+setup+(InpUseLunarEngine?(isBuy?"_NM":"_FM"):"");
   bool sent = isBuy ? trade.Buy(lots,_Symbol,0,sl,tp3,comment) : trade.Sell(lots,_Symbol,0,sl,tp3,comment);
   if(!sent || (trade.ResultRetcode()!=TRADE_RETCODE_DONE && trade.ResultRetcode()!=TRADE_RETCODE_PLACED))
     { Print("WildChance order error ",trade.ResultRetcode()," ",trade.ResultRetcodeDescription()); return; }

   if(!FindOwnPosition()) { Print("WildChance: order sent but position not found yet"); return; }
   g_tr.active=true; g_tr.dir=d; g_tr.setup=setup; g_tr.openTime=TimeCurrent();
   g_tr.maxHoldSeconds = InpUseLunarEngine ? (isBuy?InpNewMoonHoldDays:InpFullMoonHoldDays)*86400 : InpMaxBarsInTrade*PeriodSeconds();
   g_tr.entry=posInfo.PriceOpen(); g_tr.sl=sl; g_tr.risk=MathAbs(g_tr.entry-sl);
   g_tr.tp1=tp1; g_tr.tp2=tp2; g_tr.tp3=tp3; g_tr.initLots=lots; g_tr.h1=false; g_tr.h2=false;
   g_newSignalTxt = (isBuy?"LIVE BUY ":"LIVE SELL ")+setup+" "+DoubleToString(lots,2)+" lots"+
                    (InpSizingMode==SIZING_TARGET ? " (target TP"+IntegerToString(InpTargetTP)+")" : " (risk %)");
   if(InpDrawTradePlan) DrawPlan();
   if(InpAlertOnEntry) Alert("WildChance ",_Symbol," ",g_newSignalTxt," SL ",DoubleToString(sl,_Digits));
   LogSetup(isBuy, setup, g_tr.entry, sl, "ENTERED");
  }

bool FindOwnPosition()
  {
   for(int i=PositionsTotal()-1; i>=0; i--)
      if(posInfo.SelectByIndex(i) && posInfo.Magic()==InpMagicNumber && posInfo.Symbol()==_Symbol)
        { g_tr.ticket = posInfo.Ticket(); return true; }
   return false;
  }

//+------------------------------------------------------------------+
//| 6. POSITION MANAGEMENT (TP1/TP2 partials, BE, time exits)        |
//+------------------------------------------------------------------+
void ManageLivePosition()
  {
   if(!g_tr.active) return;
   if(!posInfo.SelectByTicket(g_tr.ticket))
     { g_tr.active=false; g_lastResult="CLOSED ("+g_tr.setup+")"; ClearPlan(); return; }
   bool buy = (g_tr.dir>0);
   double px = buy ? SymbolInfoDouble(_Symbol,SYMBOL_BID) : SymbolInfoDouble(_Symbol,SYMBOL_ASK);

   if((TimeCurrent()-g_tr.openTime) >= g_tr.maxHoldSeconds)
     { trade.PositionClose(g_tr.ticket); g_tr.active=false; g_lastResult="TIME EXIT"; ClearPlan(); return; }
   if(InpCloseBeforeWknd)
     {
      MqlDateTime dt; TimeToStruct(TimeGMT(), dt);
      if(dt.day_of_week==5 && dt.hour>=InpFridayCloseHour)
        { trade.PositionClose(g_tr.ticket); g_tr.active=false; g_lastResult="WEEKEND EXIT"; ClearPlan(); return; }
     }

   double minLot=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN), step=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP);
   double third = MathFloor((g_tr.initLots/3.0)/step)*step;
   if(!g_tr.h1 && ((buy && px>=g_tr.tp1) || (!buy && px<=g_tr.tp1)))
     {
      if(InpUsePartials && third>=minLot && posInfo.Volume()-third>=minLot) trade.PositionClosePartial(g_tr.ticket, third);
      g_tr.h1 = true;
      if(InpBEAfterTP1 && trade.PositionModify(g_tr.ticket, NormalizeDouble(g_tr.entry,_Digits), g_tr.tp3))
         g_tr.sl = g_tr.entry;
      if(InpDrawTradePlan) DrawPlan();
     }
   if(g_tr.h1 && !g_tr.h2 && ((buy && px>=g_tr.tp2) || (!buy && px<=g_tr.tp2)))
     {
      if(InpUsePartials && posInfo.SelectByTicket(g_tr.ticket) && third>=minLot && posInfo.Volume()-third>=minLot)
         trade.PositionClosePartial(g_tr.ticket, third);
      g_tr.h2 = true;
      if(InpDrawTradePlan) DrawPlan();
     }
  }

double CalculateLotSize(double slDist, double riskPct)
  {
   if(slDist<=0) return 0;
   double riskMoney = AccountInfoDouble(ACCOUNT_EQUITY)*riskPct/100.0;
   double tickSize  = SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE);
   double tickValue = SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_VALUE);
   if(tickSize<=0 || tickValue<=0) return 0;
   double lossPerLot = (slDist/tickSize)*tickValue;
   double minLot=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN), maxLot=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MAX);
   double step=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP);
   double lots = MathFloor((riskMoney/lossPerLot)/step)*step;
   if(lots < minLot) return 0;                    // SKIP: min lot would exceed the risk budget
   lots = MathMin(maxLot, lots);
   int digits = (int)MathMax(0, MathRound(-MathLog10(step)));
   return NormalizeDouble(lots, digits);
  }

// Owner's method: lot so a move of tpDist pays the target; never above the risk-% cap.
double TargetLots(double tpDist, double slDist, double riskPct)
  {
   double tickSize  = SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE);
   double tickValue = SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_VALUE);
   if(tpDist<=0 || tickSize<=0 || tickValue<=0) return 0;
   double target = (InpTargetUSD > 0) ? InpTargetUSD : g_initialBalance * InpTargetPct / 100.0;
   double perLot = (tpDist/tickSize)*tickValue;            // $ at TP for 1.00 lot (works for crosses)
   double step   = SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP);
   double minLot = SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN);
   double want   = MathCeil((target/perLot)/step - 1e-9)*step;   // round UP: TP reaches the target
   want = MathMax(minLot, want);
   double cap = CalculateLotSize(slDist, riskPct);            // 0 when even min lot over-risks
   if(cap <= 0) return 0;
   double lots = MathMin(want, cap);
   int digits = (int)MathMax(0, MathRound(-MathLog10(step)));
   return NormalizeDouble(lots, digits);
  }

int CountMagicPositions()
  {
   int c=0;
   for(int i=0;i<PositionsTotal();i++) if(posInfo.SelectByIndex(i) && posInfo.Magic()==InpMagicNumber) c++;
   return c;
  }

void SyncExistingPosition()
  {
   if(!FindOwnPosition()) return;
   g_tr.active=true; g_tr.dir=(posInfo.PositionType()==POSITION_TYPE_BUY)?1:-1;
   g_tr.entry=posInfo.PriceOpen(); g_tr.sl=posInfo.StopLoss(); g_tr.tp3=posInfo.TakeProfit();
   g_tr.risk=MathAbs(g_tr.entry-g_tr.sl); if(g_tr.risk<=0) g_tr.risk=_Point*100;
   g_tr.tp1=g_tr.entry+g_tr.dir*g_tr.risk*InpTP1R; g_tr.tp2=g_tr.entry+g_tr.dir*g_tr.risk*InpTP2R;
   g_tr.initLots=posInfo.Volume(); g_tr.openTime=(datetime)posInfo.Time(); g_tr.setup="SYNC";
   g_tr.h1 = (g_tr.dir>0) ? (g_tr.sl>=g_tr.entry) : (g_tr.sl>0 && g_tr.sl<=g_tr.entry);  // already at BE?
   g_tr.h2 = false;
   g_tr.maxHoldSeconds = InpUseLunarEngine ? (g_tr.dir>0?InpNewMoonHoldDays:InpFullMoonHoldDays)*86400 : InpMaxBarsInTrade*PeriodSeconds();
  }

//+------------------------------------------------------------------+
//| 7. BTMM HELPERS (v2.2)                                           |
//+------------------------------------------------------------------+
double PipSize()
  {
   if(InpPipSize>0) return InpPipSize;
   int digits=(int)SymbolInfoInteger(_Symbol,SYMBOL_DIGITS);
   string s=_Symbol; StringToUpper(s);
   if(StringFind(s,"BTC")>=0 || StringFind(s,"XBT")>=0) return 1.0;   // BTC like gold: $ pip ($1)
   if(digits==3 || digits==5) return _Point*10.0;
   if(StringFind(s,"XAU")>=0 || StringFind(s,"GOLD")>=0) return _Point*10.0;
   return _Point;
  }

// Forex pair (3/5-digit quote, no metal / crypto / oil / index name)?
bool IsFxSymbol()
  {
   string s = _Symbol; StringToUpper(s);
   int d = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   if(d != 3 && d != 5) return false;
   string non[] = {"XAU","XAG","GOLD","SILVER","BTC","ETH","OIL","WTI","BRENT","XTI","XBR","US30","US100","US500",
                   "NAS","SPX","DJ","GER","UK100","JP225","NIK","HK50"};
   for(int i = 0; i < ArraySize(non); i++) if(StringFind(s, non[i]) >= 0) return false;
   return true;
  }

// Every setup the EA finds → the app's setup log (owner sync key) and the phone (MT5 push).
void LogSetup(bool isBuy, string setup, double entry, double sl, string outcome)
  {
   string side = isBuy ? "BUY" : "SELL";
   bool entered = (outcome == "ENTERED");
   if(InpPhonePush && TerminalInfoInteger(TERMINAL_NOTIFICATIONS_ENABLED) && (entered || g_quality >= InpPhoneMinQuality))
      SendNotification("WildChance " + _Symbol + " " + side + " " + setup + " q" + IntegerToString(g_quality)
                       + "/10 @" + DoubleToString(entry, _Digits) + " - " + outcome);
   if(g_token == "" || g_role != "owner" || MQLInfoInteger(MQL_TESTER)) return;
   string js = StringFormat("{\"symbol\":\"%s\",\"side\":\"%s\",\"setup\":\"%s\",\"quality\":%d,"
                            "\"entry\":%s,\"stop\":%s,\"outcome\":\"%s\",\"tf\":\"%s\",\"session\":\"%s\",\"t\":%I64d}",
                            JsonSafe(_Symbol), side, setup, g_quality, DoubleToString(entry, _Digits),
                            DoubleToString(sl, _Digits), JsonSafe(outcome), EnumToString(_Period),
                            JsonSafe(g_sessionStr), (long)TimeGMT());
   PostJson("/bridge/setup", js);
  }

bool IsPivotHigh(const double &high[], int idx, int lb, int total)
  {
   if(idx-lb<0 || idx+lb>=total) return false;
   for(int i=1;i<=lb;i++) if(high[idx-i]>=high[idx] || high[idx+i]>=high[idx]) return false;
   return true;
  }
bool IsPivotLow(const double &low[], int idx, int lb, int total)
  {
   if(idx-lb<0 || idx+lb>=total) return false;
   for(int i=1;i<=lb;i++) if(low[idx-i]<=low[idx] || low[idx+i]<=low[idx]) return false;
   return true;
  }
// session inputs are GMT; bars are in broker server time → shift by the measured server offset
int SH(int gmtHour) { return (int)(((gmtHour + ServerToUtcOffset() / 3600) % 24 + 24) % 24); }

bool InRange(int hour,int s,int e) { return (s<=e) ? (hour>=s && hour<e) : (hour>=s || hour<e); }

void GetPriorSessionRange(int total,const datetime &time[],const double &high[],const double &low[],
                          MqlDateTime &dt,bool inLondon,bool inNY,int n,double &oH,double &oL)
  {
   oH=0; oL=0; double hh=-1, ll=1e15; bool found=false;
   int rs = inNY ? SH(InpSessionLondonStart) : (inLondon ? SH(InpSessionAsiaStart) : SH(InpSessionNYStart));
   int re = inNY ? SH(InpSessionLondonEnd)   : (inLondon ? SH(InpSessionAsiaEnd)   : SH(InpSessionNYEnd));
   for(int i=n-1; i>=MathMax(0,n-300); i--)
     {
      MqlDateTime d; TimeToStruct(time[i], d);
      if(d.day!=dt.day) break;
      if(InRange(d.hour, rs, re)) { found=true; hh=MathMax(hh,high[i]); ll=MathMin(ll,low[i]); }
     }
   if(found) { oH=hh; oL=ll; }
  }

void GetDayLevels(const datetime &time[],const double &open[],const double &high[],const double &low[],
                  MqlDateTime &dt,int n,double &tH,double &tL,double &pH,double &pL)
  {
   tH=0; tL=0; pH=0; pL=0;
   double a=-1,b=1e15,c=-1,e=1e15; int pY=-1,pM=-1,pD=-1;
   for(int i=n; i>=0; i--)
     {
      MqlDateTime d; TimeToStruct(time[i], d);
      if(d.year==dt.year && d.mon==dt.mon && d.day==dt.day)
        { a=MathMax(a,high[i]); b=MathMin(b,low[i]); g_todayOpen=open[i]; continue; }
      if(pD<0) { pY=d.year; pM=d.mon; pD=d.day; }
      if(d.year==pY && d.mon==pM && d.day==pD) { c=MathMax(c,high[i]); e=MathMin(e,low[i]); }
      else break;
     }
   if(a>=0) tH=a;
   if(b<1e15) tL=b;
   if(c>=0) pH=c;
   if(e<1e15) pL=e;
  }

bool AsiaRange(const datetime &time[],const double &high[],const double &low[],int n,double &ah,double &al)
  {
   bool found=false; double hh=-1, ll=1e15; datetime endT=0;
   for(int i=n; i>=MathMax(0,n-2500); i--)
     {
      MqlDateTime d; TimeToStruct(time[i], d);
      bool inA = InRange(d.hour, SH(InpSessionAsiaStart), SH(InpSessionAsiaEnd));
      if(!found) { if(!inA) continue; found=true; endT=time[i]; }
      if(!inA) break;
      hh=MathMax(hh,high[i]); ll=MathMin(ll,low[i]);
     }
   if(!found || hh<0 || time[n]-endT > 20*3600) return false;
   ah=hh; al=ll; return true;
  }

void UpdateHuntsToday(const datetime &time[],const double &high[],const double &low[],
                      MqlDateTime &dt,double pdHigh,double pdLow,int n)
  {
   int key=dt.year*10000+dt.mon*100+dt.day;
   if(key!=g_huntDayKey) { g_huntDayKey=key; g_hAsiaH=false; g_hAsiaL=false; g_hPDH=false; g_hPDL=false; }
   double ah=0, al=0;
   if(!g_inAsia && AsiaRange(time,high,low,n,ah,al))
     { if(high[n]>ah) g_hAsiaH=true; if(low[n]<al) g_hAsiaL=true; }
   if(pdHigh>0 && high[n]>pdHigh) g_hPDH=true;
   if(pdLow>0  && low[n] <pdLow)  g_hPDL=true;
  }

bool RecentHunt(bool isBuy, datetime now)
  {
   int win = InpHuntWindowMult*3;
   if(isBuy) return (g_lastLowHuntT>0  && BarsBetween(g_lastLowHuntT, now)<=win)  || g_hAsiaL || g_hPDL;
   return           (g_lastHighHuntT>0 && BarsBetween(g_lastHighHuntT, now)<=win) || g_hAsiaH || g_hPDH;
  }

string LocationOf(double px,double tH,double tL,double pdH,double pdL,bool highSide,double tol)
  {
   if(highSide) { if(tH>0 && MathAbs(px-tH)<=tol) return "HOD"; if(pdH>0 && MathAbs(px-pdH)<=tol) return "PDH"; }
   else         { if(tL>0 && MathAbs(px-tL)<=tol) return "LOD"; if(pdL>0 && MathAbs(px-pdL)<=tol) return "PDL"; }
   return "N/A";
  }

int ScoreCandidate(int level,string dir,bool wantBull,bool atLoc,bool huntNearby,const double &close[],int n,bool isW,int pivotIdx)
  {
   int score=0;
   if((wantBull && dir=="Bearish") || (!wantBull && dir=="Bullish")) score+=MathMin(level,3);
   if(atLoc) score+=2;
   if(huntNearby && (n-pivotIdx)<=InpHuntWindowMult*2) score+=2;
   if(InpUseBBConfluence && BBStretched(!isW)) score+=2;     // HTF Bollinger extreme confluence
   score+=1;
   for(int i=n; i>n-InpConfirmLookback && i>=1; i--)
      if((isW && close[i]>close[i-1]) || (!isW && close[i]<close[i-1])) { score+=1; break; }
   if((isW && close[n]>close[MathMax(n-1,0)]) || (!isW && close[n]<close[MathMax(n-1,0)])) score+=1;
   return (int)MathMin(score,10);
  }

void ScanMWPattern(int n,const datetime &time[],const double &high[],const double &low[],const double &close[],
                   int level,string dir,double atrN,double tH,double tL,double pdH,double pdL,bool highHunted,bool lowHunted)
  {
   double tol=InpEqualityXATR*atrN;
   int total=n+1, lb=InpSwingLookback;
   g_pattern="Watching next level..."; g_location="N/A"; g_quality=0; g_patKey="";
   int ph2=-1, ph1=-1;
   for(int i=n-lb; i>lb; i--)
      if(IsPivotHigh(high,i,lb,total))
        { if(ph2<0) ph2=i; else if(ph2-i<=InpMaxBarsBetween) { ph1=i; break; } else break; }
   if(ph2>=0 && ph1>=0 && MathAbs(high[ph2]-high[ph1])<=tol)
     {
      double midLow=1e15; for(int i=ph1+1;i<ph2;i++) midLow=MathMin(midLow,low[i]);
      if(midLow<1e15 && (MathMin(high[ph1],high[ph2])-midLow) > 0.3*atrN)
        {
         string loc=LocationOf(high[ph2],tH,tL,pdH,pdL,true,InpLocTolXATR*atrN);
         int score=ScoreCandidate(level,dir,false,loc!="N/A",highHunted,close,n,false,ph2);
         g_quality=score; g_location=loc; g_patExtreme=MathMax(high[ph1],high[ph2]);
         g_patKey="M"+IntegerToString((long)time[ph2]);
         g_pattern=(score>=InpMinQualityScore && level>=InpMinLevelsForSig) ? "M @ "+loc+" -> SELL" : "M forming -> SELL?";
        }
     }
   int pl2=-1, pl1=-1;
   for(int i=n-lb; i>lb; i--)
      if(IsPivotLow(low,i,lb,total))
        { if(pl2<0) pl2=i; else if(pl2-i<=InpMaxBarsBetween) { pl1=i; break; } else break; }
   if(pl2>=0 && pl1>=0 && MathAbs(low[pl2]-low[pl1])<=tol)
     {
      double midHigh=-1; for(int i=pl1+1;i<pl2;i++) midHigh=MathMax(midHigh,high[i]);
      if(midHigh>=0 && (midHigh-MathMax(low[pl1],low[pl2])) > 0.3*atrN)
        {
         string loc=LocationOf(low[pl2],tH,tL,pdH,pdL,false,InpLocTolXATR*atrN);
         int score=ScoreCandidate(level,dir,true,loc!="N/A",lowHunted,close,n,true,pl2);
         if(score>=g_quality)
           {
            g_quality=score; g_location=loc; g_patExtreme=MathMin(low[pl1],low[pl2]);
            g_patKey="W"+IntegerToString((long)time[pl2]);
            g_pattern=(score>=InpMinQualityScore && level>=InpMinLevelsForSig) ? "W @ "+loc+" -> BUY" : "W forming -> BUY?";
           }
        }
     }
  }

void UpdateID50(int n,const datetime &time[],const double &fast[],const double &slow[],const double &high[],
                const double &low[],const double &close[],double atrN)
  {
   if(n>=1)
     {
      if(fast[n-1]<=slow[n-1] && fast[n]>slow[n]) g_id50Watch= 1;
      if(fast[n-1]>=slow[n-1] && fast[n]<slow[n]) g_id50Watch=-1;
     }
   if(StringFind(g_id50Status,"Fired")>=0 && BarsBetween(g_id50FiredT,time[n])>0) g_id50Status="None yet";
   double tol=InpID50ToleranceXATR*atrN;
   if(g_id50Watch==1)
     {
      g_id50Status="Watching for ID50 BUY";
      if(low[n]<=slow[n]+tol && low[n]>=slow[n]-tol*3 && close[n]>slow[n] && close[n]>close[MathMax(n-1,0)])
        { g_id50Status="ID50 BUY Fired"; g_id50Watch=0; g_id50FiredT=time[n]; }
     }
   else if(g_id50Watch==-1)
     {
      g_id50Status="Watching for ID50 SELL";
      if(high[n]>=slow[n]-tol && high[n]<=slow[n]+tol*3 && close[n]<slow[n] && close[n]<close[MathMax(n-1,0)])
        { g_id50Status="ID50 SELL Fired"; g_id50Watch=0; g_id50FiredT=time[n]; }
     }
  }

void ComputeADR(int dayKey,double tH,double tL,double px,double pip)
  {
   if(g_adrAvg<=0 || dayKey!=g_adrDayKey)
     {
      MqlRates dr[];
      int c=CopyRates(_Symbol,PERIOD_D1,1,InpADRDays,dr);
      if(c>0) { double s=0; for(int i=0;i<c;i++) s+=dr[i].high-dr[i].low; g_adrAvg=s/c; g_adrDayKey=dayKey; }
     }
   if(g_adrAvg<=0) return;
   g_adrPips=g_adrAvg/pip;
   g_adrUsedPct=((tH-tL)/g_adrAvg)*100.0;
   g_adrTag=(g_adrUsedPct<60)?"NORMAL":(g_adrUsedPct<90?"LATE":"EXHAUSTED");
  }

void ComputeTrendDay()
  {
   double f[], s[];
   g_trendDayAge=0; g_trendDay="N/A";
   if(hD1F==INVALID_HANDLE || hD1S==INVALID_HANDLE) return;
   int cf=CopyBuffer(hD1F,0,1,60,f), cs=CopyBuffer(hD1S,0,1,60,s);    // completed days only
   int cnt=MathMin(cf,cs);
   if(cnt<2) return;
   int now=(f[cnt-1]>s[cnt-1])?1:-1, days=0;
   for(int i=cnt-1;i>=0;i--) { if(((f[i]>s[i])?1:-1)==now) days++; else break; }
   g_trendDayAge=days;
   g_trendDay="DAY "+IntegerToString(days)+(now>0?" UP":" DOWN");
  }

void BuildSignal()
  {
   if(StringFind(g_pattern,"->")>=0 && StringFind(g_pattern,"@")>=0)
     { g_signal=(StringFind(g_pattern,"SELL")>=0)?"SELL":"BUY"; return; }
   if(g_id50Status=="ID50 BUY Fired")  { g_signal="ID50 BUY";  return; }
   if(g_id50Status=="ID50 SELL Fired") { g_signal="ID50 SELL"; return; }
   if(StringFind(g_id50Status,"Watching")>=0) { g_signal="WATCH "+(g_id50Watch>0?"ID50 BUY":"ID50 SELL"); return; }
   if(StringFind(g_pattern,"forming")>=0) { g_signal=g_pattern; return; }
   g_signal="WAIT";
  }

//+------------------------------------------------------------------+
//| 8. DATA PUSH  EA -> app /bridge (broker bars = free real-time feed) |
//+------------------------------------------------------------------+
ENUM_TIMEFRAMES TfFromStr(string t)
  {
   if(t=="M15") return PERIOD_M15; if(t=="H1") return PERIOD_H1; if(t=="H4") return PERIOD_H4;
   if(t=="D1")  return PERIOD_D1;  if(t=="W1") return PERIOD_W1;
   return PERIOD_CURRENT;
  }
string TfName(ENUM_TIMEFRAMES tf)
  {
   switch(tf){ case PERIOD_M15: return "M15"; case PERIOD_H1: return "H1"; case PERIOD_H4: return "H4";
               case PERIOD_D1: return "D1"; case PERIOD_W1: return "W1"; default: return ""; }
  }

void SetupSendTFs()
  {
   string parts[]; int n = StringSplit(InpSendTFs, ',', parts);
   ArrayResize(g_sendTF,0); ArrayResize(g_sentBar,0); ArrayResize(g_sentInit,0);
   for(int i=0;i<n;i++)
     {
      string t=parts[i]; StringTrimLeft(t); StringTrimRight(t); StringToUpper(t);
      ENUM_TIMEFRAMES tf=TfFromStr(t);
      if(tf==PERIOD_CURRENT) continue;
      int k=ArraySize(g_sendTF);
      ArrayResize(g_sendTF,k+1); ArrayResize(g_sentBar,k+1); ArrayResize(g_sentInit,k+1);
      g_sendTF[k]=tf; g_sentBar[k]=0; g_sentInit[k]=false;
     }
   g_sendStatus = InpSendData ? "WAITING" : "OFF";
  }

// broker server time -> UTC (offset rounded to 15 min)
long ServerToUtcOffset()
  {
   long off = (long)(TimeTradeServer() - TimeGMT());
   return (long)MathRound(off/900.0)*900;
  }

bool PostJson(string path, string body)
  {
   string url = InpAppUrl; if(StringSubstr(url, StringLen(url)-1)=="/") url = StringSubstr(url,0,StringLen(url)-1);
   url += path;                                   // token goes in a header, never in the URL/logs
   char data[], result[]; string resHeaders;
   StringToCharArray(body, data, 0, StringLen(body), CP_UTF8);
   string headers = "Content-Type: application/json\r\nX-Bridge-Token: " + g_token + "\r\n";
   ResetLastError();
   int code = WebRequest("POST", url, headers, 5000, data, result, resHeaders);
   if(code == -1)
     {
      int err = GetLastError();
      g_sendStatus = (err == 4014) ? "BLOCKED: allow "+InpAppUrl+" in Options" : "ERR "+IntegerToString(err);
      return false;
     }
   g_sendStatus = (code==200) ? "OK "+TimeToString(TimeCurrent(),TIME_MINUTES) : "HTTP "+IntegerToString(code);
   return code==200;
  }

//+------------------------------------------------------------------+
//| App gate: 1 = GO, 0 = blocked by the app, -1 = app unreachable     |
//+------------------------------------------------------------------+
int AppGate(bool isBuy, double &appRisk, string &reason)
  {
   appRisk = 0; reason = "";
   string url = InpAppUrl; if(StringSubstr(url, StringLen(url)-1)=="/") url = StringSubstr(url,0,StringLen(url)-1);
   string sym = (InpAppSymbol!="") ? InpAppSymbol : _Symbol;
   url += "/bridge/gate?symbol=" + sym + "&side=" + (isBuy ? "BUY" : "SELL");
   char data[], result[]; string resHeaders;
   string headers = "X-Bridge-Token: " + g_token + "\r\n";
   ResetLastError();
   int code = WebRequest("GET", url, headers, MathMax(2000, InpGateTimeoutMs), data, result, resHeaders);
   if(code == -1) { reason = "ERR "+IntegerToString(GetLastError()); return -1; }
   if(code != 200) { reason = "HTTP "+IntegerToString(code); return -1; }
   string js = CharArrayToString(result, 0, WHOLE_ARRAY, CP_UTF8);
   appRisk = JsonNum(js, "risk_pct");
   reason  = JsonStr(js, "reason");
   if(StringFind(js, "\"allow\":true") >= 0)  return 1;
   if(StringFind(js, "\"allow\":false") >= 0) return 0;
   reason = "BAD REPLY"; return -1;
  }

double JsonNum(string js, string key)
  {
   int p = StringFind(js, "\""+key+"\":");
   if(p < 0) return 0;
   p += StringLen(key) + 3;
   int e = p;
   while(e < StringLen(js)) { ushort c = StringGetCharacter(js, e); if(c==',' || c=='}') break; e++; }
   return StringToDouble(StringSubstr(js, p, e - p));
  }

string JsonStr(string js, string key)
  {
   int p = StringFind(js, "\""+key+"\":\"");
   if(p < 0) return "";
   p += StringLen(key) + 4;
   int e = StringFind(js, "\"", p);
   return (e > p) ? StringSubstr(js, p, e - p) : "";
  }

string JsonEsc(string v)
  {
   StringReplace(v, "\\", "\\\\"); StringReplace(v, "\"", "\\\"");
   return v;
  }

// MT5 economic calendar (medium + high impact, -1 day .. +7 days) -> /bridge/calendar.
// Backs up the app's ForexFactory feed, which rate-limits cloud servers.
void PushCalendar()
  {
   long off = ServerToUtcOffset();
   datetime now = TimeTradeServer();
   MqlCalendarValue vals[];
   int n = CalendarValueHistory(vals, now - 86400, now + 7*86400);
   if(n <= 0) return;
   string js = "{\"events\":[";
   int added = 0;
   for(int i=0; i<n && added<400; i++)
     {
      MqlCalendarEvent ev;
      if(!CalendarEventById(vals[i].event_id, ev)) continue;
      if(ev.importance < CALENDAR_IMPORTANCE_MODERATE) continue;
      MqlCalendarCountry c;
      if(!CalendarCountryById(ev.country_id, c)) continue;
      string imp = (ev.importance == CALENDAR_IMPORTANCE_HIGH) ? "high" : "medium";
      if(added > 0) js += ",";
      js += "{\"t\":" + IntegerToString((long)vals[i].time - off) +
            ",\"ccy\":\"" + c.currency + "\",\"event\":\"" + JsonEsc(ev.name) +
            "\",\"impact\":\"" + imp + "\"" +
            ",\"actual\":"   + (vals[i].HasActualValue()   ? DoubleToString(vals[i].GetActualValue(),3)   : "null") +
            ",\"forecast\":" + (vals[i].HasForecastValue() ? DoubleToString(vals[i].GetForecastValue(),3) : "null") +
            ",\"previous\":" + (vals[i].HasPreviousValue() ? DoubleToString(vals[i].GetPreviousValue(),3) : "null") + "}";
      added++;
     }
   js += "]}";
   if(added > 0) PostJson("/bridge/calendar", js);
  }

void PushData()
  {
   if(g_token == "") { g_sendStatus = "OK - council gate (journal sync off)"; return; }
   bool leader = IsLeader();
   if(leader && TimeCurrent() - g_lastCalPush >= 6*3600)      // calendar every 6h (and at start)
     { g_lastCalPush = TimeCurrent(); PushCalendar(); }
   string sym = (InpAppSymbol!="") ? InpAppSymbol : _Symbol;
   long off = ServerToUtcOffset();
   for(int k=0;k<ArraySize(g_sendTF);k++)
     {
      datetime last = iTime(_Symbol, g_sendTF[k], 1);           // newest CLOSED bar
      if(last==0 || last==g_sentBar[k]) continue;
      int count = g_sentInit[k] ? 3 : InpInitialBars;
      MqlRates r[]; ArraySetAsSeries(r,false);
      int got = CopyRates(_Symbol, g_sendTF[k], 1, count, r);
      if(got<=0) continue;
      string js = "{\"symbol\":\""+sym+"\",\"timeframe\":\""+TfName(g_sendTF[k])+"\",\"bars\":[";
      for(int i=0;i<got;i++)
        {
         if(i>0) js += ",";
         js += "{\"t\":" + IntegerToString((long)r[i].time - off) +
               ",\"o\":" + DoubleToString(r[i].open,_Digits) + ",\"h\":" + DoubleToString(r[i].high,_Digits) +
               ",\"l\":" + DoubleToString(r[i].low,_Digits)  + ",\"c\":" + DoubleToString(r[i].close,_Digits) +
               ",\"v\":" + IntegerToString(r[i].tick_volume) + "}";
        }
      js += "]}";
      if(PostJson("/bridge/bars", js)) { g_sentBar[k]=last; g_sentInit[k]=true; }
      else break;                                               // retry next timer tick
     }
   if(leader && TimeCurrent() - g_lastAcctPush >= 60)
     {
      g_lastAcctPush = TimeCurrent();
      string js = StringFormat("{\"login\":%I64d,\"symbol\":\"%s\",\"preset\":\"%s\",\"phase\":%d,"
                               "\"balance\":%.2f,\"equity\":%.2f,\"initial\":%.2f,\"guard\":\"%s\","
                               "\"daily_floor\":%.2f,\"max_floor\":%.2f,\"target\":%.2f,"
                               "\"open_positions\":%d,\"lunar\":\"%s\",\"bb\":\"%s\","
                               "\"trading_days\":%d,\"profitable_days\":%d,\"min_days\":%d}",
                               AccountInfoInteger(ACCOUNT_LOGIN), sym, g_presetName, InpPhase,
                               AccountInfoDouble(ACCOUNT_BALANCE), AccountInfoDouble(ACCOUNT_EQUITY),
                               g_initialBalance, g_propStatus, g_dailyFloor, g_maxFloor, g_targetEq,
                               CountMagicPositions(), g_lunarPhaseName, g_bbStr,
                               g_tradeDays, g_profitDays, g_minDays);
      PostJson("/bridge/account", js);
     }
  }

//+------------------------------------------------------------------+
//| 9. CHART PLAN + HUD                                              |
//+------------------------------------------------------------------+
void PlanLine(string nm,double price,color clr,datetime t1,datetime t2,bool solid)
  {
   string ln=OBJ_PREFIX+"plan_"+nm;
   if(ObjectFind(0,ln)<0) ObjectCreate(0,ln,OBJ_TREND,0,t1,price,t2,price);
   ObjectSetInteger(0,ln,OBJPROP_TIME,0,t1); ObjectSetDouble(0,ln,OBJPROP_PRICE,0,price);
   ObjectSetInteger(0,ln,OBJPROP_TIME,1,t2); ObjectSetDouble(0,ln,OBJPROP_PRICE,1,price);
   ObjectSetInteger(0,ln,OBJPROP_COLOR,clr); ObjectSetInteger(0,ln,OBJPROP_STYLE,solid?STYLE_SOLID:STYLE_DASH);
   ObjectSetInteger(0,ln,OBJPROP_RAY_RIGHT,false); ObjectSetInteger(0,ln,OBJPROP_SELECTABLE,false);
  }
void DrawPlan()
  {
   if(!g_tr.active) return;
   datetime t1=g_tr.openTime, t2=TimeCurrent()+PeriodSeconds()*15;
   PlanLine("entry",g_tr.entry,clrDodgerBlue,t1,t2,true);
   PlanLine("sl",g_tr.sl,clrRed,t1,t2,true);
   PlanLine("tp1",g_tr.tp1,clrLimeGreen,t1,t2,false);
   PlanLine("tp2",g_tr.tp2,clrLimeGreen,t1,t2,false);
   PlanLine("tp3",g_tr.tp3,clrLimeGreen,t1,t2,false);
  }
void ClearPlan() { ObjectsDeleteAll(0, OBJ_PREFIX+"plan_"); }

string g_rowKeys[]   = {"title","prop","room","days","lunar","bb","feed","news","session","bias","state","phase","align",
                        "id50","pattern","location","hunt","quality","adr","tdi","trendday","signal","gate","trade"};
string g_rowLabels[] = {"WILDCHANCE PROP EA","PROP GUARD","DAILY / MAX ROOM","TRADING DAYS","LUNAR","BOLLINGER HTF","APP FEED","NEWS GUARD","SESSION",
                        "SESSION BIAS","MARKET / ADX","CYCLE PHASE","EMA ALIGN","ID50","PATTERN","LOCATION",
                        "STOP HUNT","QUALITY","ADR USED","TDI","D1 TREND","SIGNAL","EXECUTION GATE","POSITION"};

void CreateHUD()
  {
   int rowH=InpFontSize+9, total=ArraySize(g_rowKeys);
   string bg=OBJ_PREFIX+"bg";
   if(ObjectFind(0,bg)<0) ObjectCreate(0,bg,OBJ_RECTANGLE_LABEL,0,0,0);
   ObjectSetInteger(0,bg,OBJPROP_CORNER,InpCorner);
   ObjectSetInteger(0,bg,OBJPROP_XDISTANCE,InpXOffset);
   ObjectSetInteger(0,bg,OBJPROP_YDISTANCE,InpYOffset-6);
   ObjectSetInteger(0,bg,OBJPROP_XSIZE,390);
   ObjectSetInteger(0,bg,OBJPROP_YSIZE,rowH*total+10);
   ObjectSetInteger(0,bg,OBJPROP_BGCOLOR,C'10,10,18');
   ObjectSetInteger(0,bg,OBJPROP_BORDER_TYPE,BORDER_FLAT);
   ObjectSetInteger(0,bg,OBJPROP_COLOR,clrDimGray);
   ObjectSetInteger(0,bg,OBJPROP_SELECTABLE,false);
   for(int i=0;i<total;i++)
     {
      int y=InpYOffset+i*rowH;
      MakeLabel(OBJ_PREFIX+"key_"+g_rowKeys[i], InpXOffset+10,  y, g_rowLabels[i], clrSilver);
      MakeLabel(OBJ_PREFIX+"val_"+g_rowKeys[i], InpXOffset+150, y, "-", clrAqua);
     }
  }
void MakeLabel(string name,int x,int y,string text,color clr)
  {
   if(ObjectFind(0,name)<0) ObjectCreate(0,name,OBJ_LABEL,0,0,0);
   ObjectSetInteger(0,name,OBJPROP_CORNER,InpCorner);
   ObjectSetInteger(0,name,OBJPROP_XDISTANCE,x);
   ObjectSetInteger(0,name,OBJPROP_YDISTANCE,y);
   ObjectSetInteger(0,name,OBJPROP_FONTSIZE,InpFontSize);
   ObjectSetString(0,name,OBJPROP_FONT,"Consolas");
   ObjectSetString(0,name,OBJPROP_TEXT,text);
   ObjectSetInteger(0,name,OBJPROP_COLOR,clr);
   ObjectSetInteger(0,name,OBJPROP_SELECTABLE,false);
  }
void SetVal(string key,string text,color clr=clrAqua)
  {
   string v=OBJ_PREFIX+"val_"+key;
   if(ObjectFind(0,v)>=0) { ObjectSetString(0,v,OBJPROP_TEXT,text); ObjectSetInteger(0,v,OBJPROP_COLOR,clr); }
  }
void UpdateHUD()
  {
   double eq=AccountInfoDouble(ACCOUNT_EQUITY);
   SetVal("title",(MQLInfoInteger(MQL_DEBUG) ? "DEBUG RUN - attach from Navigator! " : "")+"v4.65 "+g_presetName+" P"+IntegerToString(InpPhase)+"  init "+DoubleToString(g_initialBalance,0),clrWhite);
   if(g_minDays > 0)
      SetVal("days", g_minDaysProfitable ?
             IntegerToString(g_profitDays)+"/"+IntegerToString(g_minDays)+" profitable (payout)" :
             IntegerToString(g_tradeDays)+"/"+IntegerToString(g_minDays)+" traded  ("+IntegerToString(g_profitDays)+" profitable)",
             (g_minDaysProfitable ? g_profitDays : g_tradeDays) >= g_minDays ? clrLime : clrYellow);
   else
      SetVal("days", IntegerToString(g_tradeDays)+" traded (no minimum)", clrSilver);
   SetVal("bb",g_bbStr,(MathAbs(g_bbZ1)>=2||MathAbs(g_bbZ2)>=2)?clrOrange:((MathAbs(g_bbZ1)>=1||MathAbs(g_bbZ2)>=1)?clrYellow:clrSilver));
   string setup = SetupState();
   if(setup != "")
      SetVal("feed", setup, StringFind(setup, "READY") == 0 ? clrLime : clrOrange);
   else
      SetVal("feed",g_sendStatus,StringFind(g_sendStatus,"OK")==0?clrLime:(g_sendStatus=="OFF"?clrSilver:clrOrange));
   SetVal("prop",g_propStatus,g_propStatus=="ACTIVE"?clrLime:(g_propStatus=="TARGET PASSED"?clrAqua:clrRed));
   SetVal("room",DoubleToString(eq-g_dailyFloor,0)+" / "+DoubleToString(eq-g_maxFloor,0),
          (eq-g_dailyFloor) < g_initialBalance*0.01 ? clrOrange : clrLime);
   SetVal("lunar",InpUseLunarEngine ? g_lunarPhaseName+" D"+DoubleToString(g_lunarAgeDays,1) : "OFF",
          g_inLunarObsWindow?clrOrange:clrSilver);
   SetVal("news",g_newsBlocked?("PAUSED: "+(g_newsWhat!=""?g_newsWhat:"high impact")):"CLEAR",g_newsBlocked?clrRed:clrLime);
   SetVal("session",g_sessionStr,clrWhite);
   SetVal("bias",g_sessionBias,StringFind(g_sessionBias,"BULLISH")>=0?clrLime:(StringFind(g_sessionBias,"BEARISH")>=0?clrRed:clrSilver));
   SetVal("state",g_marketState,g_isChop?clrOrange:clrLime);
   SetVal("phase",g_cyclePhase+"  "+g_levelsCount,g_cyclePhase=="Bullish"?clrLime:(g_cyclePhase=="Bearish"?clrRed:clrSilver));
   SetVal("align",g_emaAlign,clrSilver);
   SetVal("id50",g_id50Status,StringFind(g_id50Status,"Fired")>=0?clrLime:clrYellow);
   SetVal("pattern",g_pattern,clrWhite);
   SetVal("location",g_location,clrSilver);
   SetVal("hunt",g_stopHunt,g_stopHunt=="NONE YET"?clrSilver:clrOrange);
   SetVal("quality",IntegerToString(g_quality)+"/10",g_quality>=InpMinQualityScore?clrLime:clrSilver);
   SetVal("adr",DoubleToString(g_adrUsedPct,0)+"% of "+DoubleToString(g_adrPips,0)+"p - "+g_adrTag,g_adrTag=="EXHAUSTED"?clrRed:clrLime);
   SetVal("tdi",g_tdiState,clrSilver);
   SetVal("trendday",g_trendDay,clrSilver);
   SetVal("signal",g_signal,StringFind(g_signal,"BUY")>=0?clrLime:(StringFind(g_signal,"SELL")>=0?clrRed:clrYellow));
   SetVal("gate",g_newSignalTxt,StringFind(g_newSignalTxt,"BLOCKED")>=0?clrOrange:clrLime);
   if(g_tr.active && g_tr.risk>0)
     {
      double bid=SymbolInfoDouble(_Symbol,SYMBOL_BID);
      double r=(bid-g_tr.entry)*g_tr.dir/g_tr.risk;
      int hrs=(int)MathMax(0,(g_tr.maxHoldSeconds-(TimeCurrent()-g_tr.openTime))/3600);
      SetVal("trade",DoubleToString(r,2)+"R | "+IntegerToString(hrs)+"h left",r>=0?clrLime:clrRed);
     }
   else SetVal("trade","FLAT | last: "+g_lastResult,clrSilver);
  }
//+------------------------------------------------------------------+
