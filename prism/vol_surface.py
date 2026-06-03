"""Implied volatility surface construction (PRD 6.2, 15.3).

Builds a volatility surface from a listed options chain and exposes
``get_vol(strike, tenor_years)`` for the pricing engine. The surface uses a
robust, dependency-light interpolation rather than a full SVI/SABR calibration:

* For each available expiry, fit a quadratic smile in log-moneyness
  (k = ln(strike / spot)) by least squares -- this captures the skew/smile
  shape that SVI/SABR target, but is numerically stable on sparse free data.
* Across expiries, interpolate the per-tenor smiles in total variance
  (w = vol**2 * T) linearly in T, which is the standard arbitrage-aware way to
  move volatility between maturities. Extrapolation is flat in variance.

Sparse-data handling (PRD 11, 15.6): if too few usable quotes are available the
surface is flagged ``low_confidence`` and falls back to a flat ATM vol (or a
sensible default) rather than raising. Callers should propagate that flag into
the result.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

__all__ = ["VolSurface", "build_vol_surface", "DEFAULT_FALLBACK_VOL"]

DEFAULT_FALLBACK_VOL = 0.30  # used only when a chain yields no usable quotes
_MIN_QUOTES_PER_EXPIRY = 4
_MIN_TOTAL_QUOTES = 8
_VOL_FLOOR = 0.02
_VOL_CAP = 3.0


@dataclass
class _Smile:
    """Quadratic vol smile in log-moneyness for a single expiry."""

    tenor: float
    coeffs: np.ndarray  # [c0, c1, c2] for vol(k) = c0 + c1*k + c2*k**2
    atm_vol: float

    def vol(self, log_moneyness: float) -> float:
        c0, c1, c2 = self.coeffs
        v = c0 + c1 * log_moneyness + c2 * log_moneyness * log_moneyness
        return float(np.clip(v, _VOL_FLOOR, _VOL_CAP))


@dataclass
class VolSurface:
    """Queryable implied-vol surface.

    Use :func:`build_vol_surface` to construct one from an options chain.
    """

    spot: float
    smiles: list = field(default_factory=list)  # sorted by tenor
    low_confidence: bool = False
    atm_vol: float = DEFAULT_FALLBACK_VOL
    flat_vol: float | None = None  # set when falling back to a single number

    def get_vol(self, strike: float, tenor_years: float) -> float:
        """Implied vol for a given ``strike`` and ``tenor_years`` (fraction)."""
        if self.flat_vol is not None or not self.smiles:
            return float(np.clip(self.flat_vol or self.atm_vol, _VOL_FLOOR, _VOL_CAP))

        tenor_years = max(tenor_years, 1e-6)
        k = math.log(max(strike, 1e-9) / self.spot)

        tenors = [s.tenor for s in self.smiles]

        # Below the shortest / above the longest expiry: flat in total variance.
        if tenor_years <= tenors[0]:
            return self.smiles[0].vol(k)
        if tenor_years >= tenors[-1]:
            return self.smiles[-1].vol(k)

        # Bracket the target tenor and interpolate linearly in total variance.
        hi = next(i for i, t in enumerate(tenors) if t >= tenor_years)
        lo = hi - 1
        s_lo, s_hi = self.smiles[lo], self.smiles[hi]
        v_lo, v_hi = s_lo.vol(k), s_hi.vol(k)
        w_lo = v_lo * v_lo * s_lo.tenor
        w_hi = v_hi * v_hi * s_hi.tenor
        frac = (tenor_years - s_lo.tenor) / (s_hi.tenor - s_lo.tenor)
        w = w_lo + frac * (w_hi - w_lo)
        vol = math.sqrt(max(w, 0.0) / tenor_years)
        return float(np.clip(vol, _VOL_FLOOR, _VOL_CAP))


def _clean_quotes(chain: pd.DataFrame, spot: float) -> pd.DataFrame:
    """Filter an options chain down to usable implied-vol quotes."""
    df = chain.copy()
    if "impliedVolatility" not in df or "strike" not in df:
        return df.iloc[0:0]
    df = df[
        df["impliedVolatility"].notna()
        & (df["impliedVolatility"] > _VOL_FLOOR)
        & (df["impliedVolatility"] < _VOL_CAP)
        & df["strike"].notna()
        & (df["strike"] > 0)
    ]
    if "tenor_years" in df:
        df = df[df["tenor_years"] > 0]
    # Drop far out-of-the-money wings where free IVs are noisiest.
    df = df[(df["strike"] > 0.4 * spot) & (df["strike"] < 2.0 * spot)]
    return df


def _fit_smile(group: pd.DataFrame, spot: float, tenor: float) -> _Smile | None:
    """Fit a quadratic smile in log-moneyness for one expiry; None if too sparse."""
    g = group.dropna(subset=["impliedVolatility", "strike"])
    if len(g) < _MIN_QUOTES_PER_EXPIRY:
        return None
    k = np.log(g["strike"].to_numpy(dtype=float) / spot)
    iv = g["impliedVolatility"].to_numpy(dtype=float)

    # Degree falls back gracefully with the number of points.
    degree = 2 if len(g) >= 5 else 1
    try:
        coeffs = np.polyfit(k, iv, degree)
    except Exception:  # noqa: BLE001
        return None
    # np.polyfit returns highest-order first; reorder to [c0, c1, c2].
    coeffs = coeffs[::-1]
    full = np.zeros(3)
    full[: len(coeffs)] = coeffs

    atm = float(np.clip(full[0], _VOL_FLOOR, _VOL_CAP))  # vol at k=0
    return _Smile(tenor=tenor, coeffs=full, atm_vol=atm)


def build_vol_surface(chain: pd.DataFrame, spot: float) -> VolSurface:
    """Build a :class:`VolSurface` from an options ``chain`` and ``spot``.

    Never raises on sparse data: instead it returns a low-confidence surface
    backed by a flat vol. Checkpoint (PRD 15.5): ``get_vol(spot, 1.0)`` is close
    to the ATM implied vol observed in the chain.
    """
    if spot <= 0:
        raise ValueError("spot must be positive")

    clean = _clean_quotes(chain, spot)

    # Rough ATM estimate: median IV of near-the-money quotes.
    atm_est = DEFAULT_FALLBACK_VOL
    if not clean.empty:
        near = clean[
            (clean["strike"] > 0.9 * spot) & (clean["strike"] < 1.1 * spot)
        ]
        ref = near if not near.empty else clean
        atm_est = float(np.clip(ref["impliedVolatility"].median(), _VOL_FLOOR, _VOL_CAP))

    if clean.empty or len(clean) < _MIN_TOTAL_QUOTES or "tenor_years" not in clean:
        return VolSurface(
            spot=spot,
            smiles=[],
            low_confidence=True,
            atm_vol=atm_est,
            flat_vol=atm_est,
        )

    smiles: list[_Smile] = []
    for tenor, group in clean.groupby("tenor_years"):
        smile = _fit_smile(group, spot, float(tenor))
        if smile is not None:
            smiles.append(smile)

    if not smiles:
        return VolSurface(
            spot=spot,
            smiles=[],
            low_confidence=True,
            atm_vol=atm_est,
            flat_vol=atm_est,
        )

    smiles.sort(key=lambda s: s.tenor)
    # Confidence: need a reasonable spread of expiries and quotes.
    low_conf = len(smiles) < 2 or len(clean) < 2 * _MIN_TOTAL_QUOTES
    return VolSurface(
        spot=spot,
        smiles=smiles,
        low_confidence=low_conf,
        atm_vol=atm_est,
        flat_vol=None,
    )
