"""Shared fixtures for the Prism Phase-1 acceptance suite.

All fixtures are deterministic and network-free: market inputs are supplied to
``price_product`` via explicit overrides (``spot``, ``risk_free``, ``div_yield``,
``credit_spread``, ``flat_vol``) so no live endpoints are touched, and a fixed
seed makes the Monte Carlo output reproducible.

Run with:  python -m pytest tests/ -q
(Requires pytest in the active interpreter; see TEST_RESULTS.md — it was not
installed in the developer's .venv despite being listed in requirements.txt.)
"""
from __future__ import annotations

import datetime as dt
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

SEED = 12345


def _maturity(years: float) -> dt.date:
    return dt.date.today() + dt.timedelta(days=int(round(years * 365.25)))


@pytest.fixture
def seed():
    return SEED


@pytest.fixture
def offline_market():
    """Deterministic market overrides matching PRD §15.5 numeric assumptions
    (spot=100, vol=20%, r=5%) plus the engine's 'A'-rated credit spread."""
    return dict(
        spot=100.0,
        risk_free=0.05,
        div_yield=0.0,
        credit_spread=0.0090,  # RATING_SPREADS["A"]
        flat_vol=0.20,
    )


@pytest.fixture
def canonical_autocallable():
    """Canonical autocallable (B) from amended PRD §15.7 — the 18-month note used
    for STRUCTURAL / end-to-end checks only (NOT the value bands).

    AAPL, 100k, 18m, A-rated, coupon 9.5%, coupon barrier 70%, call 100%,
    knock-in 60%, par."""
    from prism.models import Autocallable

    return Autocallable(
        underlier="AAPL",
        notional=100_000,
        maturity=_maturity(1.5),
        issuer="JPMorgan Chase",
        issuer_rating="A",
        offer_price=1.0,
        coupon_rate=0.095,
        coupon_barrier=0.70,
        call_barrier=1.00,
        knock_in_barrier=0.60,
        observation_freq="quarterly",
    )


@pytest.fixture
def canonical_autocallable_5y():
    """Canonical autocallable (A) from amended PRD §15.7 — the 5-year note whose
    VALUE BANDS must hold (bond_floor ~60-70%, option_value ~30-35%,
    fair_value < offer, margin_pct in 2-8%).

    A ~60-70% bond floor and a positive 2-8% margin only arise at multi-year
    maturities (PRD §15.7): bond_floor = N·e^(-(r+s)T), so the band needs a
    multi-year tenor and a sub-investment-grade discount. AAPL, 100k, 5y,
    BB-rated, coupon 7.5%, barriers as in the canonical example, par."""
    from prism.models import Autocallable

    return Autocallable(
        underlier="AAPL",
        notional=100_000,
        maturity=_maturity(5.0),
        issuer="JPMorgan Chase",
        issuer_rating="BB",
        offer_price=1.0,
        coupon_rate=0.075,
        coupon_barrier=0.70,
        call_barrier=1.00,
        knock_in_barrier=0.60,
        observation_freq="quarterly",
    )


@pytest.fixture
def offline_market_5y():
    """Deterministic market overrides for the 5-year band note (A).

    Chosen so the engine's purely-financial output lands inside the amended
    §15.7(A) bands: r=4.5%, BB spread=4.5% -> (r+s)·5 ≈ 0.462 -> bond_floor ≈
    63.7% of notional; vol=30% leaves an option budget of ~32%."""
    return dict(
        spot=100.0,
        risk_free=0.045,
        div_yield=0.005,
        credit_spread=0.045,
        flat_vol=0.30,
    )


@pytest.fixture
def sample_reverse_convertible():
    from prism.models import ReverseConvertible

    return ReverseConvertible(
        underlier="AAPL",
        notional=100_000,
        maturity=_maturity(1.0),
        issuer="Citi",
        issuer_rating="BBB",
        offer_price=1.0,
        coupon_rate=0.09,
        barrier=0.70,
        barrier_type="european",
    )
