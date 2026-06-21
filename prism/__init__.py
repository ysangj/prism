"""Prism -- structured product pricing & decomposition engine (Phase 1 core).

Public API
----------
    from prism import price_product, decompose
    result = price_product(autocallable)

``price_product`` (and its ``decompose`` alias) fetches market data, builds the
vol surface, prices the bond floor and the embedded option via Monte Carlo, runs
bump-and-reprice Greeks, and returns a populated :class:`DecompositionResult`.

Determinism: pass ``seed=`` to ``price_product`` for reproducible Monte Carlo
output (used by the tester). Market inputs (spot, curve, vol, dividend, credit
spread) can also be supplied explicitly to run fully offline / deterministically.
"""

from __future__ import annotations

import os

import numpy as np

from . import bond_floor as _bond_floor
from . import market_data, models, risk, vol_surface
from ._env import load_local_env
from .models import (
    Autocallable,
    BarrierNote,
    BufferedNote,
    DecompositionResult,
    PrincipalProtected,
    ProductType,
    ReverseConvertible,
    observations_per_year,
    tenor_years,
)
from .pricing import black_scholes, monte_carlo, payoffs
from .report import build_report_pdf, report_filename

__all__ = [
    "price_product",
    "decompose",
    "build_report_pdf",
    "report_filename",
    "load_local_env",
    "Autocallable",
    "ReverseConvertible",
    "PrincipalProtected",
    "BarrierNote",
    "BufferedNote",
    "ProductType",
    "DecompositionResult",
    "black_scholes",
    "monte_carlo",
    "payoffs",
    "vol_surface",
    "bond_floor",
    "market_data",
    "risk",
    "models",
]

# Re-export the bond floor pricer at package level for convenience.
price_bond_floor = _bond_floor.price_bond_floor
bond_floor = _bond_floor

_DEFAULT_N_PATHS = 100_000
_PAYOFF_GRID = np.linspace(-0.5, 0.5, 101)  # -50%..+50% underlier moves (PRD 8.3)

# All five MVP product types the engine prices end-to-end (PRD 4).
_SUPPORTED_TYPES = (
    Autocallable,
    ReverseConvertible,
    PrincipalProtected,
    BarrierNote,
    BufferedNote,
)

# Terminal-payoff (path-independent) types. PPN and Buffered are always terminal;
# the Barrier note is terminal for a European barrier but path-monitored for an
# American barrier (handled inside its cashflow function via obs_indices).
_TERMINAL_TYPES = (PrincipalProtected, BarrierNote, BufferedNote)


def _interp_rate(curve: dict, tenor: float) -> float:
    """Cubic-ish interpolation of the Treasury curve at ``tenor`` years.

    Uses SciPy cubic spline when enough nodes exist (PRD 6.1), else linear.
    Flat extrapolation beyond the curve's ends.
    """
    tenors = np.array(sorted(curve))
    rates = np.array([curve[t] for t in tenors])
    if tenor <= tenors[0]:
        return float(rates[0])
    if tenor >= tenors[-1]:
        return float(rates[-1])
    if len(tenors) >= 4:
        try:
            from scipy.interpolate import CubicSpline

            return float(CubicSpline(tenors, rates)(tenor))
        except Exception:  # noqa: BLE001
            pass
    return float(np.interp(tenor, tenors, rates))


def _resolve_market_inputs(product, overrides: dict) -> dict:
    """Gather spot, curve rate, dividend yield, credit spread, and vol surface.

    Any value present in ``overrides`` short-circuits the corresponding fetch so
    the engine can run offline / deterministically. ``overrides`` may contain:
    ``spot``, ``risk_free``, ``div_yield``, ``credit_spread``, ``flat_vol``,
    ``treasury_curve``, ``fred_api_key``.

    ``fred_api_key`` (BYOK) is used only to fetch the live Treasury curve when no
    explicit ``treasury_curve``/``risk_free`` override is given. It is read here
    first, else from the ``FRED_API_KEY`` environment variable. The key is never
    logged, persisted, or echoed in notes/errors.
    """
    notes: list[str] = []
    low_confidence_curve = False
    t_years = tenor_years(product.maturity)

    spot = overrides.get("spot")
    if spot is None:
        spot = market_data.get_spot(product.underlier)

    div_yield = overrides.get("div_yield")
    if div_yield is None:
        div_yield = market_data.get_dividend_yield(product.underlier)

    risk_free = overrides.get("risk_free")
    if risk_free is None:
        curve = overrides.get("treasury_curve")
        if curve is None:
            # Resolve the FRED key: explicit override first, then environment.
            fred_key = overrides.get("fred_api_key") or os.environ.get(
                "FRED_API_KEY"
            )
            if fred_key:
                # Key present: fetch live; a genuine failure correctly raises.
                curve = market_data.get_treasury_curve(api_key=fred_key)
            else:
                # No key: documented static fallback — never crash live pricing.
                curve = market_data.static_treasury_curve()
                low_confidence_curve = True
                notes.append(
                    "Treasury curve: no FRED key provided — using a static "
                    f"fallback curve (as-of {market_data.STATIC_CURVE_AS_OF}); "
                    "rates are LOW CONFIDENCE."
                )
        risk_free = _interp_rate(curve, t_years)

    credit_spread = overrides.get("credit_spread")
    if credit_spread is None:
        credit_spread = market_data.get_credit_spread(
            product.issuer, product.issuer_rating
        )

    flat_vol = overrides.get("flat_vol")
    if flat_vol is not None:
        surface = vol_surface.VolSurface(
            spot=spot, smiles=[], low_confidence=False, atm_vol=flat_vol,
            flat_vol=flat_vol,
        )
    else:
        chain = market_data.get_options_chain(product.underlier)
        surface = vol_surface.build_vol_surface(chain, spot)
        if surface.low_confidence:
            notes.append(
                "Vol surface flagged LOW CONFIDENCE: options chain was sparse; "
                "fell back toward a flat ATM volatility."
            )

    return {
        "spot": spot,
        "div_yield": div_yield,
        "risk_free": risk_free,
        "credit_spread": credit_spread,
        "surface": surface,
        "maturity_years": t_years,
        "low_confidence_curve": low_confidence_curve,
        "notes": notes,
    }


def _observation_schedule(product, maturity_years: float):
    """Return (obs_times, n_per_year) for a product's observation dates.

    For terminal-payoff types only the maturity level matters, except an American
    barrier note, which needs a monitoring grid; we use a monthly grid there so
    the path-min check (in ``barrier_note_cashflows``) is meaningful.
    """
    if isinstance(product, Autocallable):
        n_per_year = observations_per_year(product.observation_freq)
    elif isinstance(product, ReverseConvertible):
        n_per_year = 4  # quarterly coupons by convention
    elif isinstance(product, BarrierNote) and product.barrier_type.lower() == "american":
        n_per_year = 12  # monthly monitoring grid for the American barrier
    else:  # PPN, buffered, European barrier: terminal level only
        n_per_year = 1
    n_obs = max(int(round(maturity_years * n_per_year)), 1)
    obs_times = np.array(
        [maturity_years * (i + 1) / n_obs for i in range(n_obs)], dtype=float
    )
    return obs_times, n_per_year


def _price_option_component(
    product,
    spot: float,
    surface,
    risk_free: float,
    div_yield: float,
    maturity_years: float,
    n_paths: int,
    seed: int | None,
    vol_shift: float = 0.0,
    strike: float | None = None,
):
    """Run MC and return (option_value, total_returns, prob_loss).

    ``option_value`` is the embedded option's PV: the mean discounted total
    investor cashflow minus the bond floor (added back by the caller). Here we
    return the *full* mean PV of investor cashflows; the caller subtracts the
    bond floor to isolate the option value. ``vol_shift`` is an additive bump to
    the surface vol used by the Greeks engine.
    """
    base_vol = surface.get_vol(spot, maturity_years)
    vol = max(base_vol + vol_shift, 0.01)

    n_steps = int(np.clip(round(maturity_years * 12), 12, 600))
    paths = monte_carlo.simulate_paths(
        spot=spot,
        vol_surface=None,
        risk_free=risk_free,
        div_yield=div_yield,
        maturity_years=maturity_years,
        n_paths=n_paths,
        n_steps=n_steps,
        seed=seed,
        vol=vol,
    )

    obs_times, _ = _observation_schedule(product, maturity_years)
    obs_indices = monte_carlo.step_indices_for_times(
        obs_times, maturity_years, n_steps
    )

    if isinstance(product, Autocallable):
        total_pv, total_return = payoffs.autocallable_cashflows(
            paths, product, obs_indices, obs_times, risk_free, strike=strike
        )
    elif isinstance(product, ReverseConvertible):
        total_pv, total_return = payoffs.reverse_convertible_cashflows(
            paths, product, obs_indices, obs_times, risk_free, maturity_years,
            strike=strike,
        )
    elif isinstance(product, PrincipalProtected):
        total_pv, total_return = payoffs.principal_protected_cashflows(
            paths, product, obs_indices, obs_times, risk_free, maturity_years,
            strike=strike,
        )
    elif isinstance(product, BarrierNote):
        total_pv, total_return = payoffs.barrier_note_cashflows(
            paths, product, obs_indices, obs_times, risk_free, maturity_years,
            strike=strike,
        )
    elif isinstance(product, BufferedNote):
        total_pv, total_return = payoffs.buffered_note_cashflows(
            paths, product, obs_indices, obs_times, risk_free, maturity_years,
            strike=strike,
        )
    else:
        raise TypeError(f"unsupported product type: {type(product).__name__}")

    mean_pv = float(total_pv.mean())
    prob_loss = float(np.mean(total_return < 0.0))
    return mean_pv, total_return, prob_loss


def _payoff_curve(product, total_return_sample) -> list:
    """Build a (underlier_pct, return_pct) curve for the payoff diagram.

    Computes the investor's total return deterministically at each terminal
    underlier level on the -50%..+50% grid, holding coupons at their full
    accrued value (terminal-level scenario, the standard payoff-diagram view).
    """
    curve = []
    notional = product.notional

    if isinstance(product, Autocallable):
        t = tenor_years(product.maturity)
        n_per_year = observations_per_year(product.observation_freq)
        n_obs = max(int(round(t * n_per_year)), 1)
        full_coupons = product.coupon_rate * t * notional  # if held to maturity
        for move in _PAYOFF_GRID:
            final_mult = 1.0 + move
            if final_mult >= product.coupon_barrier:
                coupons = full_coupons
            else:
                coupons = 0.0
            if final_mult >= product.knock_in_barrier:
                principal = notional
            else:
                principal = notional * final_mult
            total_ret = (coupons + principal - notional) / notional
            curve.append((round(move * 100, 2), round(total_ret * 100, 4)))
    elif isinstance(product, ReverseConvertible):
        t = tenor_years(product.maturity)
        full_coupons = product.coupon_rate * t * notional
        for move in _PAYOFF_GRID:
            final_mult = 1.0 + move
            converts = final_mult < product.barrier
            principal = notional * final_mult if converts else notional
            total_ret = (full_coupons + principal - notional) / notional
            curve.append((round(move * 100, 2), round(total_ret * 100, 4)))

    elif isinstance(product, PrincipalProtected):
        for move in _PAYOFF_GRID:
            upside = product.participation * max(move, 0.0)
            if product.cap is not None and product.cap > 0.0:
                upside = min(upside, product.cap)
            total_ret = product.floor - 1.0 + upside
            curve.append((round(move * 100, 2), round(total_ret * 100, 4)))

    elif isinstance(product, BarrierNote):
        # Terminal-level payoff diagram: a final level at/above the barrier earns
        # the fixed digital return; below it the principal tracks the underlier.
        for move in _PAYOFF_GRID:
            final_mult = 1.0 + move
            if final_mult >= product.barrier:
                total_ret = product.fixed_return
            else:
                total_ret = final_mult - 1.0
            curve.append((round(move * 100, 2), round(total_ret * 100, 4)))

    else:  # BufferedNote
        for move in _PAYOFF_GRID:
            if move >= 0.0:
                ret = product.upside_leverage * move
                if product.cap is not None and product.cap > 0.0:
                    ret = min(ret, product.cap)
            else:
                ret = min(move + product.buffer, 0.0)
            curve.append((round(move * 100, 2), round(ret * 100, 4)))

    return curve


def price_product(
    product,
    n_paths: int = _DEFAULT_N_PATHS,
    seed: int | None = None,
    **market_overrides,
) -> DecompositionResult:
    """Price a structured product end-to-end and return a DecompositionResult.

    Parameters
    ----------
    product : an :class:`Autocallable` or :class:`ReverseConvertible`.
    n_paths : Monte Carlo path count (default 100k; PRD 9 latency target).
    seed : RNG seed for reproducible output. The Greeks reuse this seed so bumps
        use common random numbers.
    market_overrides : optional explicit market inputs to run offline:
        ``spot``, ``risk_free``, ``div_yield``, ``credit_spread``, ``flat_vol``,
        ``treasury_curve``, ``fred_api_key``.

        ``fred_api_key`` (BYOK) is forwarded to
        ``market_data.get_treasury_curve(api_key=...)`` only when no
        ``treasury_curve``/``risk_free`` override is supplied. With no key and no
        curve override, pricing uses the documented static fallback curve, sets
        ``low_confidence_curve=True`` on the result, and adds a note — it never
        crashes merely because ``FRED_API_KEY`` is unset. The key is never logged.

    Raises
    ------
    market_data.MarketDataError if a required market input cannot be fetched and
    is not supplied via ``market_overrides``. (A missing FRED key is not such a
    case — it degrades to the static curve. A FRED fetch failure *with* a key
    supplied does raise.)
    """
    if not isinstance(product, _SUPPORTED_TYPES):
        raise TypeError(
            "price_product supports "
            + ", ".join(t.__name__ for t in _SUPPORTED_TYPES)
            + f"; got {type(product).__name__}"
        )

    mkt = _resolve_market_inputs(product, market_overrides)
    spot = mkt["spot"]
    surface = mkt["surface"]
    risk_free = mkt["risk_free"]
    div_yield = mkt["div_yield"]
    credit_spread = mkt["credit_spread"]
    t_years = mkt["maturity_years"]
    low_confidence_curve = mkt["low_confidence_curve"]
    notes = list(mkt["notes"])

    # Use a concrete seed for internal consistency between the base price and the
    # Greeks bumps (common random numbers). If the user passed seed=None we still
    # fix one here so delta/vega/rho are not swamped by MC noise.
    eff_seed = seed if seed is not None else 12345

    # Bond floor (guaranteed principal repayment).
    floor = _bond_floor.price_bond_floor(
        product.notional, t_years, risk_free, credit_spread
    )

    # Embedded option component = mean investor PV - bond floor.
    mean_pv, total_return, prob_loss = _price_option_component(
        product, spot, surface, risk_free, div_yield, t_years, n_paths, eff_seed
    )
    option_value = mean_pv - floor

    fair_value = floor + option_value  # == mean_pv, kept explicit for clarity
    offer_dollars = product.offer_price * product.notional
    embedded_margin = offer_dollars - fair_value
    margin_pct = embedded_margin / product.notional * 100.0

    # ---- Greeks via bump-and-reprice (common random numbers) ----
    # Pin the barrier-reference strike to the original fixing level so that a
    # spot bump moves the underlier *relative to* the strike. Without this the
    # at-inception payoff is scale-invariant in spot and delta is identically 0.
    strike_ref = spot

    def _reprice(inputs: dict) -> float:
        s = inputs["spot"]
        r = inputs["risk_free"]
        vshift = inputs.get("vol_shift", 0.0)
        pv, _, _ = _price_option_component(
            product, s, surface, r, div_yield, t_years, n_paths, eff_seed,
            vol_shift=vshift, strike=strike_ref,
        )
        # Fair value == mean investor PV: the MC already discounts the full set
        # of cashflows (coupons + principal redemption) under the risk-free
        # measure, so the bond floor is implicit inside pv and we do not add it
        # again. The bond floor / option split is purely for decomposition.
        return pv

    greeks = risk.compute_greeks(
        {"spot": spot, "risk_free": risk_free, "vol_shift": 0.0},
        _reprice,
    )

    payoff_curve = _payoff_curve(product, total_return)
    atm_vol = surface.get_vol(spot, t_years)

    return DecompositionResult(
        bond_floor=floor,
        option_value=option_value,
        fair_value=fair_value,
        offer_price_dollars=offer_dollars,
        embedded_margin=embedded_margin,
        margin_pct=margin_pct,
        greeks=greeks,
        prob_loss=prob_loss,
        return_distribution=total_return.tolist(),
        payoff_curve=payoff_curve,
        spot=spot,
        risk_free=risk_free,
        credit_spread=credit_spread,
        div_yield=div_yield,
        atm_vol=atm_vol,
        low_confidence_vol=surface.low_confidence,
        low_confidence_curve=low_confidence_curve,
        notes=notes,
    )


def decompose(product, **kwargs) -> DecompositionResult:
    """Alias for :func:`price_product` (PRD 15.3)."""
    return price_product(product, **kwargs)
