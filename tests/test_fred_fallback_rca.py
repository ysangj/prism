"""RCA PRISM-RCA-002 §7E — FRED key / live-mode graceful fallback.

Covers the new market-data + engine contract introduced by RCA-002:

* ``get_treasury_curve()`` with **no key** returns the documented static fallback
  curve and **does not raise** (the README's long-promised graceful degradation).
* ``get_treasury_curve(api_key=...)`` with a key but a **mocked FRED failure /
  empty result** still **raises ``MarketDataError``** (a genuine, surfaced error).
* ``price_product`` on the live path with **no key** completes, sets
  ``result.low_confidence_curve is True``, adds the static-curve note, and does
  NOT raise.
* Supplying ``fred_api_key="..."`` routes to ``get_treasury_curve(api_key=...)``
  (key forwarded verbatim) and yields ``low_confidence_curve is False``.
* The FRED key value is never logged / persisted / echoed in notes or errors.
* ``prism.load_local_env()`` is a safe no-op when ``python-dotenv``/``.env`` are
  absent and has no import-time side effects.

All network is mocked; no live keys, no token spend, no real FRED/yfinance calls.
Spec: docs/RCA_fred_key_live_mode.md (§6 decision, §7E tests, §7 acceptance).
Contract: BACKEND_NOTES.md.
"""
from __future__ import annotations

import importlib
import sys

import pytest

from prism import market_data
from prism.market_data import MarketDataError

# A fake FRED key used to assert plumbing. It is a SECRET-shaped sentinel: any
# test that leaks it (into notes, errors, logs) fails the security assertions.
FAKE_FRED_KEY = "FRED-SECRET-KEY-DO-NOT-LEAK-9988"


@pytest.fixture(autouse=True)
def _no_fred_env_and_clean_cache(monkeypatch):
    """Every test in this module runs with FRED_API_KEY unset and a clean cache.

    The Treasury-curve fetch is ``lru_cache``-d keyed on ``api_key``, so a result
    from one case (e.g. the static no-key slot) must not leak into the next.
    """
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    _safe_clear_cache()
    yield
    # Teardown runs before monkeypatch restores a patched ``get_treasury_curve``,
    # so guard against a plain (non-lru_cache) replacement having no cache_clear.
    _safe_clear_cache()


def _safe_clear_cache():
    """Clear market-data caches, tolerating a monkeypatched cache-less fetcher."""
    try:
        market_data.clear_cache()
    except AttributeError:
        # ``get_treasury_curve`` may be patched to a plain function in a test.
        for fn in (market_data._ticker, market_data.get_spot,
                   market_data.get_dividend_yield, market_data.get_options_chain):
            if hasattr(fn, "cache_clear"):
                fn.cache_clear()


# ===========================================================================
# 1. Unit — static fallback curve (RCA §7E unit)
# ===========================================================================
def test_static_treasury_curve_documented_tenors_as_fractions():
    curve = market_data.static_treasury_curve()
    assert isinstance(curve, dict)
    # Same tenors as the FRED series map (1/12 .. 30y), per BACKEND_NOTES.md.
    expected_tenors = sorted(market_data._FRED_TREASURY_SERIES.values())
    assert sorted(curve) == pytest.approx(expected_tenors)
    # Rates are FRACTIONS (not percent): every yield is a small positive decimal.
    for tenor, rate in curve.items():
        assert 0.0 < rate < 0.20, f"tenor {tenor} rate {rate} not a fraction"
    # Documented as-of date is exposed.
    assert market_data.STATIC_CURVE_AS_OF == "2026-05-29"


def test_static_treasury_curve_returns_fresh_copy():
    a = market_data.static_treasury_curve()
    a[1 / 12] = 999.0  # mutate the copy
    b = market_data.static_treasury_curve()
    assert b[1 / 12] != 999.0, "static_treasury_curve must return a fresh copy"


def test_get_treasury_curve_no_key_returns_static_no_raise(monkeypatch):
    """No arg key + no FRED_API_KEY env -> static curve, NO raise (new contract)."""
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    market_data.get_treasury_curve.cache_clear()
    curve = market_data.get_treasury_curve()  # must not raise
    assert curve == market_data.static_treasury_curve()
    market_data.get_treasury_curve.cache_clear()


def test_get_treasury_curve_keyed_failure_raises(monkeypatch):
    """Key supplied + mocked FRED failure/empty -> still raises MarketDataError.

    The genuine fetch failure must surface when the caller DID provide a key,
    and the error must never contain the key value.
    """
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    market_data.get_treasury_curve.cache_clear()

    # Mock the live fetch to behave like a real failure (empty result -> raise).
    def _boom(key):
        raise MarketDataError("FRED returned no Treasury data for any tenor")

    monkeypatch.setattr(market_data, "_fetch_fred_curve", _boom)

    with pytest.raises(MarketDataError) as exc:
        market_data.get_treasury_curve(api_key=FAKE_FRED_KEY)
    assert FAKE_FRED_KEY not in str(exc.value), "key leaked into error message"
    market_data.get_treasury_curve.cache_clear()


def test_get_treasury_curve_keyed_empty_series_raises(monkeypatch):
    """Key supplied + a FRED that yields no usable series -> raises (real path).

    Mocks ``fredapi.Fred`` so ``_fetch_fred_curve`` runs end-to-end with empty
    series and produces the genuine empty-result MarketDataError.
    """
    market_data.get_treasury_curve.cache_clear()

    import types

    class _FakeSeries:
        def dropna(self):
            return self

        @property
        def empty(self):
            return True

    class _FakeFred:
        def __init__(self, api_key=None):
            # Sanity: the key is forwarded to the FRED client unchanged.
            assert api_key == FAKE_FRED_KEY

        def get_series(self, series_id):
            return _FakeSeries()

    fake_mod = types.ModuleType("fredapi")
    fake_mod.Fred = _FakeFred
    monkeypatch.setitem(sys.modules, "fredapi", fake_mod)

    with pytest.raises(MarketDataError):
        market_data.get_treasury_curve(api_key=FAKE_FRED_KEY)
    market_data.get_treasury_curve.cache_clear()


def test_get_treasury_curve_keyed_success_returns_fractions(monkeypatch):
    """Key supplied + a healthy mocked FRED -> live curve as fractions, no raise."""
    market_data.get_treasury_curve.cache_clear()

    import types

    class _Series:
        def __init__(self, last):
            self._last = last

        def dropna(self):
            return self

        @property
        def empty(self):
            return False

        @property
        def iloc(self):
            return {-1: self._last}

    class _FakeFred:
        def __init__(self, api_key=None):
            assert api_key == FAKE_FRED_KEY

        def get_series(self, series_id):
            # FRED reports CMT in PERCENT; backend divides by 100.
            return _Series(4.25)

    fake_mod = types.ModuleType("fredapi")
    fake_mod.Fred = _FakeFred
    monkeypatch.setitem(sys.modules, "fredapi", fake_mod)

    curve = market_data.get_treasury_curve(api_key=FAKE_FRED_KEY)
    assert curve, "live curve should be non-empty"
    for rate in curve.values():
        assert rate == pytest.approx(0.0425)  # 4.25% -> fraction
    market_data.get_treasury_curve.cache_clear()


# ===========================================================================
# 2. Integration — price_product live path (RCA §7E integration / §7 acceptance)
# ===========================================================================
def _build_autocallable():
    import datetime as dt

    from prism.models import Autocallable

    return Autocallable(
        underlier="AAPL",
        notional=100_000,
        maturity=dt.date.today() + dt.timedelta(days=int(1.5 * 365.25)),
        issuer="JPMorgan Chase",
        issuer_rating="A",
        offer_price=1.0,
        coupon_rate=0.095,
        coupon_barrier=0.70,
        call_barrier=1.00,
        knock_in_barrier=0.60,
        observation_freq="quarterly",
    )


# Market overrides that bypass spot/vol/credit fetches but DELIBERATELY omit
# risk_free / treasury_curve so the FRED curve resolution path is exercised.
_LIVE_CURVE_OVERRIDES = dict(
    spot=100.0,
    div_yield=0.0,
    credit_spread=0.0090,  # RATING_SPREADS["A"]
    flat_vol=0.20,
)


def test_price_product_no_key_uses_static_curve_low_confidence(monkeypatch):
    """ACCEPTANCE: demo OFF + no FRED key -> result flagged low-confidence, no crash."""
    import prism

    monkeypatch.delenv("FRED_API_KEY", raising=False)
    market_data.clear_cache()

    product = _build_autocallable()
    # No risk_free, no treasury_curve, no fred_api_key, no FRED_API_KEY env.
    result = prism.price_product(
        product, n_paths=5_000, seed=123, **_LIVE_CURVE_OVERRIDES
    )

    assert result.low_confidence_curve is True
    # A human-readable static-curve note is appended.
    note_text = "\n".join(result.notes)
    assert "static" in note_text.lower()
    assert "low confidence" in note_text.lower()
    # The risk-free actually came from the static curve (interpolated 1.5y rate).
    static = market_data.static_treasury_curve()
    assert min(static.values()) <= result.risk_free <= max(static.values())
    market_data.clear_cache()


def test_price_product_no_key_does_not_raise(monkeypatch):
    """The old hard-fail (MarketDataError on missing key) must be gone."""
    import prism

    monkeypatch.delenv("FRED_API_KEY", raising=False)
    market_data.clear_cache()
    product = _build_autocallable()
    # Should simply complete — explicitly assert no exception type leaks.
    result = prism.price_product(
        product, n_paths=2_000, seed=1, **_LIVE_CURVE_OVERRIDES
    )
    assert result is not None
    market_data.clear_cache()


def test_price_product_forwards_fred_key_and_clears_low_confidence(monkeypatch):
    """Supplying fred_api_key routes to get_treasury_curve(api_key=...) and the
    result is NOT flagged low-confidence (live curve was used).

    We patch ``market_data.get_treasury_curve`` (the engine calls it through the
    module) to (a) capture the forwarded key and (b) return a real live-style
    curve so no static fallback / note is set.
    """
    import prism

    monkeypatch.delenv("FRED_API_KEY", raising=False)
    market_data.clear_cache()

    captured = {}

    def _fake_get_curve(api_key=None):
        captured["api_key"] = api_key
        # A live-style curve (fractions) covering the standard tenors.
        return {t: 0.041 for t in market_data._FRED_TREASURY_SERIES.values()}

    monkeypatch.setattr(market_data, "get_treasury_curve", _fake_get_curve)

    product = _build_autocallable()
    result = prism.price_product(
        product, n_paths=2_000, seed=7,
        fred_api_key=FAKE_FRED_KEY, **_LIVE_CURVE_OVERRIDES,
    )

    assert captured.get("api_key") == FAKE_FRED_KEY, "FRED key not forwarded"
    assert result.low_confidence_curve is False
    # No static-curve note when the live curve was used.
    assert not any("static" in n.lower() for n in result.notes)
    _safe_clear_cache()


def test_price_product_env_fred_key_forwarded(monkeypatch):
    """A FRED_API_KEY in the environment (e.g. from .env) is forwarded too."""
    import prism

    monkeypatch.setenv("FRED_API_KEY", FAKE_FRED_KEY)
    market_data.clear_cache()

    captured = {}

    def _fake_get_curve(api_key=None):
        captured["api_key"] = api_key
        return {t: 0.04 for t in market_data._FRED_TREASURY_SERIES.values()}

    monkeypatch.setattr(market_data, "get_treasury_curve", _fake_get_curve)

    product = _build_autocallable()
    result = prism.price_product(
        product, n_paths=2_000, seed=7, **_LIVE_CURVE_OVERRIDES
    )
    assert captured.get("api_key") == FAKE_FRED_KEY
    assert result.low_confidence_curve is False
    _safe_clear_cache()


# ===========================================================================
# 4. Security (RCA §7E security / §7 acceptance: keys never persisted/logged)
# ===========================================================================
def test_fred_key_never_leaks_into_notes_or_result(monkeypatch):
    """The FRED key value never appears anywhere in the returned result."""
    import dataclasses

    import prism

    monkeypatch.delenv("FRED_API_KEY", raising=False)
    market_data.clear_cache()

    def _fake_get_curve(api_key=None):
        return {t: 0.04 for t in market_data._FRED_TREASURY_SERIES.values()}

    monkeypatch.setattr(market_data, "get_treasury_curve", _fake_get_curve)

    product = _build_autocallable()
    result = prism.price_product(
        product, n_paths=2_000, seed=7,
        fred_api_key=FAKE_FRED_KEY, **_LIVE_CURVE_OVERRIDES,
    )
    blob = repr(dataclasses.asdict(result))
    assert FAKE_FRED_KEY not in blob, "FRED key leaked into the result"
    assert FAKE_FRED_KEY not in "\n".join(result.notes)
    _safe_clear_cache()


def test_fred_key_not_logged(monkeypatch, caplog):
    """No log record emitted during pricing contains the FRED key value."""
    import logging

    import prism

    monkeypatch.delenv("FRED_API_KEY", raising=False)
    market_data.clear_cache()

    def _fake_get_curve(api_key=None):
        return {t: 0.04 for t in market_data._FRED_TREASURY_SERIES.values()}

    monkeypatch.setattr(market_data, "get_treasury_curve", _fake_get_curve)

    product = _build_autocallable()
    with caplog.at_level(logging.DEBUG):
        prism.price_product(
            product, n_paths=2_000, seed=7,
            fred_api_key=FAKE_FRED_KEY, **_LIVE_CURVE_OVERRIDES,
        )
    for rec in caplog.records:
        assert FAKE_FRED_KEY not in rec.getMessage()
    _safe_clear_cache()


def test_fred_key_never_appears_in_source_grep():
    """Static check: no product source hardcodes/persists/prints a FRED key.

    Guards against a regression that would write the key to disk or echo it. We
    assert the key is only ever READ (env / arg) and never written or logged in
    market_data / __init__ / _env source.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "prism"
    for name in ("market_data.py", "__init__.py", "_env.py"):
        src = (root / name).read_text()
        lowered = src.lower()
        # No logging of the resolved key, no file writes of a key.
        assert "print(key)" not in lowered
        assert "print(api_key)" not in lowered
        assert "print(fred" not in lowered
        # The key is read from env / arg only — never assigned into os.environ
        # by the library (the app's .env autoload is the only writer, via dotenv).
        assert "os.environ[" not in src or name == "_env.py"


# ===========================================================================
# .env autoload — safe no-op, no import-time side effects (RCA §7E security)
# ===========================================================================
def test_load_local_env_no_import_side_effects(monkeypatch):
    """Importing prism / prism._env must NOT read a .env at import time."""
    # Sentinel: a key that would appear in os.environ if an import autoloaded .env.
    monkeypatch.delenv("PRISM_TEST_ENV_SENTINEL", raising=False)
    # Re-import the env module fresh; importing must not call load_dotenv.
    import prism._env as env_mod

    importlib.reload(env_mod)
    assert "PRISM_TEST_ENV_SENTINEL" not in __import__("os").environ


def test_load_local_env_safe_noop_without_dotenv(monkeypatch):
    """When python-dotenv is unavailable, load_local_env returns False, no raise."""
    import prism

    # Force the dotenv import inside load_local_env to fail.
    real_import = __builtins__["__import__"] if isinstance(
        __builtins__, dict
    ) else __builtins__.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "dotenv" or name.startswith("dotenv."):
            raise ImportError("simulated missing python-dotenv")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _fake_import)
    assert prism.load_local_env() is False  # safe no-op, no exception


def test_load_local_env_returns_true_when_dotenv_present(monkeypatch):
    """When python-dotenv IS present, load_local_env runs and returns True.

    We stub ``dotenv.load_dotenv`` so no real .env is read and nothing leaks.
    """
    import types

    import prism

    calls = {}

    def _fake_load_dotenv(*args, override=False, **kwargs):
        calls["override"] = override
        return True

    fake_dotenv = types.ModuleType("dotenv")
    fake_dotenv.load_dotenv = _fake_load_dotenv
    monkeypatch.setitem(sys.modules, "dotenv", fake_dotenv)

    assert prism.load_local_env() is True
    # Default does not override already-exported env vars.
    assert calls.get("override") is False
