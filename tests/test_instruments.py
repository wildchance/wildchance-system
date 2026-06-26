"""Catalog integrity tests for the expanded instrument universe."""

import pytest

from instruments.catalog import (
    INSTRUMENTS, get, by_class, all_symbols, catalog,
)

VALID_CLASSES = {"fx_major", "fx_minor", "fx_exotic", "metal", "crypto", "index"}


def test_catalog_nonempty_and_unique():
    syms = all_symbols()
    assert len(syms) == len(set(syms))            # no duplicate symbols
    assert len(syms) >= 20                         # expanded universe


def test_every_instrument_is_wellformed():
    for ins in INSTRUMENTS.values():
        assert ins.asset_class in VALID_CLASSES
        assert ins.pip > 0
        assert ins.dxy_sign in (-1, 0, 1)
        assert ins.td_symbol


def test_lookup_is_tolerant():
    assert get("USD/JPY").symbol == "USD/JPY"
    assert get("usdjpy").symbol == "USD/JPY"
    assert get("USDJPY").symbol == "USD/JPY"
    assert get("nonsense") is None


def test_usd_base_pairs_move_with_dxy():
    # USD as base (USD/XXX) -> +1 ; USD as quote (XXX/USD) -> -1
    assert get("USD/JPY").dxy_sign == +1
    assert get("USD/CAD").dxy_sign == +1
    assert get("EUR/USD").dxy_sign == -1
    assert get("GBP/USD").dxy_sign == -1


def test_crosses_have_no_usd_relationship():
    for s in ("EUR/JPY", "GBP/JPY", "EUR/GBP"):
        assert get(s).dxy_sign == 0
        assert get(s).cot_market is None


def test_jpy_pip_is_0_01():
    assert get("USD/JPY").pip == 0.01
    assert get("EUR/JPY").pip == 0.01
    assert get("EUR/USD").pip == 0.0001


def test_classes_present():
    for cls in ("metal", "crypto", "index", "fx_exotic"):
        assert by_class(cls), f"no instruments in class {cls}"


def test_dax_has_no_cftc_cot():
    assert get("DAX40").cot_market is None       # Eurex, not CFTC
    assert get("US500").cot_market is not None
