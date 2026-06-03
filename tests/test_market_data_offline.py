"""PRD §15.5 checkpoint 2/4 helpers — offline-deterministic market-data behaviour.

Live yfinance/FRED fetches require outbound network (blocked in this sandbox);
those live calls are exercised in ``test_market_data_live`` and skipped when
unavailable. Here we verify the deterministic credit-spread lookup and that the
Treasury-curve fetcher fails loudly (rather than fabricating data) without a key.
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


def test_treasury_curve_requires_key(monkeypatch):
    # Per market_data failure policy: no key -> raise, never fabricate.
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    market_data.get_treasury_curve.cache_clear()
    with pytest.raises(MarketDataError):
        market_data.get_treasury_curve()
    market_data.get_treasury_curve.cache_clear()
