"""Bond floor valuation (PRD 6.1).

The bond floor is the present value of the issuer's promise to repay principal
at maturity, discounted at the risk-free rate plus the issuer's credit spread:

    PV = N * exp(-(r + s) * T)

Coupons on structured products are conditional (they depend on the underlier
path) and are therefore valued inside the option component, not here. This
function values only the guaranteed principal repayment.
"""

from __future__ import annotations

import math

__all__ = ["price_bond_floor"]


def price_bond_floor(
    notional: float,
    maturity_years: float,
    risk_free: float,
    credit_spread: float,
) -> float:
    """Present value of principal repaid at maturity.

    Checkpoint (PRD 15.5): notional=100_000, T=1, r=5%, spread=1% -> ~94,176.
    """
    if maturity_years < 0:
        raise ValueError("maturity_years must be non-negative")
    discount_rate = risk_free + credit_spread
    return notional * math.exp(-discount_rate * maturity_years)
