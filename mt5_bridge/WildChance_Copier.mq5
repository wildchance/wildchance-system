//+------------------------------------------------------------------+
//|                                        WildChance_Copier.mq5     |
//|  One MASTER account -> follower accounts at any MT5 broker        |
//|  (JustMarkets, FBS, HFM, Exness, Eightcap, IC Markets, Deriv,     |
//|   GatesFX, MidasFX ...) through the WildChance app.                |
//|                                                                    |
//|  MASTER   : attach on the main account (next to WildChance v4.54). |
//|             Sends every open position to the app every ~2 s.       |
//|  FOLLOWER : attach on each broker account (any one chart).         |
//|             Reads the master book, opens / syncs SL-TP / partially |
//|             closes / closes its copies. Lots = master lots x       |
//|             (this equity / master equity) x multiplier, corrected  |
//|             for contract size, rounded down to the broker step.    |
//|                                                                    |
//|  Safety: master offline -> HOLD (nothing opened, nothing closed);  |
//|  per-account daily-loss stop; slippage limit on new copies;        |
//|  pause / multiplier per account from the bot (/copypause, /copymult)|
//|  or the /copy page. Cent accounts (USC) are handled.               |
//|                                                                    |
//|  Tools > Options > Expert Advisors: allow WebRequest for the app   |
//|  URL. NOT compiled by the author: compile in MetaEditor (F7) and   |
//|  run on DEMO accounts first.                                       |
//+------------------------------------------------------------------+
#property copyright "WildChance"
#property version   "1.00"

#include <Trade\Trade.mqh>

enum ENUM_COPY_ROLE { ROLE_MASTER = 0, ROLE_FOLLOWER = 1 };

input group "=== ROLE ==="
input ENUM_COPY_ROLE InpRole        = ROLE_FOLLOWER; // MASTER on the main account, FOLLOWER everywhere else
input string   InpAccountName       = "";     // Label in the bot / page, e.g. "Exness 1"
input string   InpAppUrl            = "https://wildchance-system.onrender.com";
input string   InpBridgeToken       = "";     // = BRIDGE_TOKEN in the app (header only, never in URLs)
input int      InpIntervalMs        = 2000;   // Sync every N ms (min 1000)

input group "=== MASTER ==="
input long     InpMasterMagic       = -1;     // -1 = copy every position (EA + manual); else only this magic

input group "=== FOLLOWER ==="
input long     InpCopyMagic         = 515151; // Magic for the copies (unique per follower terminal)
input double   InpMaxSlipPips       = 10;     // Skip a NEW copy if price moved more than this against the master entry (0 = off)
input double   InpMaxDailyLossPct   = 5.0;    // Close copies + stop for the day at this equity loss (0 = off)
input double   InpMaxLotsPerTrade   = 5.0;    // Hard cap per copied position
input bool     InpUseMinLot         = false;  // Scaled lot below broker minimum: true = use min lot, false = skip
input bool     InpCopySLTP          = true;   // Copy and keep SL / TP in sync
input string   InpSymbolSuffix      = "";     // e.g. "m" (Exness), ".a", "+" - blank = auto-detect
input string   InpSymbolMap         = "";     // Manual map, e.g. "XAUUSD=GOLD,NAS100=USTEC,US30=DJ30"

CTrade trade;
string g_status = "starting";
string g_errors[];
int    g_cycle = 0;
long   g_lagMs = -1;
int    g_missing = 0;
string g_guard = "ACTIVE";
int    g_guardDay = 0;

//+------------------------------------------------------------------+
int OnInit()
  {
   if(InpBridgeToken == "") { Alert("WildChance Copier: set InpBridgeToken"); return INIT_PARAMETERS_INCORRECT; }
   trade.SetExpertMagicNumber((ulong)InpCopyMagic);
   trade.SetDeviationInPoints(30);
   EventSetMillisecondTimer(MathMax(1000, InpIntervalMs));
   g_status = (InpRole == ROLE_MASTER) ? "MASTER" : "FOLLOWER";
   Comment("WildChance Copier ", g_status, " - waiting for first sync");
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason) { EventKillTimer(); Comment(""); }

void OnTimer()
  {
   if(MQLInfoInteger(MQL_TESTER)) return;
   if(InpRole == ROLE_MASTER) MasterPush(); else FollowerSync();
   g_cycle++;
  }

//+------------------------------------------------------------------+
//| HTTP                                                               |
//+------------------------------------------------------------------+
string BaseUrl()
  {
   string u = InpAppUrl;
   if(StringSubstr(u, StringLen(u) - 1) == "/") u = StringSubstr(u, 0, StringLen(u) - 1);
   return u;
  }

int Http(string method, string path, string body, string &reply)
  {
   char data[], result[]; string resHeaders;
   if(body != "") StringToCharArray(body, data, 0, StringLen(body), CP_UTF8);
   string headers = "X-Bridge-Token: " + InpBridgeToken + "\r\n";
   if(body != "") headers = "Content-Type: application/json\r\n" + headers;
   ResetLastError();
   uint t0 = GetTickCount();
   int code = WebRequest(method, BaseUrl() + path, headers, 8000, data, result, resHeaders);
   g_lagMs = (long)(GetTickCount() - t0);
   reply = (code > 0) ? CharArrayToString(result, 0, WHOLE_ARRAY, CP_UTF8) : "";
   if(code == -1) g_status = "WebRequest ERR " + IntegerToString(GetLastError()) + " (allow the URL in Options)";
   return code;
  }

string Esc(string v) { StringReplace(v, "\\", "\\\\"); StringReplace(v, "\"", "\\\""); return v; }

void AddError(string e)
  {
   int n = ArraySize(g_errors);
   for(int i = 0; i < n; i++) if(g_errors[i] == e) return;   // once per message
   if(n >= 5) { for(int i = 1; i < n; i++) g_errors[i-1] = g_errors[i]; n--; ArrayResize(g_errors, n); }
   ArrayResize(g_errors, n + 1); g_errors[n] = e;
   Print("WildChance Copier: ", e);
  }

//+------------------------------------------------------------------+
//| tiny JSON readers (the app sends compact JSON)                     |
//+------------------------------------------------------------------+
string JStr(string js, string key)
  {
   int p = StringFind(js, "\"" + key + "\":\"");
   if(p < 0) return "";
   p += StringLen(key) + 4;
   int e = StringFind(js, "\"", p);
   return (e > p) ? StringSubstr(js, p, e - p) : "";
  }

double JNum(string js, string key)
  {
   int p = StringFind(js, "\"" + key + "\":");
   if(p < 0) return 0;
   p += StringLen(key) + 3;
   int e = p;
   while(e < StringLen(js)) { ushort c = StringGetCharacter(js, e); if(c == ',' || c == '}' || c == ']') break; e++; }
   string v = StringSubstr(js, p, e - p);
   if(v == "null") return 0;
   return StringToDouble(v);
  }

bool JBool(string js, string key) { return StringFind(js, "\"" + key + "\":true") >= 0; }

// split the "positions":[{...},{...}] array into objects (no nested objects inside)
int JObjects(string js, string key, string &objs[])
  {
   ArrayResize(objs, 0);
   int p = StringFind(js, "\"" + key + "\":[");
   if(p < 0) return 0;
   p += StringLen(key) + 4;
   int n = 0;
   while(p < StringLen(js))
     {
      ushort c = StringGetCharacter(js, p);
      if(c == ']') break;
      if(c == '{')
        {
         int e = StringFind(js, "}", p);
         if(e < 0) break;
         ArrayResize(objs, n + 1); objs[n++] = StringSubstr(js, p, e - p + 1);
         p = e + 1; continue;
        }
      p++;
     }
   return n;
  }

//+------------------------------------------------------------------+
//| MASTER                                                             |
//+------------------------------------------------------------------+
void MasterPush()
  {
   string arr = ""; int n = 0;
   for(int i = 0; i < PositionsTotal(); i++)
     {
      ulong t = PositionGetTicket(i);
      if(t == 0) continue;
      if(InpMasterMagic >= 0 && PositionGetInteger(POSITION_MAGIC) != InpMasterMagic) continue;
      string sym = PositionGetString(POSITION_SYMBOL);
      int dg = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
      if(n > 0) arr += ",";
      arr += StringFormat("{\"ticket\":%I64u,\"symbol\":\"%s\",\"side\":\"%s\",\"volume\":%.2f,\"open_price\":%s,"
                          "\"sl\":%s,\"tp\":%s,\"contract_size\":%.4f,\"open_time\":%I64d}",
                          t, Esc(sym), (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY ? "BUY" : "SELL"),
                          PositionGetDouble(POSITION_VOLUME),
                          DoubleToString(PositionGetDouble(POSITION_PRICE_OPEN), dg),
                          DoubleToString(PositionGetDouble(POSITION_SL), dg),
                          DoubleToString(PositionGetDouble(POSITION_TP), dg),
                          SymbolInfoDouble(sym, SYMBOL_TRADE_CONTRACT_SIZE),
                          (long)PositionGetInteger(POSITION_TIME));
      n++;
     }
   string body = StringFormat("{\"login\":\"%I64d\",\"broker\":\"%s\",\"name\":\"%s\",\"equity\":%.2f,\"ccy\":\"%s\",\"positions\":[%s]}",
                              AccountInfoInteger(ACCOUNT_LOGIN), Esc(AccountInfoString(ACCOUNT_COMPANY)), Esc(InpAccountName),
                              AccountInfoDouble(ACCOUNT_EQUITY), AccountInfoString(ACCOUNT_CURRENCY), arr);
   string reply;
   int code = Http("POST", "/bridge/copy/master", body, reply);
   if(code == 200) g_status = "MASTER OK " + IntegerToString(n) + " positions";
   else if(code > 0) g_status = "MASTER HTTP " + IntegerToString(code);
   Comment("WildChance Copier - ", g_status, " - ", g_lagMs, " ms");
  }

//+------------------------------------------------------------------+
//| FOLLOWER: symbol mapping                                           |
//+------------------------------------------------------------------+
string ManualMap(string canon)
  {
   if(InpSymbolMap == "") return "";
   string parts[];
   int n = StringSplit(InpSymbolMap, ',', parts);
   for(int i = 0; i < n; i++)
     {
      string kv[];
      if(StringSplit(parts[i], '=', kv) == 2)
        {
         string k = kv[0], v = kv[1];
         StringTrimLeft(k); StringTrimRight(k); StringTrimLeft(v); StringTrimRight(v);
         if(k == canon) return v;
        }
     }
   return "";
  }

bool Tradeable(string s)
  {
   if(!SymbolSelect(s, true)) return false;
   return SymbolInfoInteger(s, SYMBOL_TRADE_MODE) != SYMBOL_TRADE_MODE_DISABLED;
  }

string MapSymbol(string canon)
  {
   string m = ManualMap(canon);
   if(m != "") return Tradeable(m) ? m : "";
   string bases[];
   ArrayResize(bases, 1); bases[0] = canon;
   if(canon == "XAUUSD") { ArrayResize(bases, 3); bases[1] = "GOLD"; bases[2] = "XAUUSDm"; }
   if(canon == "XAGUSD") { ArrayResize(bases, 2); bases[1] = "SILVER"; }
   if(canon == "NAS100") { ArrayResize(bases, 5); bases[1] = "USTEC"; bases[2] = "US100"; bases[3] = "NDX100"; bases[4] = "NAS100.cash"; }
   if(canon == "US30")   { ArrayResize(bases, 4); bases[1] = "DJ30"; bases[2] = "WS30"; bases[3] = "US30.cash"; }
   if(canon == "US500")  { ArrayResize(bases, 4); bases[1] = "SPX500"; bases[2] = "SP500"; bases[3] = "US500.cash"; }
   if(canon == "USOIL")  { ArrayResize(bases, 4); bases[1] = "XTIUSD"; bases[2] = "WTI"; bases[3] = "USOUSD"; }
   if(canon == "BTCUSD") { ArrayResize(bases, 2); bases[1] = "BTCUSDT"; }
   for(int b = 0; b < ArraySize(bases); b++)
     {
      if(InpSymbolSuffix != "" && Tradeable(bases[b] + InpSymbolSuffix)) return bases[b] + InpSymbolSuffix;
      if(Tradeable(bases[b])) return bases[b];
     }
   // auto-detect suffixed names: EURUSDm, EURUSD.a, EURUSD+, EURUSD-ECN ...
   for(int b = 0; b < ArraySize(bases); b++)
      for(int i = 0; i < SymbolsTotal(false); i++)
        {
         string s = SymbolName(i, false);
         if(StringFind(s, bases[b]) == 0 && StringLen(s) <= StringLen(bases[b]) + 5 && Tradeable(s)) return s;
        }
   return "";
  }

double PipOf(string s)
  {
   int dg = (int)SymbolInfoInteger(s, SYMBOL_DIGITS);
   double pt = SymbolInfoDouble(s, SYMBOL_POINT);
   return (dg == 3 || dg == 5) ? pt * 10 : pt;
  }

double RoundLots(string s, double lots)
  {
   double step = SymbolInfoDouble(s, SYMBOL_VOLUME_STEP), mn = SymbolInfoDouble(s, SYMBOL_VOLUME_MIN),
          mx = SymbolInfoDouble(s, SYMBOL_VOLUME_MAX);
   if(step <= 0) step = 0.01;
   lots = MathMin(lots, MathMin(mx, InpMaxLotsPerTrade));
   double r = MathFloor(lots / step + 1e-9) * step;
   if(r < mn) r = InpUseMinLot ? mn : 0;
   return NormalizeDouble(r, 2);
  }

//+------------------------------------------------------------------+
//| FOLLOWER: copy bookkeeping (GlobalVariables survive restarts)      |
//+------------------------------------------------------------------+
string GvKey(string id) { return "WCC_" + IntegerToString(InpCopyMagic) + "_" + id; }
// master volume and our lots at the moment we copied: partial closes follow the MASTER's
// volume change only (never our own equity swings)
string GvMVol(string id) { return "WCCV_" + IntegerToString(InpCopyMagic) + "_" + id; }
string GvLots(string id) { return "WCCL_" + IntegerToString(InpCopyMagic) + "_" + id; }
// copied once and already gone here (our SL / TP / manual close): never re-open it
string GvDone(string id) { return "WCCD_" + IntegerToString(InpCopyMagic) + "_" + id; }

void Forget(string id)
  {
   GlobalVariableDel(GvKey(id)); GlobalVariableDel(GvMVol(id)); GlobalVariableDel(GvLots(id));
  }

int Today() { MqlDateTime d; TimeToStruct(TimeCurrent(), d); return d.year * 10000 + d.mon * 100 + d.day; }

ulong HeldTicket(string id)
  {
   string k = GvKey(id);
   if(GlobalVariableCheck(k))
     {
      ulong t = (ulong)GlobalVariableGet(k);
      if(PositionSelectByTicket(t)) return t;
      Forget(id);                                             // closed by SL / TP / manually
      GlobalVariableSet(GvDone(id), 1);
     }
   for(int i = 0; i < PositionsTotal(); i++)               // fallback: the comment tag
     {
      ulong t = PositionGetTicket(i);
      if(t == 0 || PositionGetInteger(POSITION_MAGIC) != InpCopyMagic) continue;
      if(PositionGetString(POSITION_COMMENT) == "WCC:" + id) { GlobalVariableSet(k, (double)t); return t; }
     }
   return 0;
  }

double DayStartEquity()
  {
   MqlDateTime d; TimeToStruct(TimeCurrent(), d);
   string k = StringFormat("WCC_DAY_%I64d_%04d%02d%02d", InpCopyMagic, d.year, d.mon, d.day);
   if(!GlobalVariableCheck(k)) GlobalVariableSet(k, AccountInfoDouble(ACCOUNT_EQUITY));
   return GlobalVariableGet(k);
  }

void CloseAllCopies(string why)
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong t = PositionGetTicket(i);
      if(t != 0 && PositionGetInteger(POSITION_MAGIC) == InpCopyMagic) trade.PositionClose(t);
     }
   AddError(why);
  }

//+------------------------------------------------------------------+
//| FOLLOWER: one sync cycle                                           |
//+------------------------------------------------------------------+
void FollowerSync()
  {
   double eq = AccountInfoDouble(ACCOUNT_EQUITY);
   string ccy = AccountInfoString(ACCOUNT_CURRENCY);
   double day0 = DayStartEquity();
   double dayPl = (day0 > 0) ? (eq / day0 - 1) * 100 : 0;
   if(g_guard != "ACTIVE" && Today() != g_guardDay) g_guard = "ACTIVE";          // new trading day
   if(InpMaxDailyLossPct > 0 && dayPl <= -InpMaxDailyLossPct && g_guard == "ACTIVE")
     {
      g_guard = "DAILY STOP"; g_guardDay = Today();
      CloseAllCopies("daily loss stop hit (" + DoubleToString(dayPl, 2) + "%) - copies closed until tomorrow");
     }

   string reply;
   string path = StringFormat("/bridge/copy?login=%I64d&equity=%.2f&ccy=%s", AccountInfoInteger(ACCOUNT_LOGIN), eq, ccy);
   int code = Http("GET", path, "", reply);
   if(code != 200) { if(code > 0) g_status = "HTTP " + IntegerToString(code); Report(dayPl); Show(); return; }

   bool masterOk = JBool(reply, "master_ok");
   string action = JStr(reply, "action");
   g_status = action + (masterOk ? "" : " (master offline)");
   if(!masterOk || action == "HOLD" || g_guard != "ACTIVE") { Report(dayPl); Show(); return; }

   string objs[];
   int n = JObjects(reply, "positions", objs);
   string wanted[]; ArrayResize(wanted, n);
   g_missing = 0;
   for(int i = 0; i < n; i++)
     {
      string o = objs[i];
      string id = JStr(o, "id");
      wanted[i] = id;
      string canon = JStr(o, "symbol");
      string sym = MapSymbol(canon);
      if(sym == "") { g_missing++; AddError("no symbol for " + canon + " - set InpSymbolMap"); continue; }
      bool buy = JStr(o, "side") == "BUY";
      double mcs = JNum(o, "contract_size"), lcs = SymbolInfoDouble(sym, SYMBOL_TRADE_CONTRACT_SIZE);
      double target = JNum(o, "target_volume") * ((mcs > 0 && lcs > 0) ? mcs / lcs : 1.0);
      double lots = RoundLots(sym, target);
      double sl = InpCopySLTP ? JNum(o, "sl") : 0, tp = InpCopySLTP ? JNum(o, "tp") : 0;
      int dg = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
      sl = NormalizeDouble(sl, dg); tp = NormalizeDouble(tp, dg);
      trade.SetTypeFillingBySymbol(sym);

      ulong held = HeldTicket(id);
      if(held == 0)
        {
         if(action != "COPY") continue;                       // paused: no new copies
         if(GlobalVariableCheck(GvDone(id))) continue;        // this trade already ran its course here
         if(lots <= 0) { g_missing++; AddError(canon + " " + id + ": scaled lot " + DoubleToString(target, 3) + " below broker minimum"); continue; }
         double px = buy ? SymbolInfoDouble(sym, SYMBOL_ASK) : SymbolInfoDouble(sym, SYMBOL_BID);
         double entry = JNum(o, "open_price");
         double slip = (buy ? px - entry : entry - px) / PipOf(sym);
         if(InpMaxSlipPips > 0 && entry > 0 && slip > InpMaxSlipPips)
           { g_missing++; AddError(canon + " " + id + ": price moved " + DoubleToString(slip, 1) + " pips past the master entry - not copied"); continue; }
         string cmt = "WCC:" + id;
         bool ok = buy ? trade.Buy(lots, sym, 0, sl, tp, cmt) : trade.Sell(lots, sym, 0, sl, tp, cmt);
         if(!ok || (trade.ResultRetcode() != TRADE_RETCODE_DONE && trade.ResultRetcode() != TRADE_RETCODE_PLACED))
           {
            if(trade.ResultRetcode() == TRADE_RETCODE_INVALID_STOPS)          // stops too close here: open naked, sync next cycle
               ok = buy ? trade.Buy(lots, sym, 0, 0, 0, cmt) : trade.Sell(lots, sym, 0, 0, 0, cmt);
            if(!ok) { g_missing++; AddError(canon + " open failed: " + trade.ResultRetcodeDescription()); continue; }
           }
         ulong t = trade.ResultOrder();
         for(int k = 0; k < PositionsTotal(); k++)            // the position ticket for the map
           {
            ulong pt = PositionGetTicket(k);
            if(pt != 0 && PositionGetInteger(POSITION_MAGIC) == InpCopyMagic && PositionGetString(POSITION_COMMENT) == cmt) { t = pt; break; }
           }
         GlobalVariableSet(GvKey(id), (double)t);
         GlobalVariableSet(GvMVol(id), JNum(o, "volume"));
         GlobalVariableSet(GvLots(id), lots);
         continue;
        }
      // held: keep SL / TP in sync, follow master partial closes
      if(!PositionSelectByTicket(held)) continue;
      double pt = SymbolInfoDouble(sym, SYMBOL_POINT);
      if(InpCopySLTP && (MathAbs(PositionGetDouble(POSITION_SL) - sl) > 2 * pt || MathAbs(PositionGetDouble(POSITION_TP) - tp) > 2 * pt))
         if(!trade.PositionModify(held, sl, tp)) AddError(canon + " SL/TP sync: " + trade.ResultRetcodeDescription());
      double vol = PositionGetDouble(POSITION_VOLUME), step = SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP);
      double mOpen = GlobalVariableCheck(GvMVol(id)) ? GlobalVariableGet(GvMVol(id)) : 0;
      double ourOpen = GlobalVariableCheck(GvLots(id)) ? GlobalVariableGet(GvLots(id)) : vol;
      double mNow = JNum(o, "volume");
      if(mOpen <= 0 || mNow <= 0 || mNow >= mOpen * 0.999) continue;          // master has not scaled out
      if(step <= 0) step = 0.01;
      double want = MathFloor(ourOpen * mNow / mOpen / step + 1e-9) * step;
      if(want < SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN)) continue;          // too small to split: closes with the master
      if(vol - want >= step - 1e-9)
         if(!trade.PositionClosePartial(held, NormalizeDouble(vol - want, 2)))
            AddError(canon + " partial close: " + trade.ResultRetcodeDescription());
     }

   // close copies the master no longer holds
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong t = PositionGetTicket(i);
      if(t == 0 || PositionGetInteger(POSITION_MAGIC) != InpCopyMagic) continue;
      string c = PositionGetString(POSITION_COMMENT);
      string id = (StringFind(c, "WCC:") == 0) ? StringSubstr(c, 4) : "";
      if(id == "")                                            // comment rewritten by the broker: use the map
        {
         for(int g = 0; g < GlobalVariablesTotal(); g++)
           {
            string k = GlobalVariableName(g);
            if(StringFind(k, GvKey("")) == 0 && (ulong)GlobalVariableGet(k) == t) { id = StringSubstr(k, StringLen(GvKey(""))); break; }
           }
        }
      bool keep = false;
      for(int w = 0; w < n; w++) if(wanted[w] == id) { keep = true; break; }
      if(!keep && id != "")
        {
         if(trade.PositionClose(t)) Forget(id);
         else AddError("close failed: " + trade.ResultRetcodeDescription());
        }
     }
   // forget finished trades once the master has closed them too
   string pre = GvDone("");
   for(int g = GlobalVariablesTotal() - 1; g >= 0; g--)
     {
      string k = GlobalVariableName(g);
      if(StringFind(k, pre) != 0) continue;
      string id = StringSubstr(k, StringLen(pre));
      bool live = false;
      for(int w = 0; w < n; w++) if(wanted[w] == id) { live = true; break; }
      if(!live) GlobalVariableDel(k);
     }
   Report(dayPl);
   Show();
  }

void Report(double dayPl)
  {
   if(g_cycle % 3 != 0) return;                              // every ~6 s
   string arr = ""; int n = 0;
   for(int i = 0; i < PositionsTotal(); i++)
     {
      ulong t = PositionGetTicket(i);
      if(t == 0 || PositionGetInteger(POSITION_MAGIC) != InpCopyMagic) continue;
      string c = PositionGetString(POSITION_COMMENT);
      if(n > 0) arr += ",";
      arr += StringFormat("{\"ticket\":%I64u,\"master_id\":\"%s\",\"symbol\":\"%s\",\"volume\":%.2f,\"profit\":%.2f}",
                          t, Esc(StringFind(c, "WCC:") == 0 ? StringSubstr(c, 4) : ""), Esc(PositionGetString(POSITION_SYMBOL)),
                          PositionGetDouble(POSITION_VOLUME), PositionGetDouble(POSITION_PROFIT));
      n++;
     }
   string errs = "";
   for(int i = 0; i < ArraySize(g_errors); i++) errs += (i ? "," : "") + "\"" + Esc(g_errors[i]) + "\"";
   string body = StringFormat("{\"login\":\"%I64d\",\"name\":\"%s\",\"broker\":\"%s\",\"equity\":%.2f,\"ccy\":\"%s\","
                              "\"daily_pl_pct\":%.2f,\"guard\":\"%s\",\"missing\":%d,\"lag_ms\":%I64d,"
                              "\"status\":\"%s\",\"positions\":[%s],\"errors\":[%s]}",
                              AccountInfoInteger(ACCOUNT_LOGIN), Esc(InpAccountName), Esc(AccountInfoString(ACCOUNT_COMPANY)),
                              AccountInfoDouble(ACCOUNT_EQUITY), AccountInfoString(ACCOUNT_CURRENCY), dayPl, g_guard,
                              g_missing, g_lagMs, Esc(g_status), arr, errs);
   string reply;
   Http("POST", "/bridge/copy/report", body, reply);
  }

void Show()
  {
   Comment("WildChance Copier FOLLOWER - ", g_status, "\n",
           "guard: ", g_guard, " | not copied: ", g_missing, " | lag: ", g_lagMs, " ms\n",
           (ArraySize(g_errors) ? "last: " + g_errors[ArraySize(g_errors) - 1] : ""));
  }
//+------------------------------------------------------------------+
