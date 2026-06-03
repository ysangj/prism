"""Risk metrics via bump-and-reprice (PRD 6.4, 15.3).

Greeks are computed by nudging one market input, re-running the valuation, and
measuring the change (central differences where possible). The caller supplies a
``price_fn`` mapping a dict of bumped market inputs to a fair value, so the same
machinery works for any product.

Bumps
-----
* delta : central difference on spot, scaled to a 1% move of the underlier
          (delta reported as fair-value change per 1% spot move).
* vega  : central difference on volatility, per 1 vol point (0.01).
* rho   : central difference on the risk-free rate, per 1bp (0.0001).

To keep bump-and-reprice stable under Monte Carlo noise, the caller should price
with a fixed RNG seed so the only thing that changes between base and bumped runs
is the bumped input (common random numbers).
"""

from __future__ import annotations

from typing import Callable

__all__ = ["compute_greeks"]


def compute_greeks(
    base_inputs: dict,
    price_fn: Callable[[dict], float],
    spot_bump_frac: float = 0.01,
    vol_bump: float = 0.01,
    rate_bump: float = 0.0001,
) -> dict:
    """Compute delta, vega, rho via central-difference bump-and-reprice.

    Parameters
    ----------
    base_inputs : dict with keys ``spot``, ``vol_scale`` (a multiplicative or
        additive vol shift handled by ``price_fn``), and ``risk_free``.
    price_fn : callable taking a copy of ``base_inputs`` (with one field bumped)
        and returning the product fair value.

    Returns
    -------
    dict with ``delta``, ``vega``, ``rho`` (all in dollars per unit move as
    described in the module docstring).
    """

    def repriced(**overrides) -> float:
        inp = dict(base_inputs)
        inp.update(overrides)
        return price_fn(inp)

    spot = base_inputs["spot"]

    # Delta: per 1% move of the underlier.
    ds = spot * spot_bump_frac
    v_up = repriced(spot=spot + ds)
    v_dn = repriced(spot=spot - ds)
    delta = (v_up - v_dn) / 2.0  # change in value for a +1% spot move

    # Vega: per 1 vol point. base_inputs carries an additive vol shift "vol_shift".
    base_shift = base_inputs.get("vol_shift", 0.0)
    vv_up = repriced(vol_shift=base_shift + vol_bump)
    vv_dn = repriced(vol_shift=base_shift - vol_bump)
    vega = (vv_up - vv_dn) / 2.0  # per +1 vol point (0.01)

    # Rho: per 1bp parallel rate shift.
    r = base_inputs["risk_free"]
    vr_up = repriced(risk_free=r + rate_bump)
    vr_dn = repriced(risk_free=r - rate_bump)
    rho = (vr_up - vr_dn) / 2.0  # per +1bp

    return {"delta": delta, "vega": vega, "rho": rho}
