"""Core data models for Prism (PRD 15.2).

Products are described as plain dataclasses. The pricing engine consumes these
and returns a ``DecompositionResult``. Phase 1 supports two product types:
``Autocallable`` and ``ReverseConvertible``.

Conventions
-----------
* Barriers, coupon rate, offer price are fractions of the initial level /
  notional (e.g. 0.70 = 70%, 1.0 = par), matching the term-sheet language.
* ``maturity`` is an absolute calendar date. Use :func:`tenor_years` to convert
  it to a year-fraction relative to a valuation date (defaults to today).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum

__all__ = [
    "ProductType",
    "Autocallable",
    "ReverseConvertible",
    "PrincipalProtected",
    "BarrierNote",
    "BufferedNote",
    "DecompositionResult",
    "tenor_years",
    "DAYS_PER_YEAR",
]

# Actual/365 fixed day count. Kept as a module constant so every module that
# converts dates to year-fractions uses the same convention.
DAYS_PER_YEAR = 365.0


class ProductType(Enum):
    AUTOCALLABLE = "autocallable"
    REVERSE_CONVERTIBLE = "reverse_convertible"
    PRINCIPAL_PROTECTED = "principal_protected"
    BARRIER_NOTE = "barrier_note"
    BUFFERED_NOTE = "buffered_note"


def tenor_years(maturity: date, valuation_date: date | None = None) -> float:
    """Year-fraction from ``valuation_date`` (default today) to ``maturity``.

    Uses Actual/365. Raises if the maturity is not strictly in the future.
    """
    valuation_date = valuation_date or date.today()
    days = (maturity - valuation_date).days
    if days <= 0:
        raise ValueError(
            f"maturity {maturity} must be after valuation date {valuation_date}"
        )
    return days / DAYS_PER_YEAR


# Observation frequencies -> number of observations per year.
_FREQ_PER_YEAR = {
    "monthly": 12,
    "quarterly": 4,
    "semiannual": 2,
    "annual": 1,
}


def observations_per_year(frequency: str) -> int:
    """Map an observation-frequency label to observations per year."""
    try:
        return _FREQ_PER_YEAR[frequency]
    except KeyError as exc:
        raise ValueError(
            f"unknown observation_freq {frequency!r}; "
            f"expected one of {sorted(_FREQ_PER_YEAR)}"
        ) from exc


@dataclass
class Autocallable:
    """Phoenix autocallable note (PRD 4, 8.1).

    Pays a periodic coupon on each observation date where the underlier is at or
    above ``coupon_barrier``. Redeems early at par plus coupon if the underlier
    is at or above ``call_barrier`` on an observation date. At maturity, if never
    called, returns par if the underlier is above ``knock_in_barrier``; otherwise
    principal is reduced in line with the underlier's performance.
    """

    underlier: str
    notional: float
    maturity: date
    issuer: str
    issuer_rating: str
    offer_price: float  # fraction of notional, 1.0 = par
    coupon_rate: float  # annualized, e.g. 0.095
    coupon_barrier: float  # fraction of initial, e.g. 0.70
    call_barrier: float  # e.g. 1.00
    knock_in_barrier: float  # e.g. 0.60
    observation_freq: str  # "monthly" | "quarterly" | "semiannual" | "annual"

    product_type: ProductType = field(default=ProductType.AUTOCALLABLE, init=False)


@dataclass
class ReverseConvertible:
    """Reverse convertible note (PRD 4, 8.1).

    Pays a fixed coupon over the life of the note. At maturity, returns par
    unless the barrier is breached, in which case principal is converted to the
    underlier's performance (investor absorbs the downside).

    ``barrier_type`` is "european" (observed only at maturity) or "american"
    (observed continuously along the path).
    """

    underlier: str
    notional: float
    maturity: date
    issuer: str
    issuer_rating: str
    offer_price: float  # fraction of notional, 1.0 = par
    coupon_rate: float  # annualized
    barrier: float  # fraction of initial, e.g. 0.70
    barrier_type: str = "european"  # "european" | "american"

    product_type: ProductType = field(
        default=ProductType.REVERSE_CONVERTIBLE, init=False
    )


@dataclass
class PrincipalProtected:
    """Principal-protected note / PPN (PRD 4, 8.1).

    Returns the protected ``floor`` fraction of principal at maturity (typically
    100% = full protection) plus ``participation`` of the underlier's positive
    performance, subject to ``cap`` (the maximum upside the note pays). The
    investor never receives less than ``floor`` of notional at maturity, so the
    bond floor (the issuer's promise to repay protected principal) dominates the
    decomposition and the embedded option is the residual call spread.

    Path-independent: the payoff depends only on the terminal underlier level, so
    pricing reduces to a discounted terminal-payoff expectation.
    """

    underlier: str
    notional: float
    maturity: date
    issuer: str
    issuer_rating: str
    offer_price: float  # fraction of notional, 1.0 = par
    participation: float  # fraction of upside captured, e.g. 1.00 = 100%
    cap: float  # max upside as fraction, e.g. 0.30 = +30% cap; 0/None = uncapped
    floor: float = 1.0  # protected principal fraction, 1.0 = full protection

    product_type: ProductType = field(
        default=ProductType.PRINCIPAL_PROTECTED, init=False
    )


@dataclass
class BarrierNote:
    """Digital barrier note (PRD 4, 8.1).

    Pays principal plus a single fixed digital return (``fixed_return``) at
    maturity if the underlier stays at/above ``barrier``; otherwise the investor
    is exposed to the underlier's downside (principal tracks performance, par at
    or above the barrier reference). ``barrier_type`` is "european" (observed only
    at the terminal level) or "american" (observed along the path on the
    observation grid -- a breach any time disqualifies the digital payout).
    """

    underlier: str
    notional: float
    maturity: date
    issuer: str
    issuer_rating: str
    offer_price: float  # fraction of notional, 1.0 = par
    fixed_return: float  # digital payout as a fraction of notional, e.g. 0.20
    barrier: float  # fraction of initial, e.g. 0.80
    barrier_type: str = "european"  # "european" | "american"

    product_type: ProductType = field(default=ProductType.BARRIER_NOTE, init=False)


@dataclass
class BufferedNote:
    """Buffered / accelerated-return note (PRD 4, 8.1).

    On the upside the investor receives ``upside_leverage`` times the underlier's
    positive performance, capped at ``cap``. On the downside the first
    ``buffer`` fraction of losses is absorbed by the issuer; losses beyond the
    buffer are passed through one-for-one. Path-independent (terminal level only).
    """

    underlier: str
    notional: float
    maturity: date
    issuer: str
    issuer_rating: str
    offer_price: float  # fraction of notional, 1.0 = par
    upside_leverage: float  # e.g. 1.5 = 150% participation on the upside
    cap: float  # max upside as fraction, e.g. 0.25 = +25% cap; 0/None = uncapped
    buffer: float  # downside loss absorbed, e.g. 0.10 = first 10% protected

    product_type: ProductType = field(default=ProductType.BUFFERED_NOTE, init=False)


@dataclass
class DecompositionResult:
    """Output of the pricing engine (PRD 15.2).

    Dollar fields are unrounded; rounding happens only at the display layer.
    ``greeks`` holds delta, vega, rho. ``return_distribution`` is the list of
    per-path total returns (fraction of notional) for the histogram.
    ``payoff_curve`` is a list of (underlier_pct, return_pct) pairs.
    """

    bond_floor: float
    option_value: float
    fair_value: float
    offer_price_dollars: float
    embedded_margin: float
    margin_pct: float
    greeks: dict
    prob_loss: float
    return_distribution: list
    payoff_curve: list

    # Transparency / diagnostics (not in the minimal PRD shape but useful and
    # relied on by the tester to inspect the inputs that produced the result).
    spot: float | None = None
    risk_free: float | None = None
    credit_spread: float | None = None
    div_yield: float | None = None
    atm_vol: float | None = None
    low_confidence_vol: bool = False
    notes: list = field(default_factory=list)
