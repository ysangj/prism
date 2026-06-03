"""Geometric Brownian Motion Monte Carlo engine (PRD 6.2, 15.3, 15.6).

Simulates all paths as a single ``(n_paths, n_steps + 1)`` NumPy array -- no
Python loop over paths -- and evaluates structured-product payoffs on them.

Key conventions
---------------
* Risk-neutral drift uses ``(risk_free - div_yield)``.
* Per-step volatility is taken from the vol surface at the ATM-ish forward and a
  per-step tenor; a single representative term vol is used for the diffusion to
  keep the simulation a clean GBM (the surface still drives the level of vol and
  the bond/option split). This is standard for first-pass structured-note MC.
* Reproducibility: pass ``seed`` to get deterministic paths
  (``np.random.default_rng(seed)``); default ``seed=None`` is random.
* Performance target: < 5s for 100k paths (PRD 9). The vectorized cumulative-sum
  formulation comfortably meets this.
"""

from __future__ import annotations

import numpy as np

__all__ = ["simulate_paths", "price_option_mc", "european_call_mc"]


def simulate_paths(
    spot: float,
    vol_surface,
    risk_free: float,
    div_yield: float,
    maturity_years: float,
    n_paths: int = 100_000,
    n_steps: int | None = None,
    seed: int | None = None,
    vol: float | None = None,
) -> np.ndarray:
    """Simulate GBM price paths.

    Returns an array of shape ``(n_paths, n_steps + 1)`` where column 0 is the
    initial ``spot``.

    ``vol`` overrides the surface lookup with a single flat volatility (used by
    the European-call validation and by Greeks bumps). When ``vol`` is None the
    representative volatility is read from ``vol_surface`` at the ATM strike for
    ``maturity_years``.
    """
    if maturity_years <= 0:
        raise ValueError("maturity_years must be positive")
    if n_steps is None:
        # ~monthly steps, but at least 12 and capped for performance.
        n_steps = int(np.clip(round(maturity_years * 12), 12, 600))

    if vol is None:
        if vol_surface is None:
            raise ValueError("either vol or vol_surface must be provided")
        vol = vol_surface.get_vol(spot, maturity_years)

    dt = maturity_years / n_steps
    drift = (risk_free - div_yield - 0.5 * vol * vol) * dt
    diffusion = vol * np.sqrt(dt)

    rng = np.random.default_rng(seed)
    z = rng.standard_normal((n_paths, n_steps))
    log_increments = drift + diffusion * z

    # Cumulative log returns -> price levels. Prepend a zero column for t=0.
    cum = np.cumsum(log_increments, axis=1)
    log_paths = np.concatenate([np.zeros((n_paths, 1)), cum], axis=1)
    return spot * np.exp(log_paths)


def step_indices_for_times(
    obs_times, maturity_years: float, n_steps: int
) -> list:
    """Map observation year-fractions to the nearest simulation step index.

    The final observation is snapped to the last step so maturity always lands
    on the terminal column.
    """
    obs_times = np.asarray(obs_times, dtype=float)
    raw = np.round(obs_times / maturity_years * n_steps).astype(int)
    raw = np.clip(raw, 1, n_steps)
    raw[-1] = n_steps  # ensure final obs == maturity
    return raw.tolist()


def price_option_mc(product, paths: np.ndarray, risk_free: float, **kwargs) -> float:
    """Monte Carlo option value (mean discounted cashflow) for a product.

    This is a thin dispatcher kept for the PRD signature. The engine generally
    calls the per-product cashflow functions in ``payoffs`` directly because it
    needs the per-path return distribution as well as the mean PV. ``kwargs``
    must supply ``obs_indices``, ``obs_times`` and (for reverse convertibles)
    ``maturity_years``.
    """
    from ..models import Autocallable, ReverseConvertible
    from . import payoffs

    if isinstance(product, Autocallable):
        total_pv, _ = payoffs.autocallable_cashflows(
            paths, product, kwargs["obs_indices"], kwargs["obs_times"], risk_free
        )
    elif isinstance(product, ReverseConvertible):
        total_pv, _ = payoffs.reverse_convertible_cashflows(
            paths,
            product,
            kwargs["obs_indices"],
            kwargs["obs_times"],
            risk_free,
            kwargs["maturity_years"],
        )
    else:
        raise TypeError(f"unsupported product type: {type(product).__name__}")

    return float(total_pv.mean())


def european_call_mc(
    spot: float,
    strike: float,
    risk_free: float,
    vol: float,
    maturity_years: float,
    div_yield: float = 0.0,
    n_paths: int = 100_000,
    seed: int | None = None,
) -> float:
    """Price a European call by Monte Carlo (validation against Black-Scholes).

    Checkpoint (PRD 15.5): must match :func:`black_scholes.bs_call` within 1%.
    Uses a single terminal step since the payoff is path-independent.
    """
    paths = simulate_paths(
        spot=spot,
        vol_surface=None,
        risk_free=risk_free,
        div_yield=div_yield,
        maturity_years=maturity_years,
        n_paths=n_paths,
        n_steps=1,
        seed=seed,
        vol=vol,
    )
    terminal = paths[:, -1]
    payoff = np.maximum(terminal - strike, 0.0)
    return float(np.exp(-risk_free * maturity_years) * payoff.mean())
