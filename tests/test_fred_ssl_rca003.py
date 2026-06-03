"""RCA PRISM-RCA-003 §7E — live FRED SSL/cert robustness + no-longer-swallowed errors.

The live FRED Treasury fetch was reimplemented over ``requests`` + ``certifi``
(``fredapi`` removed). The internal seam is now
:func:`prism.market_data._fetch_one_series` (per-series fetch);
``_fetch_fred_curve`` loops it and, when **every** tenor fails, classifies the real
underlying cause into an actionable, key-safe :class:`MarketDataError` that **chains**
the original exception (``raise ... from exc``) — instead of the old swallow-all
``"FRED returned no Treasury data for any tenor"``.

These tests are fully OFFLINE: they mock ``_fetch_one_series`` (or ``requests.get``)
to simulate each failure class with **zero network** and a recognizable dummy key
that must never appear in any raised message.

Acceptance mapping (docs/RCA_fred_ssl_cert.md §7):
- distinct, accurate messages for SSL/cert, invalid key, network outage;
- the SSL message names the cert cause AND the one-line remedy;
- partial-series outages still succeed (no regression of the legit skip);
- no-key static fallback unchanged (RCA-002 regression);
- no key material ever appears in a raised message.

Contract: BACKEND_NOTES.md (PRISM-RCA-003 error taxonomy).
"""
from __future__ import annotations

import logging
import ssl

import pytest
import requests

from prism import market_data
from prism.market_data import MarketDataError

# A recognizable SECRET-shaped key: any leak into a message/log fails the test.
DUMMY_KEY = "SECRET-KEY-DO-NOT-LEAK"

# The legacy/misleading message the new classification must NOT emit on a
# systemic (exception-driven) failure.
LEGACY_MSG = "no Treasury data for any tenor"


@pytest.fixture(autouse=True)
def _clean_env_and_cache(monkeypatch):
    """No FRED_API_KEY in env, clean lru_cache between cases."""
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    market_data.clear_cache()
    yield
    market_data.clear_cache()


def _patch_all_series(monkeypatch, fn):
    """Make every per-series fetch behave per ``fn(key, series_id)``."""
    monkeypatch.setattr(market_data, "_fetch_one_series", fn)


# ===========================================================================
# 1. SSL all-fail -> SSL/cert cause + remedy, chained, not the legacy message.
# ===========================================================================
def test_ssl_all_fail_names_cause_and_remedy_and_chains(monkeypatch):
    def _ssl_fail(key, series_id):
        raise ssl.SSLCertVerificationError(
            "[SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer certificate"
        )

    _patch_all_series(monkeypatch, _ssl_fail)

    with pytest.raises(MarketDataError) as exc_info:
        market_data.get_treasury_curve(api_key=DUMMY_KEY)

    msg = str(exc_info.value)
    low = msg.lower()
    # Names the SSL / certificate cause.
    assert "ssl" in low and ("cert" in low), f"SSL cause not named: {msg!r}"
    # Gives the actionable one-line remedy (Install Certificates / SSL_CERT_FILE / certifi).
    assert "install certificates" in low, f"remedy (installer) missing: {msg!r}"
    assert "ssl_cert_file" in low, f"remedy (SSL_CERT_FILE) missing: {msg!r}"
    assert "certifi" in low, f"remedy (certifi) missing: {msg!r}"
    # NOT the old swallow-all message.
    assert LEGACY_MSG not in msg, f"emitted legacy 'no data' message: {msg!r}"
    # Original exception is chained.
    assert exc_info.value.__cause__ is not None, "original exception not chained"
    assert isinstance(exc_info.value.__cause__, ssl.SSLError), (
        f"chained cause is not an SSLError: {exc_info.value.__cause__!r}"
    )
    # Key never leaks.
    assert DUMMY_KEY not in msg


def test_ssl_via_requests_sslerror_wrapping_cert_text(monkeypatch):
    """A requests.SSLError wrapping the CERTIFICATE_VERIFY_FAILED text is classified too."""
    def _req_ssl_fail(key, series_id):
        raise requests.exceptions.SSLError(
            "HTTPSConnectionPool: [SSL: CERTIFICATE_VERIFY_FAILED] "
            "unable to get local issuer certificate"
        )

    _patch_all_series(monkeypatch, _req_ssl_fail)

    with pytest.raises(MarketDataError) as exc_info:
        market_data.get_treasury_curve(api_key=DUMMY_KEY)

    low = str(exc_info.value).lower()
    assert "ssl" in low and "certifi" in low
    assert LEGACY_MSG not in str(exc_info.value)
    assert exc_info.value.__cause__ is not None
    assert DUMMY_KEY not in str(exc_info.value)


# ===========================================================================
# 2. Auth all-fail -> invalid/unregistered key.
# ===========================================================================
@pytest.mark.parametrize("status", [400, 401, 403])
def test_auth_all_fail_says_invalid_key(monkeypatch, status):
    def _auth_fail(key, series_id):
        resp = requests.Response()
        resp.status_code = status
        raise requests.HTTPError(f"HTTP {status}", response=resp)

    _patch_all_series(monkeypatch, _auth_fail)

    with pytest.raises(MarketDataError) as exc_info:
        market_data.get_treasury_curve(api_key=DUMMY_KEY)

    msg = str(exc_info.value)
    low = msg.lower()
    assert "invalid" in low or "unregistered" in low, (
        f"auth failure not described as invalid/unregistered key: {msg!r}"
    )
    assert "key" in low
    assert LEGACY_MSG not in msg
    assert exc_info.value.__cause__ is not None
    assert DUMMY_KEY not in msg


# ===========================================================================
# 3. Network all-fail -> could not reach FRED (network).
# ===========================================================================
@pytest.mark.parametrize(
    "exc_factory",
    [
        lambda: requests.ConnectionError("DNS lookup failed"),
        lambda: requests.Timeout("read timed out"),
    ],
)
def test_network_all_fail_says_could_not_reach(monkeypatch, exc_factory):
    def _net_fail(key, series_id):
        raise exc_factory()

    _patch_all_series(monkeypatch, _net_fail)

    with pytest.raises(MarketDataError) as exc_info:
        market_data.get_treasury_curve(api_key=DUMMY_KEY)

    msg = str(exc_info.value)
    assert "could not reach fred" in msg.lower(), (
        f"network failure not described as a reachability problem: {msg!r}"
    )
    assert LEGACY_MSG not in msg
    assert exc_info.value.__cause__ is not None
    assert DUMMY_KEY not in msg


# ===========================================================================
# 4. Partial success -> still returns a curve (legit per-series skip preserved).
# ===========================================================================
def test_partial_success_still_returns_curve(monkeypatch):
    """A subset of series fail but >=1 succeeds -> curve dict, no raise."""
    succeeded = "DGS10"  # the 10y tenor

    def _partial(key, series_id):
        if series_id == succeeded:
            return 4.25  # percent
        raise requests.ConnectionError("transient per-series outage")

    _patch_all_series(monkeypatch, _partial)

    curve = market_data.get_treasury_curve(api_key=DUMMY_KEY)
    assert isinstance(curve, dict) and curve, "partial fetch should return a curve"
    tenor_10y = market_data._FRED_TREASURY_SERIES[succeeded]
    assert curve[tenor_10y] == pytest.approx(0.0425), "10y rate not the surviving series"
    # Only the surviving tenor is present (others were legitimately skipped).
    assert set(curve) == {tenor_10y}


def test_partial_success_mixed_none_and_value(monkeypatch):
    """Mix of None (no observation) and a real value -> returns the value tenors."""
    def _mixed(key, series_id):
        if series_id in ("DGS2", "DGS10"):
            return 3.90
        return None  # no usable observation; legitimate skip

    _patch_all_series(monkeypatch, _mixed)

    curve = market_data.get_treasury_curve(api_key=DUMMY_KEY)
    assert set(curve) == {
        market_data._FRED_TREASURY_SERIES["DGS2"],
        market_data._FRED_TREASURY_SERIES["DGS10"],
    }
    for rate in curve.values():
        assert rate == pytest.approx(0.039)


# ===========================================================================
# 5. No-key static fallback unchanged (RCA-002 regression).
# ===========================================================================
def test_no_key_returns_static_curve_no_raise(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    market_data.get_treasury_curve.cache_clear()
    curve = market_data.get_treasury_curve()  # no key, no env -> must not raise
    assert curve == market_data.static_treasury_curve()


# ===========================================================================
# 6. Key safety across all failure classes -> key in NONE of the messages, nor logs.
# ===========================================================================
def test_key_never_appears_in_any_failure_message_or_log(monkeypatch, caplog):
    cases = {
        "ssl": lambda k, s: (_ for _ in ()).throw(
            ssl.SSLCertVerificationError(
                "[SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer certificate"
            )
        ),
        "auth": lambda k, s: (_ for _ in ()).throw(
            requests.HTTPError("HTTP 403", response=_resp(403))
        ),
        "network": lambda k, s: (_ for _ in ()).throw(
            requests.ConnectionError("boom")
        ),
        "other": lambda k, s: (_ for _ in ()).throw(ValueError("weird parse error")),
    }
    for name, fn in cases.items():
        market_data.clear_cache()
        monkeypatch.setattr(market_data, "_fetch_one_series", fn)
        with caplog.at_level(logging.DEBUG):
            with pytest.raises(MarketDataError) as exc_info:
                market_data.get_treasury_curve(api_key=DUMMY_KEY)
        assert DUMMY_KEY not in str(exc_info.value), f"{name}: key leaked into message"
        # Walk the whole chain too.
        cur = exc_info.value
        while cur is not None:
            assert DUMMY_KEY not in str(cur), f"{name}: key leaked into chained exc"
            cur = cur.__cause__
        for rec in caplog.records:
            assert DUMMY_KEY not in rec.getMessage(), f"{name}: key leaked into a log"
        caplog.clear()


def _resp(status):
    r = requests.Response()
    r.status_code = status
    return r


# ===========================================================================
# 7. "Other" exceptions still name the actual type+message (cause never lost).
# ===========================================================================
def test_other_failure_names_actual_exception(monkeypatch):
    def _weird(key, series_id):
        raise ValueError("could not parse observation JSON")

    _patch_all_series(monkeypatch, _weird)

    with pytest.raises(MarketDataError) as exc_info:
        market_data.get_treasury_curve(api_key=DUMMY_KEY)

    msg = str(exc_info.value)
    assert "ValueError" in msg, f"actual exception type not surfaced: {msg!r}"
    assert "could not parse observation JSON" in msg
    assert LEGACY_MSG not in msg
    assert isinstance(exc_info.value.__cause__, ValueError)
    assert DUMMY_KEY not in msg


# ===========================================================================
# 8. Genuinely-empty (no exception) keeps the legacy message — distinct from systemic.
# ===========================================================================
def test_genuinely_empty_keeps_legacy_message(monkeypatch):
    """All series return None with NO exception -> the legacy empty message (correct here)."""
    _patch_all_series(monkeypatch, lambda k, s: None)

    with pytest.raises(MarketDataError) as exc_info:
        market_data.get_treasury_curve(api_key=DUMMY_KEY)
    # This is the ONLY case where the legacy message is appropriate.
    assert LEGACY_MSG in str(exc_info.value)
    # No exception was thrown per-series, so nothing to chain.
    assert exc_info.value.__cause__ is None
