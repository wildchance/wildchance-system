"""Instrument catalog — the tradable universe with per-asset metadata.

One source of truth for every symbol the system can analyse. Each entry carries
what the engines need:

  td_symbol   : the TwelveData symbol to request prices/candles with
  asset_class : fx_major | fx_minor | fx_exotic | metal | crypto | index
  pip         : one pip in price units (for stop/target maths)
  cot_market  : CFTC "market_and_exchange_names" for COT (None if no COT exists)
  dxy_sign    : +1 if the instrument tends to move WITH the US Dollar Index,
                -1 if INVERSE, 0 if no clean USD relationship (FX crosses).
                Used by the Phase-4 DXY correlation/divergence engine.

Notes:
  • FX crosses (no USD leg) have cot_market=None and dxy_sign=0.
  • DAX is a Eurex product, so no CFTC COT (cot_market=None).
  • Index/crypto price coverage depends on your TwelveData plan; the analysis
    endpoint degrades gracefully (502) if a symbol isn't available.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict, List, Optional

PIP_FX = 0.0001
PIP_JPY = 0.01


@dataclass(frozen=True)
class Instrument:
    symbol: str
    td_symbol: str
    asset_class: str
    pip: float
    cot_market: Optional[str]
    dxy_sign: int

    def to_dict(self) -> dict:
        return asdict(self)


def _fx(sym, td, pip, cot, dxy, cls="fx_major"):
    return Instrument(sym, td, cls, pip, cot, dxy)


_CME = "CHICAGO MERCANTILE EXCHANGE"
_CMX = "COMMODITY EXCHANGE INC."

INSTRUMENTS: Dict[str, Instrument] = {i.symbol: i for i in [
    # --- FX majors (USD leg) ---
    _fx("EUR/USD", "EUR/USD", PIP_FX, f"EURO FX - {_CME}", -1),
    _fx("GBP/USD", "GBP/USD", PIP_FX, f"BRITISH POUND - {_CME}", -1),
    _fx("AUD/USD", "AUD/USD", PIP_FX, f"AUSTRALIAN DOLLAR - {_CME}", -1),
    _fx("NZD/USD", "NZD/USD", PIP_FX, f"NEW ZEALAND DOLLAR - {_CME}", -1),
    _fx("USD/JPY", "USD/JPY", PIP_JPY, f"JAPANESE YEN - {_CME}", +1),
    _fx("USD/CAD", "USD/CAD", PIP_FX, f"CANADIAN DOLLAR - {_CME}", +1),
    _fx("USD/CHF", "USD/CHF", PIP_FX, f"SWISS FRANC - {_CME}", +1),

    # --- FX minors / crosses (no USD leg) ---
    _fx("EUR/JPY", "EUR/JPY", PIP_JPY, None, 0, "fx_minor"),
    _fx("GBP/JPY", "GBP/JPY", PIP_JPY, None, 0, "fx_minor"),
    _fx("AUD/JPY", "AUD/JPY", PIP_JPY, None, 0, "fx_minor"),
    _fx("EUR/GBP", "EUR/GBP", PIP_FX, None, 0, "fx_minor"),
    _fx("AUD/NZD", "AUD/NZD", PIP_FX, None, 0, "fx_minor"),
    _fx("EUR/AUD", "EUR/AUD", PIP_FX, None, 0, "fx_minor"),

    # --- FX exotics ---
    _fx("USD/MXN", "USD/MXN", PIP_FX, f"MEXICAN PESO - {_CME}", +1, "fx_exotic"),
    _fx("USD/ZAR", "USD/ZAR", PIP_FX, f"SOUTH AFRICAN RAND - {_CME}", +1, "fx_exotic"),
    _fx("USD/SGD", "USD/SGD", PIP_FX, None, +1, "fx_exotic"),
    _fx("USD/TRY", "USD/TRY", PIP_FX, None, +1, "fx_exotic"),

    # --- Metals ---
    Instrument("XAU/USD", "XAU/USD", "metal", 0.01, f"GOLD - {_CMX}", -1),
    Instrument("XAG/USD", "XAG/USD", "metal", 0.001, f"SILVER - {_CMX}", -1),

    # --- Crypto ---
    Instrument("BTC/USD", "BTC/USD", "crypto", 1.0, f"BITCOIN - {_CME}", -1),
    Instrument("ETH/USD", "ETH/USD", "crypto", 0.1, f"ETHER CASH SETTLED - {_CME}", -1),

    # --- Indices (CFTC COT for US index futures; DAX is Eurex -> None) ---
    Instrument("NAS100", "NDX", "index", 1.0, f"NASDAQ-100 STOCK INDEX (MINI) - {_CME}", -1),
    Instrument("US30", "DJI", "index", 1.0, "DOW JONES INDUSTRIAL AVG- x $5 - CHICAGO BOARD OF TRADE", -1),
    Instrument("US500", "SPX", "index", 1.0, f"E-MINI S&P 500 - {_CME}", -1),
    Instrument("DAX40", "DAX", "index", 1.0, None, -1),
]}


def get(symbol: str) -> Optional[Instrument]:
    """Look up by canonical symbol, case/separator tolerant (USD/JPY, usdjpy …)."""
    if symbol in INSTRUMENTS:
        return INSTRUMENTS[symbol]
    key = symbol.upper().replace("\\", "/")
    if key in INSTRUMENTS:
        return INSTRUMENTS[key]
    flat = key.replace("/", "")
    for ins in INSTRUMENTS.values():
        if ins.symbol.replace("/", "") == flat:
            return ins
    return None


def all_symbols() -> List[str]:
    return list(INSTRUMENTS.keys())


def by_class(asset_class: str) -> List[Instrument]:
    return [i for i in INSTRUMENTS.values() if i.asset_class == asset_class]


def catalog() -> List[dict]:
    return [i.to_dict() for i in INSTRUMENTS.values()]
