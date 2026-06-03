"""PRD §15.5 checkpoint 2/4 helpers — offline-deterministic market-data behaviour.

Live yfinance/FRED fetches require outbound network (blocked in this sandbox);
those live calls are exercised in ``test_market_data_live`` and skipped when
unavailable. Here we verify the deterministic credit-spread lookup and the
RCA-002 Treasury-curve contract: no key -> graceful static fallback (no raise);
a key supplied + genuine fetch failure -> still raises.
"""
import pytest

from prism import market_data
from prism.market_data import MarketDataError


def test_credit_spread_rating_lookup():
    assert market_data.get_credit_spread("JPMorgan", "A") == pytest.approx(0.0090)
    assert market_data.get_credit_spread("Citi", "BBB") == pytest.approx(0.0150)
    # notch suffixes collapse to the base bucket
    assert market_data.get_credit_spread("X", "A+") == pytest.approx(0.0090)
    assert market_data.get_credit_spread("X", "A-") == pytest.approx(0.0090)
    # Moody's notation maps to S&P buckets
    assert market_data.get_credit_spread("X", "Baa1") == pytest.approx(0.0150)


def test_credit_spread_unknown_rating_raises():
    with pytest.raises(MarketDataError):
        market_data.get_credit_spread("X", "ZZZ")


def test_treasury_curve_no_key_returns_static_fallback(monkeypatch):
    """RCA PRISM-RCA-002 §6.A (replaces the old 'no key -> raise' contract).

    With no ``api_key`` argument and no ``FRED_API_KEY`` in the environment,
    ``get_treasury_curve`` now returns the documented STATIC fallback curve and
    does NOT raise — the long-promised graceful degradation. The result must
    equal ``static_treasury_curve()``.
    """
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    market_data.clear_cache()  # cache is keyed on api_key; clear so no leak
    market_data.get_treasury_curve.cache_clear()

    curve = market_data.get_treasury_curve()  # must NOT raise
    assert curve == market_data.static_treasury_curve()

    market_data.clear_cache()
    market_data.get_treasury_curve.cache_clear()


def test_treasury_curve_keyed_fetch_failure_raises(monkeypatch):
    """A key WAS supplied -> a genuine FRED failure/empty result still raises.

    The raise behaviour moved from the missing-key case to the keyed-fetch case:
    if the caller provided a key and the live fetch genuinely fails, surface it
    as ``MarketDataError`` (and never echo the key).
    """
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    market_data.clear_cache()
    market_data.get_treasury_curve.cache_clear()

    secret = "FRED-KEY-SHOULD-NOT-LEAK-0001"

    def _fail(key):
        # Mocked genuine failure (e.g. bad key / outage / empty result).
        raise MarketDataError("FRED returned no Treasury data for any tenor")

    monkeypatch.setattr(market_data, "_fetch_fred_curve", _fail)

    with pytest.raises(MarketDataError) as exc:
        market_data.get_treasury_curve(api_key=secret)
    assert secret not in str(exc.value)

    market_data.clear_cache()
    market_data.get_treasury_curve.cache_clear()
