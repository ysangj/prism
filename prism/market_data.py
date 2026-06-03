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
is unset, :func:`get_treasury_curve` returns a documented *static fallback*
Treasury curve (:func:`static_treasury_curve`) instead of raising — callers
should treat that result as LOW CONFIDENCE. When a key *is* supplied but the
fetch genuinely fails (bad key, outage, empty result), :class:`MarketDataError`
is raised so the problem is surfaced rather than masked. yfinance requires no
API key.

Transport (PRISM-RCA-003)
-------------------------
Live FRED data is fetched directly from the FRED REST API with :mod:`requests`,
which verifies TLS against the bundled :mod:`certifi` CA store. This sidesteps
the macOS python.org cert gap that broke the old ``fredapi``/``urllib`` path
(``ssl.SSLCertVerificationError`` because the framework build ships no trust
store). On a genuine all-tenor failure, :func:`_fetch_fred_curve` classifies the
underlying cause (SSL/cert, auth/key, network) into an actionable
:class:`MarketDataError` and chains the original exception. The FRED API key is
never included in any error message or log (the REST URL carries ``api_key=`` as
a query param, so we never echo the URL).
"""

from __future__ import annotations

import functools
import os
import ssl
from datetime import datetime, timezone

import pandas as pd

__all__ = [
    "MarketDataError",
    "get_spot",
    "get_options_chain",
    "get_dividend_yield",
    "get_treasury_curve",
    "static_treasury_curve",
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

# Static fallback Treasury par yield curve, used when no FRED key is available
# (RCA PRISM-RCA-002 §6.A). Rates are fractions and cover the same tenors as
# ``_FRED_TREASURY_SERIES``. Results derived from this curve are LOW CONFIDENCE.
#
# Source: U.S. Treasury Daily Par Yield Curve Rates (CMT), as-of 2026-05-29.
#   https://home.treasury.gov/.../daily-treasury-rates
# This is a representative, recent par curve; refresh it periodically. It exists
# only so live pricing degrades gracefully instead of hard-failing without a key.
STATIC_CURVE_AS_OF = "2026-05-29"
_STATIC_TREASURY_CURVE = {
    1 / 12: 0.0432,   # 1-month
    0.25: 0.0428,     # 3-month
    0.5: 0.0421,      # 6-month
    1.0: 0.0405,      # 1-year
    2.0: 0.0388,      # 2-year
    3.0: 0.0385,      # 3-year
    5.0: 0.0392,      # 5-year
    7.0: 0.0408,      # 7-year
    10.0: 0.0425,     # 10-year
    20.0: 0.0462,     # 20-year
    30.0: 0.0451,     # 30-year
}


def static_treasury_curve() -> dict:
    """Return a fresh copy of the documented static fallback Treasury curve.

    ``{tenor_years: rate}`` with rates as fractions. Used when no FRED API key is
    available; callers must treat derived results as LOW CONFIDENCE. See
    :data:`STATIC_CURVE_AS_OF` for the as-of date.
    """
    return dict(_STATIC_TREASURY_CURVE)


# FRED REST endpoint for the latest observation of a series. We hit this directly
# with ``requests`` (certifi-backed TLS) instead of fredapi/urllib so the macOS
# python.org cert gap (PRISM-RCA-003) never bites.
_FRED_OBSERVATIONS_URL = "https://api.stlouisfed.org/fred/series/observations"
_FRED_TIMEOUT_SECONDS = 15


def _looks_like_ssl_error(exc: BaseException) -> bool:
    """True if ``exc`` (or its cause chain) is a TLS/cert-verification failure."""
    cursor: BaseException | None = exc
    while cursor is not None:
        if isinstance(cursor, ssl.SSLError):
            return True
        text = str(cursor)
        if "CERTIFICATE_VERIFY_FAILED" in text or "unable to get local issuer certificate" in text:
            return True
        cursor = cursor.__cause__ or cursor.__context__
    return False


def _classify_fred_failure(exc: BaseException) -> str:
    """Map a per-series fetch exception to an actionable, key-safe message.

    Called only when *every* tenor failed (a systemic problem), so the message
    names the real root cause instead of the old "no data for any tenor".
    The FRED API key is never interpolated into the returned string.
    """
    import requests  # local import; classifier only runs on the failure path

    # SSL / certificate trust (the PRISM-RCA-003 macOS case).
    if _looks_like_ssl_error(exc):
        return (
            "FRED fetch failed: SSL certificate verification error "
            "(could not verify the FRED server certificate). "
            "Fix: run the python.org 'Install Certificates.command', or set "
            "SSL_CERT_FILE to your certifi bundle "
            "(python -c 'import certifi; print(certifi.where())')."
        )

    # Auth / key rejection: an HTTP 400/403 from the REST API, which FRED returns
    # for a bad or unregistered api_key.
    status = None
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        status = exc.response.status_code
    if status in (400, 401, 403):
        return "FRED API key appears invalid or unregistered."

    # Network / connectivity (DNS, refused, timeout, generic transport error).
    if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        return "Could not reach FRED (network)."

    # Fallback: name the actual exception type + message so the cause is never
    # lost. The message text here is series-agnostic and carries no key.
    return f"FRED fetch failed for all tenors: {type(exc).__name__}: {exc}"


def _fetch_one_series(key: str, series_id: str):
    """Fetch the latest non-missing observation value for a single FRED series.

    Returns the value as a float (still in FRED's PERCENT units) or ``None`` if
    the series has no usable observation. Raises ``requests``/SSL exceptions on a
    transport/HTTP failure so the caller can classify them. The key travels only
    as a request parameter; it is never placed in any raised message.
    """
    import requests
    import certifi

    params = {
        "series_id": series_id,
        "api_key": key,
        "file_type": "json",
        "sort_order": "desc",
        "limit": 1,
    }
    resp = requests.get(
        _FRED_OBSERVATIONS_URL,
        params=params,
        timeout=_FRED_TIMEOUT_SECONDS,
        verify=certifi.where(),
    )
    # raise_for_status surfaces 4xx/5xx (e.g. a rejected key) as requests.HTTPError
    # WITHOUT including the request URL/key in the str() we propagate downstream.
    if resp.status_code >= 400:
        raise requests.HTTPError(f"HTTP {resp.status_code}", response=resp)

    observations = resp.json().get("observations") or []
    for obs in observations:
        value = obs.get("value")
        # FRED encodes missing observations as ".".
        if value in (None, ".", ""):
            continue
        return float(value)
    return None


def _fetch_fred_curve(key: str) -> dict:
    """Fetch the live Treasury curve from FRED. Raises on a genuine failure.

    Separated from the cache wrapper so the no-key static path and the keyed live
    path never share an ``lru_cache`` slot.

    Individual unavailable tenors are skipped (a couple of missing series must not
    kill the fetch). But per-series exceptions are *tracked*: if **zero** tenors
    succeed, the failure is systemic, so we classify the underlying cause
    (SSL/cert, auth/key, network, or other) into an actionable
    :class:`MarketDataError` and chain the original exception — never collapsing it
    into a generic "no data for any tenor". The FRED API key is never echoed in
    any message (PRISM-RCA-003 §A).
    """
    try:
        import requests  # noqa: F401 - import here so a missing dep is a clear error
        import certifi  # noqa: F401
    except ImportError as exc:  # pragma: no cover - guaranteed by requirements
        raise MarketDataError(
            "requests/certifi are not installed; cannot fetch live FRED data"
        ) from exc

    curve: dict[float, float] = {}
    last_exc: BaseException | None = None
    for series_id, tenor in _FRED_TREASURY_SERIES.items():
        try:
            latest = _fetch_one_series(key, series_id)
        except Exception as exc:  # noqa: BLE001 - track, then classify if ALL fail
            last_exc = exc
            continue
        if latest is None:
            continue
        # FRED reports CMT rates in percent; convert to a fraction.
        curve[tenor] = latest / 100.0

    if not curve:
        if last_exc is not None:
            raise MarketDataError(_classify_fred_failure(last_exc)) from last_exc
        # No exception, just genuinely empty data for every tenor.
        raise MarketDataError("FRED returned no Treasury data for any tenor")
    return curve


@functools.lru_cache(maxsize=8)
def get_treasury_curve(api_key: str | None = None) -> dict:
    """U.S. Treasury par yield curve as ``{tenor_years: rate}`` (rates as fractions).

    Behavior (RCA PRISM-RCA-002 §6.A):

    * **No key** — neither ``api_key`` nor ``FRED_API_KEY`` is set — returns the
      documented :func:`static_treasury_curve` fallback and does **not** raise.
      Callers should flag derived results LOW CONFIDENCE.
    * **Key supplied** (argument or env) — fetches the live FRED curve and raises
      :class:`MarketDataError` on a genuine fetch failure or empty result, so a
      bad key / outage is surfaced rather than silently masked.

    The cache keys on ``api_key`` (with a maxsize > 1) so the static no-key path
    and any keyed live path occupy distinct slots and never collide.
    """
    key = api_key or os.environ.get("FRED_API_KEY")
    if not key:
        # Documented graceful fallback: a fresh static curve copy.
        return static_treasury_curve()
    return _fetch_fred_curve(key)


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
