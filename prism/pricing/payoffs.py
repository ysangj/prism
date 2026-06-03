"""Per-product payoff functions (PRD 15.3).

Each payoff maps simulated price paths to discounted cashflows. The functions
are fully vectorized over paths: they accept a ``(n_paths, n_steps + 1)`` array
of simulated prices (column 0 is the initial spot) and return per-path results
as 1-D NumPy arrays. No Python loop over paths.

Returned quantities
-------------------
``*_pv(...)`` helpers return, per path, the present value of the *option*
component only -- that is, the total discounted cashflow to the investor minus
the bond floor's guaranteed principal repayment. The pricing engine adds the
bond floor back separately so that decomposition (bond floor vs. option value)
is clean. Each function also returns per-path total investor return (fraction of
notional) for the P(loss) and return-distribution metrics.

Observation handling (PRD 15.6): autocall/coupon observation dates are mapped to
the nearest simulation step by the caller and passed in as ``obs_indices``.
"""

from __future__ import annotations

import numpy as np

from ..models import (
    Autocallable,
    BarrierNote,
    BufferedNote,
    PrincipalProtected,
    ReverseConvertible,
)

__all__ = [
    "autocallable_cashflows",
    "reverse_convertible_cashflows",
    "principal_protected_cashflows",
    "barrier_note_cashflows",
    "buffered_note_cashflows",
    "autocallable_payoff",
]


def _discount_factors(times: np.ndarray, risk_free: float) -> np.ndarray:
    """Continuous-compounding discount factors for an array of times."""
    return np.exp(-risk_free * times)


def autocallable_cashflows(
    paths: np.ndarray,
    product: Autocallable,
    obs_indices: list,
    obs_times: np.ndarray,
    risk_free: float,
    strike: float | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Discounted total investor PV and total return per path for an autocallable.

    Parameters
    ----------
    paths : (n_paths, n_steps + 1) array of simulated prices.
    product : the Autocallable term sheet.
    obs_indices : step indices (into ``paths`` columns) of each observation date,
        in chronological order. The final observation must coincide with maturity.
    obs_times : year-fractions matching ``obs_indices``.
    risk_free : risk-free rate used for discounting.
    strike : the fixed initial-fixing level the barriers reference. Defaults to
        each path's own starting level (``paths[:, 0]``), which makes the payoff
        scale-invariant (the at-inception case). For delta bump-and-reprice the
        engine pins ``strike`` to the original fixing so that bumping spot moves
        the underlier *relative to* the strike and delta is non-trivial.

    Returns
    -------
    (total_pv, total_return)
        ``total_pv`` -- present value of all investor cashflows per path
        (coupons + redemption), in dollars.
        ``total_return`` -- undiscounted total return as a fraction of notional
        (coupons received + redemption - notional) / notional, per path.
    """
    n_paths = paths.shape[0]
    initial = paths[:, 0] if strike is None else float(strike)
    notional = product.notional

    coupon_barrier_lvl = product.coupon_barrier * initial
    call_barrier_lvl = product.call_barrier * initial
    knock_in_lvl = product.knock_in_barrier * initial

    obs_idx = np.asarray(obs_indices, dtype=int)
    obs_times = np.asarray(obs_times, dtype=float)
    n_obs = len(obs_idx)

    # Per-period coupon amount (simple accrual between observations).
    if n_obs > 1:
        period = float(np.mean(np.diff(obs_times)))
    else:
        period = float(obs_times[0])
    coupon_amt = product.coupon_rate * period * notional

    discounts = _discount_factors(obs_times, risk_free)

    total_pv = np.zeros(n_paths)
    total_undiscounted = np.zeros(n_paths)  # nominal cashflows (for return calc)
    alive = np.ones(n_paths, dtype=bool)  # not yet autocalled

    for j in range(n_obs):
        idx = obs_idx[j]
        level = paths[:, idx]
        df = discounts[j]

        # Conditional coupon: paid to still-alive paths above the coupon barrier.
        pays_coupon = alive & (level >= coupon_barrier_lvl)
        total_pv += np.where(pays_coupon, coupon_amt * df, 0.0)
        total_undiscounted += np.where(pays_coupon, coupon_amt, 0.0)

        is_final = j == n_obs - 1
        if not is_final:
            # Early redemption (autocall) at par for alive paths at/above call barrier.
            called = alive & (level >= call_barrier_lvl)
            total_pv += np.where(called, notional * df, 0.0)
            total_undiscounted += np.where(called, notional, 0.0)
            alive &= ~called
        else:
            # Maturity redemption for everything still alive.
            # Principal: par if the final level is at/above the knock-in barrier;
            # otherwise principal tracks the underlier's performance (the
            # conventional final-level Phoenix rule -- discrete intra-path
            # monitoring of the knock-in is a documented future enhancement).
            final_level = level
            perf = final_level / initial  # gross performance multiple
            protected = final_level >= knock_in_lvl

            redemption = np.where(protected, notional, notional * perf)
            redemption = np.where(alive, redemption, 0.0)
            total_pv += redemption * df
            total_undiscounted += np.where(alive, redemption, 0.0)

    total_return = (total_undiscounted - notional) / notional
    return total_pv, total_return


def reverse_convertible_cashflows(
    paths: np.ndarray,
    product: ReverseConvertible,
    obs_indices: list,
    obs_times: np.ndarray,
    risk_free: float,
    maturity_years: float,
    strike: float | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Discounted total investor PV and total return per path for a reverse convertible.

    Pays a fixed coupon on every observation date regardless of underlier level.
    At maturity returns par unless the barrier is breached -- European barrier is
    tested only at the final level; American barrier is tested along the path on
    the observation grid -- in which case principal is converted to the
    underlier's performance.

    ``strike`` pins the barrier-reference level for delta bump-and-reprice; it
    defaults to each path's own starting level (the at-inception, scale-invariant
    case).
    """
    n_paths = paths.shape[0]
    initial = paths[:, 0] if strike is None else float(strike)
    notional = product.notional
    barrier_lvl = product.barrier * initial

    obs_idx = np.asarray(obs_indices, dtype=int)
    obs_times = np.asarray(obs_times, dtype=float)
    n_obs = len(obs_idx)

    if n_obs > 1:
        period = float(np.mean(np.diff(obs_times)))
    else:
        period = float(obs_times[0])
    coupon_amt = product.coupon_rate * period * notional
    discounts = _discount_factors(obs_times, risk_free)

    total_pv = np.zeros(n_paths)
    total_undiscounted = np.zeros(n_paths)

    # Fixed coupons on every observation date.
    for j in range(n_obs):
        total_pv += coupon_amt * discounts[j]
        total_undiscounted += coupon_amt

    final_level = paths[:, obs_idx[-1]]
    perf = final_level / initial

    if product.barrier_type.lower() == "american":
        obs_levels = paths[:, obs_idx]
        breached = obs_levels.min(axis=1) < barrier_lvl
        # Even if breached intraperiod, conversion only bites if finishing below par.
        converts = breached & (final_level < initial)
    else:  # european: barrier observed only at maturity
        converts = final_level < barrier_lvl

    redemption = np.where(converts, notional * perf, notional)
    df_mat = np.exp(-risk_free * maturity_years)
    total_pv += redemption * df_mat
    total_undiscounted += redemption

    total_return = (total_undiscounted - notional) / notional
    return total_pv, total_return


def _is_uncapped(cap) -> bool:
    """A cap of None or <= 0 means the upside is uncapped."""
    return cap is None or cap <= 0.0


def principal_protected_cashflows(
    paths: np.ndarray,
    product: PrincipalProtected,
    obs_indices: list,
    obs_times: np.ndarray,
    risk_free: float,
    maturity_years: float,
    strike: float | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Discounted PV and total return per path for a principal-protected note.

    Terminal payoff (path-independent): the investor receives the greater of the
    protected floor and the floor plus capped, participated upside::

        perf       = S_T / S_0 - 1                     (underlier performance)
        upside     = participation * max(perf, 0)
        upside     = min(upside, cap)                  (if capped)
        redemption = notional * (floor + upside)

    The protected ``floor`` (typically 1.0) guarantees principal at maturity, so
    ``prob_loss`` is ~0 for a fully-protected note (it can only be > 0 if the note
    is offered above the protected level or floor < offer). ``obs_indices`` /
    ``obs_times`` are accepted for a uniform engine signature but unused (only the
    terminal level matters).
    """
    initial = paths[:, 0] if strike is None else float(strike)
    notional = product.notional
    floor = product.floor

    final_level = paths[:, -1]
    perf = final_level / initial - 1.0
    upside = product.participation * np.maximum(perf, 0.0)
    if not _is_uncapped(product.cap):
        upside = np.minimum(upside, product.cap)

    redemption = notional * (floor + upside)
    df = np.exp(-risk_free * maturity_years)
    total_pv = redemption * df
    total_return = (redemption - notional) / notional
    return total_pv, total_return


def barrier_note_cashflows(
    paths: np.ndarray,
    product: BarrierNote,
    obs_indices: list,
    obs_times: np.ndarray,
    risk_free: float,
    maturity_years: float,
    strike: float | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Discounted PV and total return per path for a digital barrier note.

    If the barrier condition holds the investor receives principal plus the fixed
    digital return; otherwise principal tracks the underlier's downside::

        condition holds -> redemption = notional * (1 + fixed_return)
        condition fails -> redemption = notional * (S_T / S_0)   (principal at risk)

    European barrier: condition = terminal level at/above the barrier.
    American barrier: condition = path never closed below the barrier on the
    observation grid AND finishes at/above the barrier (a breach any time
    forfeits the digital payout -- consistent with the Phase 1 RC American-barrier
    monitoring on the observation grid; continuous monitoring is a documented
    simplification).
    """
    initial = paths[:, 0] if strike is None else float(strike)
    notional = product.notional
    barrier_lvl = product.barrier * initial

    final_level = paths[:, -1]
    perf = final_level / initial

    if product.barrier_type.lower() == "american":
        obs_idx = np.asarray(obs_indices, dtype=int)
        obs_levels = paths[:, obs_idx]
        never_breached = obs_levels.min(axis=1) >= barrier_lvl
        pays_digital = never_breached & (final_level >= barrier_lvl)
    else:  # european: observed only at maturity
        pays_digital = final_level >= barrier_lvl

    redemption = np.where(
        pays_digital,
        notional * (1.0 + product.fixed_return),
        notional * perf,
    )
    df = np.exp(-risk_free * maturity_years)
    total_pv = redemption * df
    total_return = (redemption - notional) / notional
    return total_pv, total_return


def buffered_note_cashflows(
    paths: np.ndarray,
    product: BufferedNote,
    obs_indices: list,
    obs_times: np.ndarray,
    risk_free: float,
    maturity_years: float,
    strike: float | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Discounted PV and total return per path for a buffered/accelerated note.

    Terminal payoff (path-independent)::

        perf = S_T / S_0 - 1
        if perf >= 0:  ret = min(upside_leverage * perf, cap)   (leveraged, capped)
        else:          ret = min(perf + buffer, 0)              (buffer absorbs first `buffer` of loss)
        redemption = notional * (1 + ret)

    The buffer protects the first ``buffer`` fraction of losses; losses beyond the
    buffer pass through one-for-one, so ``prob_loss`` is the fraction of paths
    finishing more than ``buffer`` below the initial level.
    """
    initial = paths[:, 0] if strike is None else float(strike)
    notional = product.notional

    final_level = paths[:, -1]
    perf = final_level / initial - 1.0

    up = product.upside_leverage * np.maximum(perf, 0.0)
    if not _is_uncapped(product.cap):
        up = np.minimum(up, product.cap)
    # Downside: absorb the first `buffer` of loss, pass through the rest.
    down = np.minimum(np.minimum(perf, 0.0) + product.buffer, 0.0)
    ret = np.where(perf >= 0.0, up, down)

    redemption = notional * (1.0 + ret)
    df = np.exp(-risk_free * maturity_years)
    total_pv = redemption * df
    total_return = ret  # by construction redemption = notional*(1+ret)
    return total_pv, total_return


def autocallable_payoff(path: np.ndarray, product: Autocallable, obs_indices: list) -> float:
    """Single-path autocallable payoff (undiscounted nominal cashflows).

    Provided to match the PRD 15.3 signature. The vectorized
    :func:`autocallable_cashflows` is what the engine actually uses; this wraps a
    one-path call for clarity and ad-hoc checks.
    """
    p = np.asarray(path, dtype=float).reshape(1, -1)
    initial = p[0, 0]
    notional = product.notional
    obs_idx = np.asarray(obs_indices, dtype=int)

    coupon_barrier_lvl = product.coupon_barrier * initial
    call_barrier_lvl = product.call_barrier * initial
    knock_in_lvl = product.knock_in_barrier * initial
    # One coupon per observation period, accrued over equal periods.
    period = 1.0 / max(len(obs_idx), 1)
    coupon_amt = product.coupon_rate * period * notional

    total = 0.0
    for j, idx in enumerate(obs_idx):
        level = p[0, idx]
        if level >= coupon_barrier_lvl:
            total += coupon_amt
        is_final = j == len(obs_idx) - 1
        if not is_final:
            if level >= call_barrier_lvl:
                total += notional
                return total
        else:
            if level >= knock_in_lvl:
                total += notional
            else:
                total += notional * (level / initial)
    return total
