"""BYOK Claude term-sheet extraction (PRD 8.1 Option B, 15.3, 15.6).

``parse_term_sheet(pdf_bytes, api_key)`` sends a structured-product term-sheet
PDF to the Anthropic Messages API as a base64 ``document`` content block and asks
Claude to extract the product parameters into strict JSON. The returned dict's
keys match the corresponding product dataclass's fields (see :mod:`prism.models`)
plus a ``product_type`` key so the UI can route the result to the right form /
dataclass.

Security (PRD 9, 15.6 -- BYOK)
------------------------------
* The Anthropic API key is a **function argument only**. It is never read from an
  environment variable, config file, or disk, never logged, and never persisted.
* The uploaded PDF and the extracted data are not written anywhere; they live
  only for the duration of the call.
* Network/auth failures and unparseable PDFs are raised as :class:`PdfParseError`
  with a friendly message so the UI can show a clean error (the original key is
  never echoed in the message).

Conventions
-----------
* Percentages are converted to the fraction convention the dataclasses use
  (e.g. 70 -> 0.70, 9.5 -> 0.095). ``offer_price`` is special-cased: a term sheet
  states it as a percent of par (100 -> 1.0), but a value already given as a
  fraction (1.0) is left untouched.
* Dates are parsed to :class:`datetime.date`.
* If a field is absent / uncertain in the sheet, the model is instructed to emit
  ``null`` and the parser leaves it ``None`` for the user to fill -- nothing is
  hallucinated.

Live verification is deferred to manual UI testing with a real key (this module
ships with an offline, mocked self-check; see ``_selfcheck.py`` and
BACKEND_NOTES.md). No network call is made unless ``parse_term_sheet`` is invoked
with a real key.
"""

from __future__ import annotations

import base64
import datetime
import json
import re

import anthropic

__all__ = [
    "parse_term_sheet",
    "PdfParseError",
    "UnsupportedProductError",
    "check_supported",
]

# Current, cost/speed-efficient model. Internal constant -- change in one place.
_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 2048

# Per-type expected fields (mirror the dataclasses in prism.models). The UI maps
# these keys directly to its form fields.
_FIELDS_BY_TYPE = {
    "autocallable": [
        "underlier", "notional", "maturity", "issuer", "issuer_rating",
        "offer_price", "coupon_rate", "coupon_barrier", "call_barrier",
        "knock_in_barrier", "observation_freq",
    ],
    "reverse_convertible": [
        "underlier", "notional", "maturity", "issuer", "issuer_rating",
        "offer_price", "coupon_rate", "barrier", "barrier_type",
    ],
    "principal_protected": [
        "underlier", "notional", "maturity", "issuer", "issuer_rating",
        "offer_price", "participation", "cap", "floor",
    ],
    "barrier_note": [
        "underlier", "notional", "maturity", "issuer", "issuer_rating",
        "offer_price", "fixed_return", "barrier", "barrier_type",
    ],
    "buffered_note": [
        "underlier", "notional", "maturity", "issuer", "issuer_rating",
        "offer_price", "upside_leverage", "cap", "buffer",
    ],
}

# Fields expressed as percentages on the term sheet -> stored as fractions
# (value / 100). NOTE: offer_price is handled separately (see _scale_offer_price);
# notional is an amount and upside_leverage is a multiple, so neither is scaled.
# participation (PPN, 100% -> 1.0) and buffer (Buffered, 10% -> 0.10) ARE percents
# despite the prompt grouping them with leverage -- the dataclasses store them as
# fractions, so they must be divided by 100 like the other percent fields.
_PERCENT_FIELDS = {
    "coupon_rate", "coupon_barrier", "call_barrier", "knock_in_barrier",
    "barrier", "fixed_return", "cap", "floor", "participation", "buffer",
}

# String/categorical fields left as-is (besides trimming).
_STRING_FIELDS = {
    "underlier", "issuer", "issuer_rating", "observation_freq",
    "barrier_type", "product_type",
}

_PROMPT = """You are extracting parameters from a U.S. structured-product term \
sheet (pricing supplement / 424B2) into strict JSON for an independent pricing \
engine.

First classify the product into exactly one of these types:
  - "autocallable"          (auto-callable / contingent-coupon notes)
  - "reverse_convertible"   (fixed/contingent coupon, downside conversion at maturity, NOT auto-callable)
  - "principal_protected"   (full or partial principal protection + capped upside participation)
  - "barrier_note"          (single fixed/digital return if above a barrier, else principal at risk)
  - "buffered_note"         (leveraged & capped upside with a downside buffer)

Then extract these common fields:
  - underlier:     the underlying ticker symbol (e.g. "AAPL", "SPY"). If the note is linked to a basket / worst-of of several underliers, report the LARGEST-WEIGHT (or first) constituent ticker here ONLY so a refusal message can name it -- DO NOT pretend the note is single-name; the detection fields below are what matter.
  - notional:      denomination / principal amount in dollars (a number, e.g. 100000); use 1000 if the note is sold per $1,000 denomination and no total is given
  - final_valuation_date: the final valuation / observation date as "YYYY-MM-DD" (the date the underlier level is last observed); else null
  - maturity_date: the maturity / principal-repayment / settlement (payment) date as "YYYY-MM-DD" -- this is the date the note pays off, which is usually a few business days AFTER the final valuation date; else null
  - issuer:        issuing entity name
  - issuer_rating: issuer credit rating ONLY if explicitly stated in the document (e.g. "A", "Baa1"); else null. Do not infer a rating.
  - offer_price:   public offering price as a PERCENT of par (e.g. 100 for par); else null

Detection fields (REQUIRED -- these gate whether the product can be priced):
  - num_underlyings:     integer count of distinct underliers the note references (1 for a single-name note)
  - is_basket:           true if the note is linked to a basket / index basket / multiple underliers, else false
  - basket_constituents: list of {"ticker": str, "weight": number} for every underlier in the basket (weight as a fraction or percent as printed); use an empty list [] for a single-name note
  - unsupported_features: list drawn ONLY from this exact vocabulary, including every one that applies, else an empty list []:
        "basket"          -- linked to more than one underlier
        "worst_of"        -- payoff depends on the worst (or best) performing of several underliers
        "geared_downside" -- downside loss is GEARED/LEVERAGED (e.g. a 1.11x or "buffer rate"/"downside leverage factor" > 1.0), so losses exceed 1:1 below the threshold
        "airbag"          -- an "airbag" feature / geared buffer that absorbs an initial loss then gears thereafter
        "range_accrual"   -- coupon accrues based on days the underlier stays in a range
        "dual_directional"-- pays positive return for BOTH up and down moves (a.k.a. twin-win / absolute return)
        "snowball"        -- memory / cumulative ("snowball") coupon that rolls up unpaid coupons

And the type-specific fields (PERCENTS as plain numbers, e.g. 9.5 for 9.5%, 70 for 70%):
  autocallable:        coupon_rate, coupon_barrier, call_barrier, knock_in_barrier, observation_freq ("monthly"|"quarterly"|"semiannual"|"annual")
  reverse_convertible: coupon_rate, barrier, barrier_type ("european"|"american")
  principal_protected: participation, cap, floor (protected principal percent, e.g. 100)
  barrier_note:        fixed_return, barrier, barrier_type ("european"|"american")
  buffered_note:       upside_leverage (a multiple, e.g. 1.5), cap, buffer

Rules:
  - Output ONE JSON object and NOTHING else. No markdown, no commentary.
  - Use null for any field you cannot find or are unsure about. DO NOT GUESS or invent values.
  - Percentages as plain numbers (70 not 0.70). upside_leverage as a multiple (1.5).
  - notional and participation/leverage are amounts/multiples, NOT percents.
  - For the detection fields: report what the document ACTUALLY says. Do not normalize a basket into a single name. unsupported_features must contain ONLY strings from the vocabulary above.
  - Always include "product_type", "num_underlyings", "is_basket", "basket_constituents", and "unsupported_features" keys.

Return only the JSON object."""


class PdfParseError(RuntimeError):
    """Raised when the term sheet cannot be parsed (auth, network, or bad output).

    Carries a user-friendly message. The API key is never included.
    """


class UnsupportedProductError(PdfParseError):
    """Raised when a parsed term sheet describes a product outside Prism's scope.

    Subclasses :class:`PdfParseError` so existing callers that catch
    ``PdfParseError`` keep working -- but the UI should catch
    ``UnsupportedProductError`` FIRST to render the dedicated refusal panel.

    Carries the structured list of refusal reasons on ``.reasons``. The API key
    is NEVER included in the message or the reasons.
    """

    def __init__(self, reasons: list[str]):
        self.reasons = list(reasons or [])
        joined = "; ".join(self.reasons) if self.reasons else "unsupported product"
        super().__init__(
            "Prism can't independently value this note: " + joined
        )


# Supported product types -- the single source of truth for the type gate
# (mirrors prism.__init__._SUPPORTED_TYPES at the dataclass level).
_SUPPORTED_TYPES = frozenset(_FIELDS_BY_TYPE)

# Allowed barrier monitoring conventions.
_SUPPORTED_BARRIER_TYPES = frozenset({"european", "american"})

# Controlled vocabulary -> friendly refusal text. Keys MUST match the prompt's
# ``unsupported_features`` vocabulary exactly.
_FEATURE_REASONS = {
    "basket": "Multi-underlier basket — Prism prices single-underlier notes only",
    "worst_of": "Worst-of / best-of payoff on multiple underliers — not supported",
    "geared_downside": "Geared/leveraged downside (loss > 1:1) — only 1.0× downside is supported",
    "airbag": "Airbag / geared-buffer downside — only a plain buffer or knock-in is supported",
    "range_accrual": "Range-accrual coupon — not supported",
    "dual_directional": "Dual-directional / twin-win payoff — not supported",
    "snowball": "Snowball / memory coupon — not supported",
}

# Bloomberg-style index tickers -> Yahoo Finance symbols (yfinance live mode).
# Equity single names and already-Yahoo / unknown tickers are left untouched.
_BLOOMBERG_TO_YAHOO = {
    "SX5E": "^STOXX50E",
    "NKY": "^N225",
    "UKX": "^FTSE",
    "SMI": "^SSMI",
    "AS51": "^AXJO",
    "SPX": "^GSPC",
    "RTY": "^RUT",
    "NDX": "^NDX",
}


def _normalize_ticker(ticker):
    """Map a Bloomberg index ticker to its Yahoo symbol; pass others through.

    Returns the input unchanged for already-Yahoo or unknown tickers (no guessing).
    """
    if not isinstance(ticker, str):
        return ticker
    return _BLOOMBERG_TO_YAHOO.get(ticker.strip().upper(), ticker.strip())


def check_supported(extracted: dict) -> list[str]:
    """Enforce the §6 support boundary on a raw extracted dict.

    Returns ``[]`` if the product is priceable, otherwise a list of
    human-readable refusal reasons (every reason that applies).

    Tolerant of missing / None detection fields (older extractions): an absent
    flag is treated as "not flagged", EXCEPT that a constituent count > 1 always
    triggers the basket reason.

    The API key is never referenced here.
    """
    if not isinstance(extracted, dict):
        return ["Could not interpret the extracted product data."]

    reasons: list[str] = []

    # --- Basket / multi-underlier detection ---------------------------------
    num = extracted.get("num_underlyings")
    is_basket = extracted.get("is_basket")
    constituents = extracted.get("basket_constituents")
    n_constituents = len(constituents) if isinstance(constituents, (list, tuple)) else 0

    try:
        num_int = int(num) if num is not None else None
    except (TypeError, ValueError):
        num_int = None

    basket_flagged = bool(is_basket) or (num_int is not None and num_int > 1) or n_constituents > 1
    if basket_flagged:
        count = num_int if (num_int is not None and num_int > 1) else max(n_constituents, 2)
        reasons.append(
            f"Multi-underlier basket ({count} underlyings) — "
            "Prism prices single-underlier notes only"
        )

    # --- Controlled-vocabulary unsupported features -------------------------
    features = extracted.get("unsupported_features")
    if isinstance(features, (list, tuple)):
        seen = set()
        for feat in features:
            if not isinstance(feat, str):
                continue
            key = feat.strip().lower()
            if key in seen:
                continue
            seen.add(key)
            if key == "basket":
                # Already covered by the basket detection above (avoid dupes).
                if not basket_flagged:
                    reasons.append(_FEATURE_REASONS["basket"])
            elif key in _FEATURE_REASONS:
                reasons.append(_FEATURE_REASONS[key])
            else:
                reasons.append(f"Unsupported feature: {key}")

    # --- Product-type gate --------------------------------------------------
    ptype = extracted.get("product_type")
    if isinstance(ptype, str):
        ptype = ptype.strip().lower()
    if ptype not in _SUPPORTED_TYPES:
        reasons.append(
            f"Unsupported product type: {ptype!r} — supported types are "
            + ", ".join(sorted(_SUPPORTED_TYPES))
        )

    # --- Barrier-type gate (only when present) ------------------------------
    btype = extracted.get("barrier_type")
    if isinstance(btype, str):
        btype_norm = btype.strip().lower()
        if btype_norm and btype_norm not in _SUPPORTED_BARRIER_TYPES:
            reasons.append(
                f"Unsupported barrier type: {btype_norm!r} — only european / american"
            )

    return reasons


def _build_request(pdf_bytes: bytes) -> dict:
    """Build the Messages API request body (PDF as a base64 document block)."""
    b64 = base64.standard_b64encode(pdf_bytes).decode("ascii")
    return {
        "model": _MODEL,
        "max_tokens": _MAX_TOKENS,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": b64,
                        },
                    },
                    {"type": "text", "text": _PROMPT},
                ],
            }
        ],
    }


def _extract_text(response) -> str:
    """Concatenate the text blocks of a Messages API response."""
    parts = []
    for block in getattr(response, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "".join(parts).strip()


def _extract_json_object(text: str) -> dict:
    """Parse the first JSON object found in ``text`` (robust to stray prose)."""
    text = text.strip()
    # Strip a ```json ... ``` fence if the model added one.
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        # Fall back to the first balanced {...} span.
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise PdfParseError(
                "Could not extract structured data from the document. The model "
                "did not return valid JSON. Please check the PDF is a structured-"
                "product term sheet and try again, or enter the parameters manually."
            )
        try:
            obj = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise PdfParseError(
                "Could not parse the extracted parameters as JSON. Please enter "
                "the parameters manually."
            ) from exc
    if not isinstance(obj, dict):
        raise PdfParseError("Extracted data was not a JSON object.")
    return obj


def _scale_offer_price(value):
    """Offer price: term sheets quote it as a percent of par (100 -> 1.0).

    A value already given as a fraction near par (<= ~2, e.g. 1.0) is treated as
    already a fraction; a value like 100 / 99.5 is divided by 100.
    """
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v / 100.0 if v > 2.0 else v


def _parse_date(value):
    """Parse a date string ("YYYY-MM-DD" preferred) into a datetime.date."""
    if value is None:
        return None
    if isinstance(value, datetime.date):
        return value
    s = str(value).strip()
    if not s:
        return None
    # ISO first.
    try:
        return datetime.date.fromisoformat(s[:10])
    except ValueError:
        pass
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%B %d, %Y", "%b %d, %Y", "%d %B %Y"):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    # Unparseable date -> leave None for the user to fill (do not hallucinate).
    return None


def _coerce(raw: dict, product_type: str) -> dict:
    """Validate and convert raw model values into dataclass-ready Python values.

    Percentages -> fractions; dates -> ``datetime.date``; offer_price normalized;
    strings trimmed. Unknown/null values stay ``None``. Only the keys relevant to
    ``product_type`` (plus the common fields) are returned, alongside
    ``product_type`` itself.
    """
    wanted = set(_FIELDS_BY_TYPE.get(product_type, [])) | {"product_type"}
    out: dict = {"product_type": product_type}
    inferred: list[str] = []

    for key in wanted:
        if key == "product_type":
            continue

        # Maturity is sourced from the split date fields, not a raw "maturity"
        # key. Prefer the true maturity/payment date; fall back to the final
        # valuation date (and record the ambiguity).
        if key == "maturity":
            maturity_raw = raw.get("maturity_date")
            valuation_raw = raw.get("final_valuation_date")
            if maturity_raw is None and valuation_raw is None:
                # Backwards-compat: older extractions used a single "maturity".
                maturity_raw = raw.get("maturity")
            chosen = maturity_raw if maturity_raw is not None else valuation_raw
            if maturity_raw is None and valuation_raw is not None:
                # Only the valuation date was found -- maturity is approximated.
                inferred.append("maturity")
            out[key] = _parse_date(chosen)
            continue

        value = raw.get(key)

        if value is None:
            out[key] = None
            continue

        if key == "underlier":
            out[key] = _normalize_ticker(value)
        elif key == "offer_price":
            out[key] = _scale_offer_price(value)
        elif key == "notional":
            try:
                out[key] = float(value)
            except (TypeError, ValueError):
                out[key] = None
        elif key == "upside_leverage":
            # A multiple, not a percent.
            try:
                out[key] = float(value)
            except (TypeError, ValueError):
                out[key] = None
        elif key in _PERCENT_FIELDS:
            try:
                out[key] = float(value) / 100.0
            except (TypeError, ValueError):
                out[key] = None
        elif key in _STRING_FIELDS:
            out[key] = str(value).strip()
        else:
            out[key] = value

    out["inferred_fields"] = inferred
    return out


def parse_term_sheet(pdf_bytes: bytes, api_key: str) -> dict:
    """Extract structured-product parameters from a term-sheet PDF via Claude.

    Parameters
    ----------
    pdf_bytes : raw bytes of the uploaded PDF term sheet.
    api_key : the user's Anthropic API key (BYOK). Used only to construct the
        client for this call; never stored, logged, or read from the environment.

    Returns
    -------
    dict
        Keys match the relevant product dataclass's fields plus ``product_type``
        (one of "autocallable", "reverse_convertible", "principal_protected",
        "barrier_note", "buffered_note") and ``inferred_fields`` (list of field
        names that were defaulted/approximated rather than read from the sheet).
        Percentages are fractions, ``maturity`` is a :class:`datetime.date`
        (the principal-repayment date, falling back to the final valuation date),
        ``underlier`` is Yahoo-normalized, and fields absent from the sheet are
        ``None``. The basket/feature detection fields used to gate pricing
        (``num_underlyings``, ``is_basket``, ``basket_constituents``,
        ``unsupported_features``) are validated via :func:`check_supported` and
        are NOT included in the returned (priceable) dict.

    Raises
    ------
    UnsupportedProductError
        If the product is outside Prism's scope (basket, geared downside, etc.).
        Subclass of ``PdfParseError`` -- catch it FIRST. Carries ``.reasons``.
    PdfParseError
        On authentication failure, network/API error, or unparseable output.
    """
    if not api_key:
        raise PdfParseError("An Anthropic API key is required to parse a PDF.")
    if not pdf_bytes:
        raise PdfParseError("No PDF content was provided.")

    request = _build_request(pdf_bytes)

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(**request)
    except anthropic.AuthenticationError as exc:
        # Do NOT echo the key.
        raise PdfParseError(
            "Authentication failed: the Anthropic API key was rejected. Please "
            "check your key in Settings and try again."
        ) from exc
    except anthropic.RateLimitError as exc:
        raise PdfParseError(
            "Anthropic rate limit reached. Please wait a moment and try again."
        ) from exc
    except anthropic.APIError as exc:
        raise PdfParseError(
            "The Anthropic API request failed while parsing the PDF. Please try "
            "again or enter the parameters manually."
        ) from exc
    except Exception as exc:  # noqa: BLE001 - network/SDK errors -> friendly msg
        raise PdfParseError(
            "Could not reach the Anthropic API. Check your connection and try again."
        ) from exc

    text = _extract_text(response)
    if not text:
        raise PdfParseError(
            "The model returned an empty response. Please try again or enter the "
            "parameters manually."
        )

    raw = _extract_json_object(text)

    product_type = raw.get("product_type")
    if isinstance(product_type, str):
        product_type = product_type.strip().lower()
    if product_type not in _FIELDS_BY_TYPE:
        raise PdfParseError(
            "Could not determine the product type from the document. Please "
            "select the product type and enter the parameters manually."
        )

    # Refuse out-of-scope products at the parse boundary (§6). This MUST happen
    # before _coerce / dataclass mapping, which cannot represent basket or
    # geared-downside features. The raw dict carries the detection fields.
    reasons = check_supported(raw)
    if reasons:
        raise UnsupportedProductError(reasons=reasons)

    return _coerce(raw, product_type)
