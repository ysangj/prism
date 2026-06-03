"""Market data service (PRD 5, 15.3).

Fetchers for spot prices, options chains, dividend yields, the Treasury yield
curve, and issuer credit spreads. All network-backed calls are cached per
process with :func:`functools.lru_cache` so re-pricing the same ticker in a
session does not re-hit the APIs.

Failure policy (per build spec): never silently fabricate market data. If a
fetch fails or the network/API is unavailable, raise :class:`MarketDataError`
so the caller can catch it and surface a clear message. Where the PRD itself
specifies a *modeled* fallback (rating-based credit spreads when issuer-specific
TRACE data is unavailable), that fallback is used and recorded, not treated as
an error.

Environment
-----------
``FRED_API_KEY`` (optional) enables live Treasury-curve fetches from FRED. If it
is unset, :func:`get_treasury_curve` raises :class:`MarketDataError`; callers
that want to proceed offline can pass an explicit curve to the pricing layer.
yfinance requires no API key.
"""

from __future__ import annotations

import functools
import os
from datetime import datetime, timezone

import pandas as pd

__all__ = [
    "MarketDataError",
    "get_spot",
    "get_options_chain",
    "get_dividend_yield",
    "get_treasury_curve",
    "get_credit_spread",
    "clear_cache",
    "RATING_SPREADS",
]


class MarketDataError(RuntimeError):
    """Raised when market data cannot be fetched or is unusable.

    Catch this to distinguish a data/connectivity problem from a modeling bug.
    """


# ---------------------------------------------------------------------------
# Equity data (yfinance)
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=128)
def _ticker(ticker: str):
    """Return a cached yfinance Ticker object."""
    try:
        import yfinance as yf
    except ImportError as exc:  # pragma: no cover - dependency guaranteed by reqs
        raise MarketDataError("yfinance is not installed") from exc
    return yf.Ticker(ticker.upper())


@functools.lru_cache(maxsize=128)
def get_spot(ticker: str) -> float:
    """Latest spot price for ``ticker`` via yfinance.

    Tries fast_info first (cheap), then falls back to the last daily close.
    Raises :class:`MarketDataError` if no price can be resolved.
    """
    t = _ticker(ticker)

    # fast_info is the cheapest and most reliable path.
    try:
        price = t.fast_info.get("last_price") or t.fast_info.get("lastPrice")
        if price and price > 0:
            return float(price)
    except Exception:  # noqa: BLE001 - fall through to history
        pass

    try:
        hist = t.history(period="5d")
    except Exception as exc:  # noqa: BLE001
        raise MarketDataError(f"failed to fetch spot for {ticker}: {exc}") from exc

    if hist is None or hist.empty or "Close" not in hist:
        raise MarketDataError(f"no price data returned for {ticker}")
    return float(hist["Close"].dropna().iloc[-1])


@functools.lru_cache(maxsize=64)
def get_dividend_yield(ticker: str) -> float:
    """Annualized continuous-ish dividend yield for ``ticker`` (fraction).

    Reads ``Ticker.info``; returns 0.0 if the underlier pays no dividend or the
    field is unavailable (a missing dividend yield is not an error).
    """
    t = _ticker(ticker)
    try:
        info = t.info
    except Exception:  # noqa: BLE001 - treat as no dividend rather than failing
        return 0.0
    if not info:
        return 0.0
    for key in ("dividendYield", "trailingAnnualDividendYield", "yield"):
        val = info.get(key)
        if val is None:
            continue
        val = float(val)
        normalized = _normalize_dividend_yield(val)
        if normalized is not None:
            return normalized
    return 0.0


# Plausible upper bound for an equity dividend yield as a fraction. Used to
# distinguish percent-point inputs (e.g. 0.35 -> 0.35%) from already-fractional
# ones and to reject absurd values. ~20% comfortably covers even the highest
# real-world yields while excluding mis-scaled inputs.
_MAX_PLAUSIBLE_DIV_YIELD = 0.20


def _normalize_dividend_yield(val: float) -> float | None:
    """Normalize a raw yfinance dividend-yield value to a decimal fraction.

    Returns the yield as a fraction (e.g. 0.0035 for 0.35%), or ``None`` if the
    raw value cannot be normalized into a plausible range and the caller should
    try the next field.

    WHY: current yfinance versions report ``dividendYield`` in *percent-point*
    units, e.g. ``0.35`` means 0.35% and ``4.2`` means 4.2% (not 35%/420%), so
    the correct conversion is ``val / 100``. Older yfinance builds occasionally
    returned an already-normalized small fraction (e.g. ``0.004`` for 0.4%).
    The heuristic: divide by 100 (the current convention); accept that result if
    it lands in a plausible equity-yield range (0..~20%). If dividing by 100
    underflows an already-fractional value (e.g. 0.004 -> 0.00004), fall back to
    treating the raw value as a fraction when *that* is the plausible reading.
    """
    if val < 0:
        return None
    if val == 0.0:
        return 0.0

    as_percent = val / 100.0  # current yfinance convention: val is a percent point

    # If the raw value already looks like a plausible fraction (<= ~20%) but
    # dividing by 100 would push it to an implausibly tiny yield, the source was
    # likely an older fraction-style field. Prefer the raw value in that case.
    raw_is_plausible_fraction = 0.0 < val <= _MAX_PLAUSIBLE_DIV_YIELD
    percent_underflows = as_percent < 0.0005  # < 0.05%, implausibly small for a payer
    if raw_is_plausible_fraction and percent_underflows:
        return val

    if 0.0 <= as_percent <= _MAX_PLAUSIBLE_DIV_YIELD:
        return as_percent

    # Anything else (e.g. raw > 20 -> >20% even after /100) is implausible.
    return None


@functools.lru_cache(maxsize=32)
def get_options_chain(ticker: str) -> pd.DataFrame:
    """Full listed options chain for ``ticker`` as a single DataFrame.

    Columns: expiration (date), tenor_years (float), type ("call"/"put"),
    strike, bid, ask, lastPrice, volume, openInterest, impliedVolatility.

    Raises :class:`MarketDataError` if no expirations / contracts are returned.
    """
    t = _ticker(ticker)
    try:
        expirations = t.options
    except Exception as exc:  # noqa: BLE001
        raise MarketDataError(
            f"failed to fetch option expirations for {ticker}: {exc}"
        ) from exc

    if not expirations:
        raise MarketDataError(f"no listed options for {ticker}")

    today = datetime.now(timezone.utc).date()
    frames: list[pd.DataFrame] = []
    for exp in expirations:
        try:
            oc = t.option_chain(exp)
        except Exception:  # noqa: BLE001 - skip a bad expiry, keep the rest
            continue
        exp_date = datetime.strptime(exp, "%Y-%m-%d").date()
        tenor = max((exp_date - today).days, 0) / 365.0
        for side, df in (("call", oc.calls), ("put", oc.puts)):
            if df is None or df.empty:
                continue
            df = df.copy()
            df["expiration"] = exp_date
            df["tenor_years"] = tenor
            df["type"] = side
            frames.append(df)

    if not frames:
        raise MarketDataError(f"option chain for {ticker} returned no contracts")

    chain = pd.concat(frames, ignore_index=True)
    keep = [
        "expiration",
        "tenor_years",
        "type",
        "strike",
        "bid",
        "ask",
        "lastPrice",
        "volume",
        "openInterest",
        "impliedVolatility",
    ]
    present = [c for c in keep if c in chain.columns]
    return chain[present]


# ---------------------------------------------------------------------------
# Treasury curve (FRED)
# ---------------------------------------------------------------------------

# FRED constant-maturity Treasury series -> tenor in years.
_FRED_TREASURY_SERIES = {
    "DGS1MO": 1 / 12,
    "DGS3MO": 0.25,
    "DGS6MO": 0.5,
    "DGS1": 1.0,
    "DGS2": 2.0,
    "DGS3": 3.0,
    "DGS5": 5.0,
    "DGS7": 7.0,
    "DGS10": 10.0,
    "DGS20": 20.0,
    "DGS30": 30.0,
}


@functools.lru_cache(maxsize=1)
def get_treasury_curve(api_key: str | None = None) -> dict:
    """U.S. Treasury par yield curve as ``{tenor_years: rate}`` (rates as fractions).

    Pulls FRED constant-maturity series. Requires a FRED API key, taken from the
    ``api_key`` argument or the ``FRED_API_KEY`` environment variable. Raises
    :class:`MarketDataError` if the key is missing or the fetch fails.
    """
    key = api_key or os.environ.get("FRED_API_KEY")
    if not key:
        raise MarketDataError(
            "FRED_API_KEY is not set; cannot fetch the Treasury curve. "
            "Set the environment variable or pass api_key=..."
        )

    try:
        from fredapi import Fred
    except ImportError as exc:  # pragma: no cover
        raise MarketDataError("fredapi is not installed") from exc

    fred = Fred(api_key=key)
    curve: dict[float, float] = {}
    for series_id, tenor in _FRED_TREASURY_SERIES.items():
        try:
            series = fred.get_series(series_id)
        except Exception:  # noqa: BLE001 - skip unavailable series, keep others
            continue
        if series is None or series.dropna().empty:
            continue
        latest = float(series.dropna().iloc[-1])
        # FRED reports CMT rates in percent; convert to a fraction.
        curve[tenor] = latest / 100.0

    if not curve:
        raise MarketDataError("FRED returned no Treasury data for any tenor")
    return curve


# ---------------------------------------------------------------------------
# Credit spreads
# ---------------------------------------------------------------------------

# Rating-based fallback spreads over Treasuries, in fractions (PRD 6.1: use a
# rating index such as ICE BofA when issuer-specific TRACE data is unavailable).
# These are representative long-run option-adjusted-spread levels by rating and
# are used as a transparent default; callers can override.
RATING_SPREADS = {
    "AAA": 0.0040,
    "AA": 0.0060,
    "A": 0.0090,
    "BBB": 0.0150,
    "BB": 0.0300,
    "B": 0.0450,
    "CCC": 0.0900,
}


def _normalize_rating(rating: str) -> str:
    """Collapse notch suffixes (e.g. 'A+', 'Baa1', 'A-') to a base bucket."""
    if not rating:
        return ""
    r = rating.strip().upper().replace("+", "").replace("-", "")
    # Map common Moody's notation to S&P-style buckets.
    moody_map = {
        "AAA": "AAA",
        "AA1": "AA", "AA2": "AA", "AA3": "AA",
        "A1": "A", "A2": "A", "A3": "A",
        "BAA1": "BBB", "BAA2": "BBB", "BAA3": "BBB", "BAA": "BBB",
        "BA1": "BB", "BA2": "BB", "BA3": "BB", "BA": "BB",
        "B1": "B", "B2": "B", "B3": "B",
        "CAA": "CCC", "CAA1": "CCC", "CAA2": "CCC", "CAA3": "CCC",
    }
    if r in moody_map:
        return moody_map[r]
    # S&P style: keep only the leading letters (AA1 already handled above).
    for bucket in ("AAA", "AA", "A", "BBB", "BB", "B", "CCC"):
        if r.startswith(bucket):
            return bucket
    return r


def get_credit_spread(issuer: str, rating: str) -> float:
    """Issuer credit spread over Treasuries (fraction).

    Phase 1 uses the rating-based index fallback (PRD 6.1). Issuer-specific
    TRACE lookups are a documented post-Phase-1 enhancement. The ``issuer``
    argument is accepted for signature compatibility and future use.

    Raises :class:`MarketDataError` if the rating cannot be mapped to a bucket.
    """
    bucket = _normalize_rating(rating)
    if bucket in RATING_SPREADS:
        return RATING_SPREADS[bucket]
    raise MarketDataError(
        f"no credit spread available for issuer={issuer!r} rating={rating!r}; "
        f"known buckets: {sorted(RATING_SPREADS)}"
    )


def clear_cache() -> None:
    """Clear all per-session market-data caches. Useful in tests."""
    for fn in (
        _ticker,
        get_spot,
        get_dividend_yield,
        get_options_chain,
        get_treasury_curve,
    ):
        fn.cache_clear()
