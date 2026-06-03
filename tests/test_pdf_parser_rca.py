"""RCA Option A — refuse unsupported products (docs/RCA_pdf_parser_unsupported_products.md).

Covers RCA §7F: the canonical basket refusal, the §6 boundary matrix for
`check_supported`, single-name no-false-positive regression, the date-split /
ticker-normalization / inferred_fields contract (§D), and the engine backstop.

The Anthropic client is fully mocked everywhere — NO live key, NO network, NO
token spend on the PDF path. We patch ``anthropic.Anthropic`` so
``parse_term_sheet`` never reaches the wire.
"""
from __future__ import annotations

import datetime
import json
from unittest import mock

import pytest

import anthropic
from prism import (
    Autocallable, ReverseConvertible, PrincipalProtected, BarrierNote,
    BufferedNote, price_product, DecompositionResult,
)
from prism.pdf_parser import (
    parse_term_sheet, check_supported,
    UnsupportedProductError, PdfParseError,
)

PDF_BYTES = b"%PDF-1.7\nfake basket term sheet\n%%EOF"
API_KEY = "sk-ant-RCA-SECRET-DO-NOT-LEAK-999"

# Offline market inputs (mirrors tests/test_phase2_products.py) so price_product
# runs without any network / FRED / yfinance call.
OFFLINE = dict(spot=200.0, risk_free=0.045, div_yield=0.005,
               credit_spread=0.009, flat_vol=0.28)
SEED = 7


# ---------------------------------------------------------------------------
# Fake Messages API response + client (no network).
# ---------------------------------------------------------------------------
class _Block:
    def __init__(self, text):
        self.text = text


class _Resp:
    def __init__(self, text):
        self.content = [_Block(text)]


def _mock_client_returning(text):
    captured = {}

    class _Messages:
        def create(self, **kwargs):
            captured["request"] = kwargs
            return _Resp(text)

    class _Client:
        def __init__(self, **kwargs):
            captured["init_kwargs"] = kwargs
            self.messages = _Messages()

    return _Client, captured


def _parse(payload):
    """Run parse_term_sheet against a mocked client returning ``payload`` JSON."""
    cls, captured = _mock_client_returning(json.dumps(payload))
    with mock.patch.object(anthropic, "Anthropic", cls):
        result = parse_term_sheet(PDF_BYTES, API_KEY)
    return result, captured


# ===========================================================================
# 1. Basket refusal — the canonical bug (RCA §3 / §7F.1 / §8 acceptance).
#    JPMorgan Airbag In-Digital Notes: 5-index unequally-weighted basket,
#    90% barrier, ~1.111x geared downside, airbag.
# ===========================================================================
AIRBAG_BASKET = {
    "product_type": "barrier_note",
    "underlier": "SX5E",                # largest-weight leg only (40%)
    "notional": 1000,
    "final_valuation_date": "2027-05-28",
    "maturity_date": "2027-06-02",
    "issuer": "JPMorgan Chase Financial Company LLC",
    "issuer_rating": None,
    "offer_price": 100,
    "fixed_return": 13.40,
    "barrier": 90,
    "barrier_type": "european",
    # Detection fields — what gates pricing.
    "num_underlyings": 5,
    "is_basket": True,
    "basket_constituents": [
        {"ticker": "SX5E", "weight": 40.0},
        {"ticker": "NKY", "weight": 25.0},
        {"ticker": "UKX", "weight": 17.5},
        {"ticker": "SMI", "weight": 10.0},
        {"ticker": "AS51", "weight": 7.5},
    ],
    "unsupported_features": ["basket", "geared_downside", "airbag"],
}


def test_basket_pdf_raises_unsupported_product_error():
    cls, _ = _mock_client_returning(json.dumps(AIRBAG_BASKET))
    with mock.patch.object(anthropic, "Anthropic", cls):
        with pytest.raises(UnsupportedProductError) as excinfo:
            parse_term_sheet(PDF_BYTES, API_KEY)
    exc = excinfo.value
    # Subclass of PdfParseError so generic callers still catch it.
    assert isinstance(exc, PdfParseError)
    reasons_text = " ".join(exc.reasons).lower()
    # A basket reason AND a geared-downside reason must both be present
    # (substring/keyword match, not exact prose).
    assert "basket" in reasons_text, exc.reasons
    assert "geared" in reasons_text or "1:1" in reasons_text, exc.reasons


def test_basket_refusal_lists_each_reason_once():
    """The redundant 'basket' in unsupported_features must not double-report."""
    reasons = check_supported(AIRBAG_BASKET)
    basket_reasons = [r for r in reasons if "basket" in r.lower()]
    assert len(basket_reasons) == 1, reasons
    assert any("geared" in r.lower() for r in reasons)
    assert any("airbag" in r.lower() for r in reasons)


def test_basket_refusal_never_leaks_api_key():
    cls, _ = _mock_client_returning(json.dumps(AIRBAG_BASKET))
    with mock.patch.object(anthropic, "Anthropic", cls):
        with pytest.raises(UnsupportedProductError) as excinfo:
            parse_term_sheet(PDF_BYTES, API_KEY)
    exc = excinfo.value
    assert API_KEY not in str(exc)
    for r in exc.reasons:
        assert API_KEY not in r


# ===========================================================================
# 2. check_supported §6 boundary matrix (RCA §7F.2).
# ===========================================================================
def _clean(ptype="autocallable", **extra):
    """A minimal clean single-name extracted dict for ``ptype``."""
    base = {
        "product_type": ptype,
        "underlier": "AAPL",
        "num_underlyings": 1,
        "is_basket": False,
        "basket_constituents": [],
        "unsupported_features": [],
    }
    base.update(extra)
    return base


@pytest.mark.parametrize("ptype", [
    "autocallable", "reverse_convertible", "principal_protected",
    "barrier_note", "buffered_note",
])
def test_clean_single_name_supported_for_each_type(ptype):
    assert check_supported(_clean(ptype)) == []


@pytest.mark.parametrize("mutate, kw", [
    ("is_basket flag", {"is_basket": True}),
    ("num_underlyings>1", {"num_underlyings": 3}),
    ("constituents>1", {"basket_constituents": [
        {"ticker": "A", "weight": 50}, {"ticker": "B", "weight": 50}]}),
    ("worst_of", {"unsupported_features": ["worst_of"]}),
    ("geared_downside", {"unsupported_features": ["geared_downside"]}),
    ("airbag", {"unsupported_features": ["airbag"]}),
    ("range_accrual", {"unsupported_features": ["range_accrual"]}),
    ("dual_directional", {"unsupported_features": ["dual_directional"]}),
    ("snowball", {"unsupported_features": ["snowball"]}),
])
def test_each_unsupported_feature_is_flagged(mutate, kw):
    reasons = check_supported(_clean(**kw))
    assert reasons, f"{mutate} should produce a non-empty reason list"


def test_unsupported_product_type_flagged():
    reasons = check_supported(_clean(product_type="snowball_autocall"))
    assert any("product type" in r.lower() for r in reasons)


def test_invalid_barrier_type_flagged():
    reasons = check_supported(_clean("reverse_convertible", barrier_type="bermudan"))
    assert any("barrier type" in r.lower() for r in reasons)


def test_valid_barrier_types_pass():
    assert check_supported(_clean("reverse_convertible", barrier_type="european")) == []
    assert check_supported(_clean("barrier_note", barrier_type="american")) == []


# --- tolerance of missing / None detection fields (older-style extraction) ---
def test_missing_detection_fields_single_underlier_passes():
    """An older extraction with only product_type+underlier (no detection
    fields) and a single underlier must NOT be flagged."""
    older = {"product_type": "autocallable", "underlier": "AAPL"}
    assert check_supported(older) == []


def test_none_detection_fields_treated_as_not_flagged():
    older = {
        "product_type": "reverse_convertible", "underlier": "AAPL",
        "num_underlyings": None, "is_basket": None,
        "basket_constituents": None, "unsupported_features": None,
        "barrier_type": "european",
    }
    assert check_supported(older) == []


def test_more_than_one_constituent_flags_even_without_other_flags():
    older = {
        "product_type": "autocallable", "underlier": "AAPL",
        "num_underlyings": None, "is_basket": None,
        "basket_constituents": [
            {"ticker": "A", "weight": 50}, {"ticker": "B", "weight": 50}],
    }
    reasons = check_supported(older)
    assert any("basket" in r.lower() for r in reasons)


def test_check_supported_non_dict_input():
    assert check_supported(None) != []
    assert check_supported("nope") != []


def test_check_supported_never_references_api_key():
    """Defensive: even feeding the key into the dict must not echo it back."""
    payload = _clean(product_type="weird_type", underlier=API_KEY)
    reasons = check_supported(payload)
    for r in reasons:
        assert API_KEY not in r


# ===========================================================================
# 3. Single-name regression — no false positives (RCA §7F.3 / §8).
# ===========================================================================
SUPPORTED_AUTOCALL = {
    "product_type": "autocallable",
    "underlier": "AAPL",
    "notional": 100000,
    "final_valuation_date": "2029-11-27",
    "maturity_date": "2029-11-30",
    "issuer": "JPMorgan Chase",
    "issuer_rating": "A",
    "offer_price": 100,
    "coupon_rate": 9.5,
    "coupon_barrier": 70,
    "call_barrier": 100,
    "knock_in_barrier": 60,
    "observation_freq": "quarterly",
    "num_underlyings": 1,
    "is_basket": False,
    "basket_constituents": [],
    "unsupported_features": [],
}

SUPPORTED_RC = {
    "product_type": "reverse_convertible",
    "underlier": "MSFT",
    "notional": 1000,
    "final_valuation_date": "2027-01-12",
    "maturity_date": "2027-01-15",
    "issuer": "Citigroup Global Markets Holdings Inc.",
    "issuer_rating": "A",
    "offer_price": 100,
    "coupon_rate": 9.0,
    "barrier": 70,
    "barrier_type": "european",
    "num_underlyings": 1,
    "is_basket": False,
    "basket_constituents": [],
    "unsupported_features": [],
}


def test_supported_autocallable_check_supported_empty():
    assert check_supported(SUPPORTED_AUTOCALL) == []


def test_supported_rc_check_supported_empty():
    assert check_supported(SUPPORTED_RC) == []


def test_supported_autocallable_parses_and_coerces():
    result, _ = _parse(SUPPORTED_AUTOCALL)
    assert result["product_type"] == "autocallable"
    assert result["underlier"] == "AAPL"
    # percents -> fractions
    assert result["coupon_rate"] == pytest.approx(0.095)
    assert result["coupon_barrier"] == pytest.approx(0.70)
    assert result["offer_price"] == pytest.approx(1.0)
    assert isinstance(result["maturity"], datetime.date)
    assert "inferred_fields" in result
    # detection fields are NOT leaked into the priceable dict
    for k in ("num_underlyings", "is_basket", "basket_constituents",
              "unsupported_features"):
        assert k not in result


def test_supported_parse_then_price_returns_decomposition():
    """The coerced dict builds a dataclass that price_product can value."""
    result, _ = _parse(SUPPORTED_AUTOCALL)
    product = Autocallable(
        underlier=result["underlier"],
        notional=result["notional"],
        maturity=datetime.date.today() + datetime.timedelta(days=365 * 3),
        issuer=result["issuer"],
        issuer_rating=result["issuer_rating"],
        offer_price=result["offer_price"],
        coupon_rate=result["coupon_rate"],
        coupon_barrier=result["coupon_barrier"],
        call_barrier=result["call_barrier"],
        knock_in_barrier=result["knock_in_barrier"],
        observation_freq=result["observation_freq"],
    )
    res = price_product(product, n_paths=5_000, seed=SEED, **OFFLINE)
    assert isinstance(res, DecompositionResult)
    assert res.fair_value > 0
    assert isinstance(res.greeks, dict) and res.greeks


def test_supported_rc_parses_and_prices():
    result, _ = _parse(SUPPORTED_RC)
    assert result["barrier_type"] == "european"
    assert result["barrier"] == pytest.approx(0.70)
    product = ReverseConvertible(
        underlier=result["underlier"],
        notional=result["notional"],
        maturity=datetime.date.today() + datetime.timedelta(days=365),
        issuer=result["issuer"],
        issuer_rating=result["issuer_rating"],
        offer_price=result["offer_price"],
        coupon_rate=result["coupon_rate"],
        barrier=result["barrier"],
        barrier_type=result["barrier_type"],
    )
    res = price_product(product, n_paths=5_000, seed=SEED, **OFFLINE)
    assert isinstance(res, DecompositionResult)
    assert res.fair_value > 0


# ===========================================================================
# 4. Date split + ticker normalization + inferred_fields (RCA §D / §7F.4).
# ===========================================================================
def test_maturity_uses_maturity_date_not_valuation():
    """When both dates are present, maturity maps to the payment date."""
    result, _ = _parse(SUPPORTED_AUTOCALL)
    assert result["maturity"] == datetime.date(2029, 11, 30)   # maturity_date
    assert result["maturity"] != datetime.date(2029, 11, 27)   # not valuation
    assert "maturity" not in result["inferred_fields"]


def test_maturity_falls_back_to_valuation_and_is_flagged_inferred():
    payload = dict(SUPPORTED_AUTOCALL)
    payload["maturity_date"] = None          # only valuation present
    payload["final_valuation_date"] = "2029-11-27"
    result, _ = _parse(payload)
    assert result["maturity"] == datetime.date(2029, 11, 27)
    assert "maturity" in result["inferred_fields"]


def test_maturity_backwards_compat_single_field():
    payload = dict(SUPPORTED_AUTOCALL)
    payload.pop("maturity_date", None)
    payload.pop("final_valuation_date", None)
    payload["maturity"] = "2030-03-15"
    result, _ = _parse(payload)
    assert result["maturity"] == datetime.date(2030, 3, 15)


@pytest.mark.parametrize("bloomberg, yahoo", [
    ("SX5E", "^STOXX50E"),
    ("NKY", "^N225"),
    ("UKX", "^FTSE"),
    ("SMI", "^SSMI"),
    ("AS51", "^AXJO"),
])
def test_bloomberg_ticker_normalized(bloomberg, yahoo):
    payload = dict(SUPPORTED_AUTOCALL)
    payload["underlier"] = bloomberg
    result, _ = _parse(payload)
    assert result["underlier"] == yahoo


@pytest.mark.parametrize("ticker", ["AAPL", "^GSPC", "MSFT", "SPY"])
def test_unknown_or_yahoo_ticker_passthrough(ticker):
    payload = dict(SUPPORTED_AUTOCALL)
    payload["underlier"] = ticker
    result, _ = _parse(payload)
    assert result["underlier"] == ticker


def test_inferred_fields_empty_when_clean():
    result, _ = _parse(SUPPORTED_AUTOCALL)
    assert result["inferred_fields"] == []


# ===========================================================================
# 5. Engine backstop (RCA §7F.5 / §B).
# ===========================================================================
def test_price_product_rejects_unsupported_dataclass_type():
    class NotAProduct:
        pass
    with pytest.raises(TypeError) as excinfo:
        price_product(NotAProduct(), n_paths=1000, seed=SEED, **OFFLINE)
    msg = str(excinfo.value)
    # The message names the supported types.
    for name in ("Autocallable", "ReverseConvertible", "PrincipalProtected",
                 "BarrierNote", "BufferedNote"):
        assert name in msg, f"{name} missing from guard message: {msg}"
