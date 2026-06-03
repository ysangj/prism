"""Closed-form Black-Scholes pricing for European and digital options.

Used both as a primary pricer for the simple components of a structured product
and as a validation cross-check against the Monte Carlo engine (PRD 6.2).

All functions are pure and take a continuously-compounded dividend yield
``div_yield`` so the same code prices both dividend-paying and non-dividend
underliers. Volatility ``vol`` is an annualized standard deviation (e.g. 0.20).
"""

from __future__ import annotations

import math

from scipy.stats import norm

__all__ = ["bs_call", "bs_put", "bs_digital"]


def _d1_d2(
    spot: float,
    strike: float,
    risk_free: float,
    vol: float,
    maturity_years: float,
    div_yield: float,
) -> tuple[float, float]:
    """Return the Black-Scholes ``d1`` and ``d2`` terms."""
    if maturity_years <= 0:
        raise ValueError("maturity_years must be positive")
    if vol <= 0:
        raise ValueError("vol must be positive")
    if spot <= 0 or strike <= 0:
        raise ValueError("spot and strike must be positive")

    vol_sqrt_t = vol * math.sqrt(maturity_years)
    d1 = (
        math.log(spot / strike)
        + (risk_free - div_yield + 0.5 * vol * vol) * maturity_years
    ) / vol_sqrt_t
    d2 = d1 - vol_sqrt_t
    return d1, d2


def bs_call(
    spot: float,
    strike: float,
    risk_free: float,
    vol: float,
    maturity_years: float,
    div_yield: float = 0.0,
) -> float:
    """Price a European call option.

    Checkpoint (PRD 15.5): spot=100, strike=100, r=5%, vol=20%, T=1 -> ~10.45.
    """
    d1, d2 = _d1_d2(spot, strike, risk_free, vol, maturity_years, div_yield)
    return spot * math.exp(-div_yield * maturity_years) * norm.cdf(d1) - strike * math.exp(
        -risk_free * maturity_years
    ) * norm.cdf(d2)


def bs_put(
    spot: float,
    strike: float,
    risk_free: float,
    vol: float,
    maturity_years: float,
    div_yield: float = 0.0,
) -> float:
    """Price a European put option."""
    d1, d2 = _d1_d2(spot, strike, risk_free, vol, maturity_years, div_yield)
    return strike * math.exp(-risk_free * maturity_years) * norm.cdf(-d2) - spot * math.exp(
        -div_yield * maturity_years
    ) * norm.cdf(-d1)


def bs_digital(
    spot: float,
    strike: float,
    risk_free: float,
    vol: float,
    maturity_years: float,
    div_yield: float = 0.0,
    payoff: float = 1.0,
    option_type: str = "call",
) -> float:
    """Price a cash-or-nothing digital (binary) option.

    Pays ``payoff`` if the underlier finishes above (call) or below (put) the
    strike at maturity. Default ``payoff=1.0`` gives the risk-neutral
    probability of finishing in the money, discounted to today.
    """
    _, d2 = _d1_d2(spot, strike, risk_free, vol, maturity_years, div_yield)
    discount = math.exp(-risk_free * maturity_years)
    if option_type == "call":
        return payoff * discount * norm.cdf(d2)
    if option_type == "put":
        return payoff * discount * norm.cdf(-d2)
    raise ValueError("option_type must be 'call' or 'put'")
