"""D2 regression — dividend-yield normalization (network-free, mocked yfinance).

Verifies the fix to ``market_data.get_dividend_yield`` documented in
BACKEND_NOTES.md §5 / §8 (Session 2):

current yfinance reports ``Ticker.info["dividendYield"]`` in *percent-point*
units (e.g. ``0.35`` means 0.35%, ``4.2`` means 4.2% — NOT 35%/420%), so the
fetcher must divide by 100 and return a decimal *fraction*. Absurd values
(>20% after scaling) and missing fields fall back to the documented default 0.0.

These tests mock at the boundary used inside ``market_data`` — the cached
``_ticker`` accessor — so no network is touched. The ``get_dividend_yield``
lru_cache is cleared between cases (per BACKEND_NOTES.md §4: caches are
``functools.lru_cache``; ``market_data.clear_cache()`` resets them).
"""
from __future__ import annotations

import pytest

from prism import market_data
from prism.market_data import get_dividend_yield


class _FakeTicker:
    """Minimal stand-in for a yfinance Ticker exposing only ``.info``."""

    def __init__(self, info):
        self._info = info

    @property
    def info(self):
        return self._info


def _patch_ticker(monkeypatch, info):
    """Patch the internal ``_ticker`` accessor to return a fake with ``info``.

    Also clears all market-data caches so the previous case's memoized
    dividend yield does not leak into this one.
    """
    market_data.clear_cache()
    monkeypatch.setattr(market_data, "_ticker", lambda ticker: _FakeTicker(info))


@pytest.fixture(autouse=True)
def _isolate_cache():
    """Ensure a clean cache before and after every case in this module."""
    market_data.clear_cache()
    yield
    market_data.clear_cache()


# ---------------------------------------------------------------------------
# Core D2 assertion: percent-point -> decimal fraction.
# ---------------------------------------------------------------------------

def test_div_yield_percent_point_035_to_decimal(monkeypatch):
    """0.35 (= 0.35%) must normalize to ~0.0035, NOT 0.35 (35%)."""
    _patch_ticker(monkeypatch, {"dividendYield": 0.35})
    assert get_dividend_yield("AAPL") == pytest.approx(0.0035, abs=1e-9)


def test_div_yield_percent_point_42_to_decimal(monkeypatch):
    """4.2 (= 4.2%) must normalize to ~0.042."""
    _patch_ticker(monkeypatch, {"dividendYield": 4.2})
    assert get_dividend_yield("XYZ") == pytest.approx(0.042, abs=1e-9)


def test_div_yield_missing_field_defaults_to_zero(monkeypatch):
    """Missing dividendYield field -> documented default 0.0 (not an error)."""
    _patch_ticker(monkeypatch, {"someOtherField": 123})
    assert get_dividend_yield("NODIV") == 0.0


def test_div_yield_none_value_defaults_to_zero(monkeypatch):
    """Explicit None dividendYield -> documented default 0.0."""
    _patch_ticker(monkeypatch, {"dividendYield": None})
    assert get_dividend_yield("NONEDIV") == 0.0


def test_div_yield_empty_info_defaults_to_zero(monkeypatch):
    """Empty / falsy info dict -> 0.0, no crash."""
    _patch_ticker(monkeypatch, {})
    assert get_dividend_yield("EMPTY") == 0.0


def test_div_yield_absurd_value_does_not_return_above_20pct(monkeypatch):
    """An absurd raw value (25.0) must NOT yield a >20% dividend yield.

    25.0/100 = 0.25 (25%) exceeds the plausibility bound, so the dividendYield
    field is rejected and the fetcher falls back to the documented default 0.0
    (no other plausible field present).
    """
    _patch_ticker(monkeypatch, {"dividendYield": 25.0})
    result = get_dividend_yield("ABSURD")
    assert result <= market_data._MAX_PLAUSIBLE_DIV_YIELD
    assert result == 0.0


def test_div_yield_absurd_value_falls_back_to_plausible_field(monkeypatch):
    """If dividendYield is absurd but a plausible alternate field exists, use it."""
    # 25.0 rejected; trailingAnnualDividendYield 0.5 (= 0.5%) accepted -> 0.005.
    _patch_ticker(
        monkeypatch,
        {"dividendYield": 25.0, "trailingAnnualDividendYield": 0.5},
    )
    assert get_dividend_yield("FALLBACK") == pytest.approx(0.005, abs=1e-9)


def test_div_yield_negative_defaults_to_zero(monkeypatch):
    """Negative dividend yield is implausible -> default 0.0."""
    _patch_ticker(monkeypatch, {"dividendYield": -1.0})
    assert get_dividend_yield("NEG") == 0.0


def test_div_yield_already_fractional_small_value(monkeypatch):
    """An older-style already-fractional value (0.004 = 0.4%) is preserved.

    Dividing 0.004 by 100 underflows to 0.00004 (< 0.05%), so the raw value is
    taken as-is per the documented heuristic (BACKEND_NOTES.md §5).
    """
    _patch_ticker(monkeypatch, {"dividendYield": 0.004})
    assert get_dividend_yield("OLDSTYLE") == pytest.approx(0.004, abs=1e-9)


# ---------------------------------------------------------------------------
# Unit-level checks on the normalizer helper (no ticker needed).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        (0.35, 0.0035),
        (4.2, 0.042),
        (0.5, 0.005),
        (0.0, 0.0),
        (20.0, 0.20),   # exactly at the plausibility bound -> 20%
    ],
)
def test_normalize_helper_plausible(raw, expected):
    assert market_data._normalize_dividend_yield(raw) == pytest.approx(expected, abs=1e-9)


@pytest.mark.parametrize("raw", [25.0, 2000.0, -1.0])
def test_normalize_helper_rejects_implausible(raw):
    assert market_data._normalize_dividend_yield(raw) is None


# ---------------------------------------------------------------------------
# utcnow DeprecationWarning regression (best-effort).
# A FRESH import of prism.market_data under -W error::DeprecationWarning must not
# raise (the datetime.utcnow() -> datetime.now(timezone.utc) fix). Run in a
# subprocess so the strict warning filter and any fresh import cannot pollute
# the module identity (e.g. MarketDataError) seen by the rest of the suite.
# ---------------------------------------------------------------------------

def test_no_deprecation_warning_on_fresh_import():
    import subprocess
    import sys

    code = (
        "import warnings\n"
        "warnings.simplefilter('error', DeprecationWarning)\n"
        "import prism.market_data as m\n"
        "print('OK')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-W", "error::DeprecationWarning", "-c", code],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        f"fresh import raised under -W error::DeprecationWarning:\n{proc.stderr}"
    )
    assert "OK" in proc.stdout
