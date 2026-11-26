"""Watch-only treasury: the owner's Tangem (or any) PUBLIC addresses → balances in USD / KES
and an alert whenever funds arrive or leave.

No private key, seed phrase or signing ever touches this server — only public
addresses and public explorers, so a leaked bot token or server cannot move funds.

Chains (free public APIs, no keys):
  btc  mempool.space                         BTC
  eth  publicnode JSON-RPC                   ETH, USDT, USDC (ERC-20)
  tron TronGrid                              TRX, USDT (TRC-20)
  ton  tonapi.io                             TON, USDT (jetton)
"""

from __future__ import annotations

import re
import time
from typing import Dict, List, Optional, Tuple

from decouple import config

KEY = "treasury"
ETH_RPC = str(config("TREASURY_ETH_RPC", default="https://ethereum-rpc.publicnode.com"))
ERC20 = {"USDT": ("0xdAC17F958D2ee523a2206206994597C13D831ec7", 6),
         "USDC": ("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", 6)}
TRC20_USDT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
STABLE = {"USDT", "USDC"}
MIN_MOVE_USD = float(config("TREASURY_MIN_ALERT_USD", default="1"))

_PATTERNS = {
    "btc": re.compile(r"^(bc1[02-9ac-hj-np-z]{11,71}|[13][1-9A-HJ-NP-Za-km-z]{25,34})$"),
    "eth": re.compile(r"^0x[0-9a-fA-F]{40}$"),
    "tron": re.compile(r"^T[1-9A-HJ-NP-Za-km-z]{33}$"),
    "ton": re.compile(r"^((EQ|UQ|kQ|0Q)[A-Za-z0-9_-]{46}|-?\d:[0-9a-fA-F]{64})$"),
}
CHAIN_ALIASES = {"bitcoin": "btc", "btc": "btc", "eth": "eth", "ethereum": "eth", "erc20": "eth",
                 "tron": "tron", "trx": "tron", "trc20": "tron", "ton": "ton"}


# ── safety: never accept secrets ─────────────────────────────────────────────────
def looks_secret(text: str) -> bool:
    """A seed phrase (12/15/18/21/24 plain words), a raw private key, or an extended key."""
    t = (text or "").strip()
    words = t.split()
    if len(words) in (12, 15, 18, 21, 24) and all(re.fullmatch(r"[a-z]{3,8}", w) for w in words):
        return True
    if re.fullmatch(r"(0x)?[0-9a-fA-F]{64}", t):
        return True
    return t[:4] in ("xprv", "yprv", "zprv", "tprv")


def chain_of(chain: str, address: str) -> Optional[str]:
    c = CHAIN_ALIASES.get((chain or "").lower())
    return c if c and _PATTERNS[c].match(address or "") else None


def short(addr: str) -> str:
    return f"{addr[:6]}…{addr[-4:]}" if len(addr) > 12 else addr


# ── storage ──────────────────────────────────────────────────────────────────────
async def load() -> Dict:
    from services import data_store
    v, _ = await data_store.get(KEY)
    v = dict(v or {})
    v.setdefault("addresses", [])
    v.setdefault("last", {})
    return v


async def save(v: Dict) -> None:
    from services import data_store
    await data_store.put(KEY, v)


async def add(chain: str, address: str, label: str = "") -> Dict:
    c = chain_of(chain, address)
    if not c:
        return {"ok": False, "reason": f"not a valid {chain} public address"}
    v = await load()
    if any(a["address"] == address and a["chain"] == c for a in v["addresses"]):
        return {"ok": False, "reason": "already watched"}
    v["addresses"].append({"chain": c, "address": address, "label": label[:30] or f"{c.upper()} wallet"})
    await save(v)
    return {"ok": True, "n": len(v["addresses"])}


async def remove(n: int) -> Dict:
    v = await load()
    if not 1 <= n <= len(v["addresses"]):
        return {"ok": False, "reason": "no such entry — see /wallet"}
    a = v["addresses"].pop(n - 1)
    v["last"].pop(f"{a['chain']}:{a['address']}", None)
    await save(v)
    return {"ok": True, "removed": a}


# ── parsers (pure) ───────────────────────────────────────────────────────────────
def parse_mempool(d: Dict) -> Dict[str, float]:
    cs, ms = d.get("chain_stats") or {}, d.get("mempool_stats") or {}
    sats = (cs.get("funded_txo_sum", 0) - cs.get("spent_txo_sum", 0)
            + ms.get("funded_txo_sum", 0) - ms.get("spent_txo_sum", 0))
    return {"BTC": sats / 1e8}


def parse_tron(d: Dict) -> Dict[str, float]:
    rows = d.get("data") or []
    if not rows:
        return {"TRX": 0.0, "USDT": 0.0}
    acc = rows[0]
    usdt = 0.0
    for t in acc.get("trc20") or []:
        if TRC20_USDT in t:
            usdt = int(t[TRC20_USDT]) / 1e6
    return {"TRX": int(acc.get("balance") or 0) / 1e6, "USDT": usdt}


def parse_ton(acc: Dict, jettons: Dict) -> Dict[str, float]:
    out = {"TON": int(acc.get("balance") or 0) / 1e9}
    for b in (jettons or {}).get("balances") or []:
        j = b.get("jetton") or {}
        sym = str(j.get("symbol") or "").replace("₮", "T").upper()
        if sym in ("USDT", "USD₮"):
            out["USDT"] = int(b.get("balance") or 0) / 10 ** int(j.get("decimals") or 6)
    out.setdefault("USDT", 0.0)
    return out


def _erc20_call(owner: str, token: str) -> Dict:
    data = "0x70a08231" + owner.lower().replace("0x", "").rjust(64, "0")      # balanceOf(owner)
    return {"to": token, "data": data}


# ── fetch ────────────────────────────────────────────────────────────────────────
async def _rpc(method: str, params: list):
    from services.data_feeds import _get
    d = await _get("eth_rpc", ETH_RPC, method="POST",
                   body={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    return (d or {}).get("result")


async def balances(chain: str, address: str) -> Optional[Dict[str, float]]:
    from services.data_feeds import _get
    if chain == "btc":
        d = await _get("mempool", f"https://mempool.space/api/address/{address}")
        return parse_mempool(d) if d else None
    if chain == "tron":
        d = await _get("trongrid", f"https://api.trongrid.io/v1/accounts/{address}")
        return parse_tron(d) if d is not None else None
    if chain == "ton":
        acc = await _get("tonapi", f"https://tonapi.io/v2/accounts/{address}")
        if not acc:
            return None
        jet = await _get("tonapi", f"https://tonapi.io/v2/accounts/{address}/jettons")
        return parse_ton(acc, jet or {})
    if chain == "eth":
        wei = await _rpc("eth_getBalance", [address, "latest"])
        if wei is None:
            return None
        out = {"ETH": int(wei, 16) / 1e18}
        for sym, (tok, dec) in ERC20.items():
            r = await _rpc("eth_call", [_erc20_call(address, tok), "latest"])
            out[sym] = int(r, 16) / 10 ** dec if r and r != "0x" else 0.0
        return out
    return None


async def prices() -> Dict[str, float]:
    from services.data_feeds import _get
    out = {s: 1.0 for s in STABLE}
    d = await _get("binance", "https://data-api.binance.vision/api/v3/ticker/price",
                   {"symbols": '["BTCUSDT","ETHUSDT","TONUSDT","TRXUSDT"]'})
    for r in d or []:
        try:
            out[r["symbol"].replace("USDT", "")] = float(r["price"])
        except (KeyError, TypeError, ValueError):
            continue
    return out


def kes_per_usd() -> float:
    from services.hardware_scout import rates
    return rates()["kes_per_usd"]


# ── report + movement alerts ─────────────────────────────────────────────────────
def moves(prev: Dict[str, float], cur: Dict[str, float], px: Dict[str, float]) -> List[Tuple[str, float, float]]:
    """(asset, change, change_usd) for every asset that moved by ≥ MIN_MOVE_USD."""
    out = []
    for asset in set(prev) | set(cur):
        d = cur.get(asset, 0.0) - prev.get(asset, 0.0)
        usd = d * px.get(asset, 0.0)
        if abs(usd) >= MIN_MOVE_USD:
            out.append((asset, d, usd))
    return sorted(out)


async def snapshot(alert: bool = False) -> Dict:
    v = await load()
    px = await prices()
    rows, total, alerts = [], 0.0, []
    for i, a in enumerate(v["addresses"], 1):
        key = f"{a['chain']}:{a['address']}"
        bal = await balances(a["chain"], a["address"])
        if bal is None:
            rows.append({**a, "n": i, "ok": False})
            continue
        usd = sum(q * px.get(s, 0.0) for s, q in bal.items())
        total += usd
        rows.append({**a, "n": i, "ok": True, "balances": bal, "usd": round(usd, 2)})
        prev = v["last"].get(key)
        if prev is not None:
            for asset, d, dusd in moves(prev, bal, px):
                alerts.append(f"{'💰' if d > 0 else '📤'} {a['label']} ({a['chain'].upper()} {short(a['address'])}): "
                              f"{d:+,.6g} {asset} (≈ ${dusd:+,.2f})")
        v["last"][key] = bal
    v["checked"] = time.time()
    await save(v)
    sent = False
    if alert and alerts:
        from services.owner_notify import send
        sent = await send("🏦 Treasury movement\n" + "\n".join(alerts) +
                          "\n(watch-only — if you did not make an outgoing move, check your Tangem app now)")
    kes = kes_per_usd()
    return {"rows": rows, "total_usd": round(total, 2), "total_kes": round(total * kes), "kes_per_usd": kes,
            "prices": px, "alerts": alerts, "alert_sent": sent}


def text(rep: Dict) -> str:
    if not rep["rows"]:
        return ("🏦 Treasury (watch-only): no addresses yet.\n"
                "Add a PUBLIC address from your Tangem app:\n"
                "/walletadd tron T… Tangem USDT\n/walletadd btc bc1… Tangem BTC\n"
                "/walletadd eth 0x… · /walletadd ton UQ…\n"
                "Never send a seed phrase or private key — the bot never needs them.")
    lines = [f"🏦 Treasury (watch-only) — ${rep['total_usd']:,.2f} ≈ KES {rep['total_kes']:,}"]
    for r in rep["rows"]:
        head = f"{r['n']}. {r['label']} · {r['chain'].upper()} {short(r['address'])}"
        if not r["ok"]:
            lines.append(head + " — explorer not reachable, retry later")
            continue
        assets = " · ".join(f"{q:,.6g} {s}" for s, q in r["balances"].items() if q)
        lines.append(f"{head} — ${r['usd']:,.2f}" + (f"\n   {assets}" if assets else "\n   empty"))
    lines.append("Remove: /walletdel <number>")
    return "\n".join(lines)
