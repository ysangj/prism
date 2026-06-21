"""PDF Report Export regression suite (PRD §8.5).

Validates ``prism.build_report_pdf`` / ``prism.report_filename`` for ALL FIVE
product types, fully OFFLINE and deterministic (no network, no keys, no token
spend): every product is priced via ``price_product`` with explicit market
overrides + a fixed seed (the conftest offline-market pattern), then the report
is built and inspected with the already-installed ``pypdf``.

Coverage maps to PRD §8.5 "Contents":
  * Product summary (term-sheet parameters)
  * Fair-value decomposition + methodology notes
  * Payoff diagram
  * Risk metrics
  * Market-data snapshot (rates / vol / spot)
  * Disclaimer + methodology disclosure
  * Timestamp + data sources cited

Plus robustness/safety: graceful degradation on missing optional fields /
``meta=None`` / negative embedded margin, deterministic filename, and a
no-secret-leakage guard.
"""
from __future__ import annotations

import datetime as dt
from io import BytesIO

import pytest
from pypdf import PdfReader

from prism import build_report_pdf, report_filename, price_product
from prism.models import (
    Autocallable,
    BarrierNote,
    BufferedNote,
    PrincipalProtected,
    ReverseConvertible,
)

# Deterministic offline pricing inputs. Reuses the conftest offline-market shape
# (spot/risk_free/div_yield/credit_spread/flat_vol) + a fixed seed so the priced
# result is reproducible. Modest path count keeps the suite fast.
SEED = 12345
N_PATHS = 8000
PINNED_TS = "2026-06-20T15:30:00+00:00"
PINNED_DATE = "20260620"

# Secret-like tokens that must NEVER appear in a report (case-insensitive).
_SECRET_TOKENS = ("sk-ant", "fred_api_key", "api_key", "bearer", "secret")


def _maturity(years: float) -> dt.date:
    return dt.date.today() + dt.timedelta(days=int(round(years * 365.25)))


_OFFLINE = dict(
    spot=200.0,
    risk_free=0.045,
    div_yield=0.005,
    credit_spread=0.045,
    flat_vol=0.30,
)


def _make_product(kind: str):
    """One representative term sheet per product type (single-underlier, in-scope)."""
    common = dict(
        underlier="AAPL",
        notional=100_000,
        maturity=_maturity(3.0),
        issuer="JPMorgan Chase",
        issuer_rating="A",
        offer_price=1.0,
    )
    if kind == "autocallable":
        return Autocallable(
            **common, coupon_rate=0.095, coupon_barrier=0.70, call_barrier=1.00,
            knock_in_barrier=0.60, observation_freq="quarterly",
        )
    if kind == "reverse_convertible":
        return ReverseConvertible(
            **common, coupon_rate=0.09, barrier=0.70, barrier_type="european",
        )
    if kind == "principal_protected":
        return PrincipalProtected(
            **common, participation=1.0, cap=0.30, floor=1.0,
        )
    if kind == "barrier_note":
        return BarrierNote(
            **common, fixed_return=0.20, barrier=0.80, barrier_type="european",
        )
    if kind == "buffered_note":
        return BufferedNote(
            **common, upside_leverage=1.5, cap=0.25, buffer=0.10,
        )
    raise ValueError(kind)


def _price(product):
    return price_product(product, n_paths=N_PATHS, seed=SEED, **_OFFLINE)


def _meta(**overrides):
    base = dict(
        demo_mode=True,
        generated_at=PINNED_TS,
        n_paths=N_PATHS,
        data_source="Demo (offline)",
    )
    base.update(overrides)
    return base


def _read(pdf_bytes: bytes) -> PdfReader:
    return PdfReader(BytesIO(pdf_bytes))


def _text(pdf_bytes: bytes) -> str:
    reader = _read(pdf_bytes)
    return "\n".join((page.extract_text() or "") for page in reader.pages)


ALL_KINDS = [
    "autocallable",
    "reverse_convertible",
    "principal_protected",
    "barrier_note",
    "buffered_note",
]


# Price each product once per session (MC is the slow part); reports are cheap.
@pytest.fixture(scope="module", params=ALL_KINDS)
def priced(request):
    product = _make_product(request.param)
    result = _price(product)
    return request.param, product, result


# ===========================================================================
# 1. Valid PDF: bytes / %PDF- header / pages > 0  — for ALL FIVE types.
# ===========================================================================
def test_report_is_valid_nonempty_pdf(priced):
    kind, product, result = priced
    pdf = build_report_pdf(product, result, meta=_meta())
    assert isinstance(pdf, bytes) and pdf, f"{kind}: empty / non-bytes report"
    assert pdf.startswith(b"%PDF-"), f"{kind}: missing %PDF- header"
    assert b"%%EOF" in pdf, f"{kind}: missing %%EOF trailer"
    reader = _read(pdf)
    assert len(reader.pages) > 0, f"{kind}: PDF has no pages"


# ===========================================================================
# 2. Content checks (PRD §8.5 contents) via pypdf text extraction.
#    Keyword-based / resilient substrings, not exact sentences.
# ===========================================================================
def test_report_contains_product_and_underlier(priced):
    kind, product, result = priced
    text = _text(build_report_pdf(product, result, meta=_meta())).lower()
    assert "aapl" in text, f"{kind}: underlier (AAPL) not in report"
    assert "product summary" in text, f"{kind}: no product-summary section"


def test_report_contains_embedded_margin_verdict(priced):
    kind, product, result = priced
    text = _text(build_report_pdf(product, result, meta=_meta())).lower()
    assert "fair value" in text, f"{kind}: no 'fair value' headline/verdict"
    assert "margin" in text, f"{kind}: no embedded-margin wording"
    assert "decomposition" in text, f"{kind}: no decomposition section"


def test_report_contains_risk_section(priced):
    kind, product, result = priced
    text = _text(build_report_pdf(product, result, meta=_meta())).lower()
    assert "risk metrics" in text, f"{kind}: no risk-metrics section"
    assert ("loss" in text), f"{kind}: no P(loss) wording"
    # Greeks present.
    for g in ("delta", "vega", "rho"):
        assert g in text, f"{kind}: Greek {g!r} not in risk section"


def test_report_contains_market_snapshot(priced):
    kind, product, result = priced
    text = _text(build_report_pdf(product, result, meta=_meta())).lower()
    assert "market data snapshot" in text, f"{kind}: no market snapshot section"
    assert "spot" in text, f"{kind}: spot not in snapshot"
    assert ("risk-free" in text or "risk free" in text), f"{kind}: rate not in snapshot"
    assert ("vol" in text), f"{kind}: vol not in snapshot"


def test_report_contains_disclaimer_and_methodology(priced):
    kind, product, result = priced
    text = _text(build_report_pdf(product, result, meta=_meta())).lower()
    assert "not investment advice" in text, f"{kind}: disclaimer missing"
    assert "methodology" in text, f"{kind}: methodology mention missing"
    # Methodology cites the modeling approach + data sources.
    assert "monte carlo" in text, f"{kind}: methodology lacks Monte Carlo mention"
    assert "yfinance" in text and "fred" in text, (
        f"{kind}: data sources (yfinance/FRED) not cited")


def test_report_contains_timestamp(priced):
    kind, product, result = priced
    text = _text(build_report_pdf(product, result, meta=_meta()))
    assert "2026-06-20" in text, f"{kind}: pinned generated timestamp not shown"


def test_report_contains_payoff_section(priced):
    kind, product, result = priced
    text = _text(build_report_pdf(product, result, meta=_meta())).lower()
    assert "payoff at maturity" in text, f"{kind}: no payoff section"


# ===========================================================================
# 3. No secret leakage — a meta WITHOUT any secret yields no secret-like text.
# ===========================================================================
def test_report_has_no_secret_leakage(priced):
    kind, product, result = priced
    # Build with a clean meta (no keys). Verify neither the raw bytes nor the
    # extracted text carries any secret-like token (case-insensitive).
    pdf = build_report_pdf(product, result, meta=_meta())
    lower_bytes = pdf.lower()
    text_lower = _text(pdf).lower()
    for tok in _SECRET_TOKENS:
        btok = tok.encode()
        assert btok not in lower_bytes, f"{kind}: secret token {tok!r} in PDF bytes"
        assert tok not in text_lower, f"{kind}: secret token {tok!r} in PDF text"


def test_report_ignores_secret_keys_injected_into_meta(priced):
    """Defensive: even if a caller wrongly stuffs a secret into meta, the report
    must NOT render or embed it (the backend reads only the documented keys)."""
    kind, product, result = priced
    dirty = _meta()
    dirty["api_key"] = "sk-ant-THIS-MUST-NOT-LEAK-INTO-THE-PDF-0001"
    dirty["FRED_API_KEY"] = "FRED-SECRET-MUST-NOT-LEAK-0002"
    dirty["Authorization"] = "Bearer leaky-token-0003"
    pdf = build_report_pdf(product, result, meta=dirty)
    blob = pdf.lower()
    text = _text(pdf).lower()
    for canary in ("sk-ant-this-must-not-leak", "fred-secret-must-not-leak",
                   "leaky-token-0003"):
        assert canary.encode() not in blob, f"{kind}: meta secret leaked into bytes"
        assert canary not in text, f"{kind}: meta secret leaked into text"


# ===========================================================================
# 4. Graceful degradation — None optional fields / meta=None / negative margin.
# ===========================================================================
def test_report_builds_with_none_optional_fields_and_meta_none():
    """A result with several optional fields = None and meta=None still builds a
    valid PDF (no exception)."""
    product = _make_product("autocallable")
    result = _price(product)
    # Blank out optional/diagnostic fields the report reads defensively.
    result.atm_vol = None
    result.credit_spread = None
    result.div_yield = None
    result.notes = []
    result.low_confidence_vol = False
    result.low_confidence_curve = False

    pdf = build_report_pdf(product, result, meta=None)
    assert pdf.startswith(b"%PDF-"), "meta=None / None fields: invalid PDF"
    reader = _read(pdf)
    assert len(reader.pages) > 0, "meta=None: no pages"
    # Robustly renders 'n/a' for the missing snapshot fields rather than crashing.
    assert "n/a" in _text(pdf).lower(), "missing optional fields should show 'n/a'"


def test_report_builds_with_empty_payoff_and_distribution():
    """Empty payoff_curve / return_distribution → chart falls back to table /
    'n/a', no exception, valid PDF."""
    product = _make_product("buffered_note")
    result = _price(product)
    result.payoff_curve = []
    result.return_distribution = []

    pdf = build_report_pdf(product, result, meta=_meta())
    assert pdf.startswith(b"%PDF-")
    assert len(_read(pdf).pages) > 0


def test_report_builds_with_negative_embedded_margin():
    """Offer below fair value → negative embedded margin. The stacked decomposition
    chart can't render and falls back to a caption/table; the report still builds
    as a valid PDF."""
    product = _make_product("autocallable")
    # Offer well below par forces fair_value > offer => embedded_margin < 0.
    product.offer_price = 0.85
    result = _price(product)
    assert result.embedded_margin < 0, "test setup: expected a negative margin"

    pdf = build_report_pdf(product, result, meta=_meta())
    assert pdf.startswith(b"%PDF-"), "negative-margin report invalid"
    assert len(_read(pdf).pages) > 0
    text = _text(pdf).lower()
    # Verdict acknowledges the below-fair-value case.
    assert "below" in text or "cheap" in text, (
        "negative-margin verdict wording not found")


# ===========================================================================
# 5. report_filename — sanitized .pdf, contains underlier + product type,
#    no spaces/secrets, deterministic given meta['generated_at'].
# ===========================================================================
@pytest.mark.parametrize("kind,slug", [
    ("autocallable", "autocallable"),
    ("reverse_convertible", "reverse_convertible"),
    ("principal_protected", "principal_protected"),
    ("barrier_note", "barrier_note"),
    ("buffered_note", "buffered_note"),
])
def test_report_filename_shape(kind, slug):
    product = _make_product(kind)
    name = report_filename(product, _meta())
    assert name.endswith(".pdf"), f"{kind}: filename must end with .pdf"
    assert " " not in name, f"{kind}: filename must have no spaces ({name!r})"
    assert "aapl" in name, f"{kind}: filename must contain the underlier"
    assert slug in name, f"{kind}: filename must contain the product slug"
    assert PINNED_DATE in name, f"{kind}: filename date must come from meta"
    # No secret-like tokens.
    low = name.lower()
    for tok in _SECRET_TOKENS:
        assert tok not in low, f"{kind}: secret token {tok!r} in filename"
    # Sanitized charset only.
    assert all(c.isalnum() or c in "._-" for c in name), (
        f"{kind}: filename has non-sanitized chars: {name!r}")


def test_report_filename_deterministic_given_generated_at():
    product = _make_product("autocallable")
    n1 = report_filename(product, _meta(generated_at=PINNED_TS))
    n2 = report_filename(product, _meta(generated_at=PINNED_TS))
    assert n1 == n2, "filename should be deterministic for a fixed generated_at"
    assert n1 == "prism_report_aapl_autocallable_20260620.pdf", (
        f"unexpected filename: {n1!r}")


def test_report_filename_date_tracks_generated_at():
    product = _make_product("autocallable")
    other = report_filename(product, _meta(generated_at="2025-01-02T00:00:00+00:00"))
    assert "20250102" in other, f"filename date should track meta: {other!r}"


# ===========================================================================
# 6. Determinism — same inputs + same meta['generated_at'] → stable output.
#    reportlab embeds an internal PDF CreationDate that varies per build call,
#    so raw bytes are NOT byte-identical across calls; we assert a stable page
#    count + stable key text instead (documented in TEST_RESULTS.md).
# ===========================================================================
def test_report_deterministic_pagecount_and_text():
    product = _make_product("autocallable")
    result = _price(product)
    pdf1 = build_report_pdf(product, result, meta=_meta())
    pdf2 = build_report_pdf(product, result, meta=_meta())

    pages1 = len(_read(pdf1).pages)
    pages2 = len(_read(pdf2).pages)
    assert pages1 == pages2, "page count should be stable across identical builds"
    assert pages1 > 0

    # Key text stable across builds (filename, pinned date, disclaimer).
    t1, t2 = _text(pdf1).lower(), _text(pdf2).lower()
    for marker in ("aapl", "2026-06-20", "not investment advice",
                   "fair value", "monte carlo"):
        assert (marker in t1) == (marker in t2) is True, (
            f"marker {marker!r} not stably present across builds")


def test_report_pinned_timestamp_avoids_now():
    """With meta['generated_at'] pinned, the report header shows the pinned date
    (the module does not stamp datetime.now() when generated_at is supplied)."""
    product = _make_product("reverse_convertible")
    result = _price(product)
    text = _text(build_report_pdf(product, result, meta=_meta()))
    today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    assert "2026-06-20" in text, "pinned date not shown in header/methodology"
    if today != "2026-06-20":
        assert today not in text, (
            "report should not stamp today's date when generated_at is pinned")
