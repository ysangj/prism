"""PRD §15.5 checkpoints 2 & 4 — LIVE market data (network-dependent).

DEFERRED in this sandbox (outbound network blocked). Each test attempts the real
fetch and SKIPs (not fails) when the network is unavailable, so the build is not
blocked. Re-run in a networked environment (and set ``FRED_API_KEY`` for the
live Treasury curve) to exercise these for real.
"""
import os

import pytest

from prism import market_data
from prism.market_data import MarketDataError


def test_live_get_spot():
    market_data.clear_cache()
    try:
        spot = market_data.get_spot("AAPL")
    except MarketDataError as exc:
        pytest.skip(f"DEFERRED: live spot unavailable (network blocked): {exc}")
    assert spot > 0


def test_live_options_chain_non_empty():
    market_data.clear_cache()
    try:
        chain = market_data.get_options_chain("AAPL")
    except MarketDataError as exc:
        pytest.skip(f"DEFERRED: live options chain unavailable (network blocked): {exc}")
    assert len(chain) > 0


def test_live_treasury_curve_from_fred():
    if not os.environ.get("FRED_API_KEY"):
        pytest.skip("DEFERRED: FRED_API_KEY not set; live Treasury curve not exercised")
    market_data.get_treasury_curve.cache_clear()
    try:
        curve = market_data.get_treasury_curve()
    except MarketDataError as exc:
        pytest.skip(f"DEFERRED: live FRED fetch failed (network blocked): {exc}")
    assert isinstance(curve, dict) and len(curve) > 0
