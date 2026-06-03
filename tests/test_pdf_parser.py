"""Phase 2: pdf_parser.parse_term_sheet — mocked client (no live Anthropic call).

PRD §8.1 Option B (BYOK PDF), §9 (API-key security: key never persisted/logged,
PDF/extracted data not persisted), §15.3 / BACKEND_NOTES §10 (contract).

The anthropic client is fully mocked: NO network call is made and NO tokens are
spent. We assert:
  * the request shape (PDF as base64 application/pdf document block, model set,
    api_key passed THROUGH the function arg and never read from env),
  * JSON-response parsing into the dict (product_type + per-type keys,
    percents→fractions, offer_price→fraction, maturity→date, missing→None),
  * PdfParseError on bad/unparseable output and on auth-style failures.
"""
from __future__ import annotations

import base64
import datetime
import json
from unittest import mock

import pytest

import anthropic
from prism import pdf_parser
from prism.pdf_parser import PdfParseError, parse_term_sheet

PDF_BYTES = b"%PDF-1.7\nfake term sheet bytes\n%%EOF"
API_KEY = "sk-ant-test-DO-NOT-LOG-123"


# ---------------------------------------------------------------------------
# Helpers to build a fake Messages API response.
# ---------------------------------------------------------------------------
class _Block:
    def __init__(self, text):
        self.text = text


class _Resp:
    def __init__(self, text):
        self.content = [_Block(text)]


def _mock_client_returning(text):
    """Return (mock_anthropic_cls, captured) where captured records the call."""
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


SAMPLE_JSON = json.dumps({
    "product_type": "autocallable",
    "underlier": "AAPL",
    "notional": 100000,
    "maturity": "2029-11-30",
    "issuer": "JPMorgan Chase",
    "issuer_rating": "A",
    "offer_price": 100,
    "coupon_rate": 9.5,
    "coupon_barrier": 70,
    "call_barrier": 100,
    "knock_in_barrier": 60,
    "observation_freq": "quarterly",
})


# ===========================================================================
# Request shape + key passthrough (BACKEND_NOTES §10).
# ===========================================================================
def test_request_sends_base64_pdf_document_block():
    cls, captured = _mock_client_returning(SAMPLE_JSON)
    with mock.patch.object(anthropic, "Anthropic", cls):
        parse_term_sheet(PDF_BYTES, API_KEY)

    req = captured["request"]
    content = req["messages"][0]["content"]
    doc = next(b for b in content if b.get("type") == "document")
    assert doc["source"]["type"] == "base64"
    assert doc["source"]["media_type"] == "application/pdf"
    # base64 of the document block round-trips to the original PDF bytes.
    assert base64.standard_b64decode(doc["source"]["data"]) == PDF_BYTES
    # A text prompt block accompanies the document.
    assert any(b.get("type") == "text" for b in content)


def test_request_sets_model():
    cls, captured = _mock_client_returning(SAMPLE_JSON)
    with mock.patch.object(anthropic, "Anthropic", cls):
        parse_term_sheet(PDF_BYTES, API_KEY)
    assert captured["request"]["model"] == pdf_parser._MODEL


def test_api_key_passed_through_to_client():
    cls, captured = _mock_client_returning(SAMPLE_JSON)
    with mock.patch.object(anthropic, "Anthropic", cls):
        parse_term_sheet(PDF_BYTES, API_KEY)
    assert captured["init_kwargs"].get("api_key") == API_KEY


def test_api_key_never_read_from_env(monkeypatch):
    """PRD §9: the key is a function arg only — never from the environment.

    With a key in the function arg but a DIFFERENT one in the env, the client
    must receive the function arg, never the env value.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-ENV-SHOULD-BE-IGNORED")
    cls, captured = _mock_client_returning(SAMPLE_JSON)
    with mock.patch.object(anthropic, "Anthropic", cls):
        parse_term_sheet(PDF_BYTES, API_KEY)
    assert captured["init_kwargs"].get("api_key") == API_KEY
    assert captured["init_kwargs"].get("api_key") != "sk-ant-ENV-SHOULD-BE-IGNORED"


# ===========================================================================
# Parsing / coercion of a good response (BACKEND_NOTES §10).
# ===========================================================================
def test_parses_autocallable_sample():
    cls, _ = _mock_client_returning(SAMPLE_JSON)
    with mock.patch.object(anthropic, "Anthropic", cls):
        result = parse_term_sheet(PDF_BYTES, API_KEY)

    assert result["product_type"] == "autocallable"
    assert result["underlier"] == "AAPL"
    assert result["notional"] == 100000.0
    assert result["issuer"] == "JPMorgan Chase"
    assert result["issuer_rating"] == "A"
    # percents -> fractions
    assert result["coupon_rate"] == pytest.approx(0.095)
    assert result["coupon_barrier"] == pytest.approx(0.70)
    assert result["call_barrier"] == pytest.approx(1.00)
    assert result["knock_in_barrier"] == pytest.approx(0.60)
    # offer_price 100 (% of par) -> 1.0
    assert result["offer_price"] == pytest.approx(1.0)
    # maturity -> date
    assert result["maturity"] == datetime.date(2029, 11, 30)
    assert result["observation_freq"] == "quarterly"


def test_offer_price_fraction_left_untouched():
    """offer_price given already as a fraction (1.0) stays 1.0."""
    payload = json.loads(SAMPLE_JSON)
    payload["offer_price"] = 1.0
    cls, _ = _mock_client_returning(json.dumps(payload))
    with mock.patch.object(anthropic, "Anthropic", cls):
        result = parse_term_sheet(PDF_BYTES, API_KEY)
    assert result["offer_price"] == pytest.approx(1.0)


def test_missing_fields_become_none():
    payload = {
        "product_type": "autocallable",
        "underlier": "SPY",
        # everything else absent
    }
    cls, _ = _mock_client_returning(json.dumps(payload))
    with mock.patch.object(anthropic, "Anthropic", cls):
        result = parse_term_sheet(PDF_BYTES, API_KEY)
    assert result["underlier"] == "SPY"
    for key in ("notional", "maturity", "issuer", "issuer_rating",
                "offer_price", "coupon_rate", "coupon_barrier",
                "call_barrier", "knock_in_barrier", "observation_freq"):
        assert result[key] is None, f"{key} should be None when absent"


def test_only_relevant_keys_returned_per_type():
    """A reverse_convertible response returns RC keys + common, not autocallable."""
    payload = {
        "product_type": "reverse_convertible",
        "underlier": "AAPL", "notional": 1000, "maturity": "2027-01-15",
        "issuer": "Citi", "issuer_rating": "BBB", "offer_price": 100,
        "coupon_rate": 9.0, "barrier": 70, "barrier_type": "european",
        # an irrelevant autocallable-only field the model might leak:
        "knock_in_barrier": 60,
    }
    cls, _ = _mock_client_returning(json.dumps(payload))
    with mock.patch.object(anthropic, "Anthropic", cls):
        result = parse_term_sheet(PDF_BYTES, API_KEY)
    assert result["product_type"] == "reverse_convertible"
    assert result["barrier"] == pytest.approx(0.70)
    assert result["barrier_type"] == "european"
    assert "knock_in_barrier" not in result  # not an RC field


def test_buffered_leverage_stays_a_multiple():
    payload = {
        "product_type": "buffered_note",
        "underlier": "QQQ", "notional": 1000, "maturity": "2030-06-01",
        "issuer": "GS", "issuer_rating": "A", "offer_price": 100,
        "upside_leverage": 1.5, "cap": 25, "buffer": 10,
    }
    cls, _ = _mock_client_returning(json.dumps(payload))
    with mock.patch.object(anthropic, "Anthropic", cls):
        result = parse_term_sheet(PDF_BYTES, API_KEY)
    assert result["upside_leverage"] == pytest.approx(1.5)  # multiple, NOT /100
    assert result["cap"] == pytest.approx(0.25)


def test_buffered_buffer_percent_to_fraction():
    payload = {
        "product_type": "buffered_note",
        "underlier": "QQQ", "notional": 1000, "maturity": "2030-06-01",
        "issuer": "GS", "issuer_rating": "A", "offer_price": 100,
        "upside_leverage": 1.5, "cap": 25, "buffer": 10,
    }
    cls, _ = _mock_client_returning(json.dumps(payload))
    with mock.patch.object(anthropic, "Anthropic", cls):
        result = parse_term_sheet(PDF_BYTES, API_KEY)
    assert result["buffer"] == pytest.approx(0.10)
    # upside_leverage is a multiple, NOT a percent -> left unscaled.
    assert result["upside_leverage"] == pytest.approx(1.5)


def test_ppn_participation_percent_to_fraction():
    payload = {
        "product_type": "principal_protected",
        "underlier": "AAPL", "notional": 1000, "maturity": "2030-06-01",
        "issuer": "GS", "issuer_rating": "A", "offer_price": 100,
        "participation": 100, "cap": 30, "floor": 100,
    }
    cls, _ = _mock_client_returning(json.dumps(payload))
    with mock.patch.object(anthropic, "Anthropic", cls):
        result = parse_term_sheet(PDF_BYTES, API_KEY)
    assert result["participation"] == pytest.approx(1.0)


def test_parses_json_in_code_fence():
    cls, _ = _mock_client_returning("```json\n" + SAMPLE_JSON + "\n```")
    with mock.patch.object(anthropic, "Anthropic", cls):
        result = parse_term_sheet(PDF_BYTES, API_KEY)
    assert result["product_type"] == "autocallable"


def test_parses_json_with_surrounding_prose():
    text = "Here is the extracted data:\n" + SAMPLE_JSON + "\nHope this helps!"
    cls, _ = _mock_client_returning(text)
    with mock.patch.object(anthropic, "Anthropic", cls):
        result = parse_term_sheet(PDF_BYTES, API_KEY)
    assert result["product_type"] == "autocallable"


# ===========================================================================
# Error paths -> PdfParseError (BACKEND_NOTES §10).
# ===========================================================================
def test_empty_pdf_raises():
    with pytest.raises(PdfParseError):
        parse_term_sheet(b"", API_KEY)


def test_blank_key_raises():
    with pytest.raises(PdfParseError):
        parse_term_sheet(PDF_BYTES, "")


def test_non_json_output_raises():
    cls, _ = _mock_client_returning("I could not read this document, sorry.")
    with mock.patch.object(anthropic, "Anthropic", cls):
        with pytest.raises(PdfParseError):
            parse_term_sheet(PDF_BYTES, API_KEY)


def test_empty_model_output_raises():
    cls, _ = _mock_client_returning("")
    with mock.patch.object(anthropic, "Anthropic", cls):
        with pytest.raises(PdfParseError):
            parse_term_sheet(PDF_BYTES, API_KEY)


def test_unknown_product_type_raises():
    payload = {"product_type": "snowball_autocallable", "underlier": "AAPL"}
    cls, _ = _mock_client_returning(json.dumps(payload))
    with mock.patch.object(anthropic, "Anthropic", cls):
        with pytest.raises(PdfParseError):
            parse_term_sheet(PDF_BYTES, API_KEY)


def test_auth_failure_raises_pdfparseerror():
    """An auth-style SDK error becomes a friendly PdfParseError (no key echoed)."""
    class _Messages:
        def create(self, **kwargs):
            # Construct an AuthenticationError without hitting the network.
            req = mock.Mock()
            body = {"error": {"message": "invalid x-api-key"}}
            try:
                raise anthropic.AuthenticationError(
                    message="invalid x-api-key",
                    response=mock.Mock(status_code=401, request=req),
                    body=body,
                )
            except TypeError:
                # SDK signature fallback.
                raise anthropic.AuthenticationError("invalid x-api-key")

    class _Client:
        def __init__(self, **kwargs):
            self.messages = _Messages()

    with mock.patch.object(anthropic, "Anthropic", _Client):
        with pytest.raises(PdfParseError) as exc:
            parse_term_sheet(PDF_BYTES, API_KEY)
    # Friendly message; the key must not leak.
    assert API_KEY not in str(exc.value)


def test_generic_sdk_exception_raises_pdfparseerror():
    class _Messages:
        def create(self, **kwargs):
            raise RuntimeError("some transport blew up")

    class _Client:
        def __init__(self, **kwargs):
            self.messages = _Messages()

    with mock.patch.object(anthropic, "Anthropic", _Client):
        with pytest.raises(PdfParseError) as exc:
            parse_term_sheet(PDF_BYTES, API_KEY)
    assert API_KEY not in str(exc.value)


def test_error_messages_never_contain_the_key():
    """No error path should ever echo the API key (PRD §9)."""
    bad_cases = [
        lambda: parse_term_sheet(b"", API_KEY),
    ]
    for case in bad_cases:
        with pytest.raises(PdfParseError) as exc:
            case()
        assert API_KEY not in str(exc.value)
