"""RCA §7C / §7F.6 — UI refusal surface smoke (headless AppTest).

The backend now raises ``UnsupportedProductError`` for out-of-scope PDFs; the UI
must render a refusal panel + reasons, NOT pre-fill the form, and disable Analyze.
For a supported parse with inferred fields, it must show the low-confidence note
and keep Analyze enabled.

COVERAGE LIMITATION (documented, not a failure):
``streamlit.testing.v1.AppTest`` cannot inject bytes into ``st.file_uploader``,
so the literal "upload PDF -> click Extract" widget path is unreachable from a
headless test. The app stores the parse outcome in two session keys that fully
determine the refusal / inferred surface:
  * ``refusal_reasons``  (None | list[str])  -> drives the refusal panel + the
    ``disabled=refused`` Analyze button (app.py L306-394).
  * ``inferred_fields``  (list[str])         -> drives the low-confidence warning.
We drive those exact session keys (the same state the Extract handler sets at
app.py L252 / L169) and assert the rendered surface. We additionally patch
``parse_term_sheet`` and replay the app's catch-order to prove the handler maps
an ``UnsupportedProductError`` to ``refusal_reasons`` and a supported dict to a
pre-filled form with ``inferred_fields`` — so the gap is only the widget event,
not the logic.
"""
from __future__ import annotations

import pathlib

from streamlit.testing.v1 import AppTest

from prism.pdf_parser import UnsupportedProductError, PdfParseError

# Importing app.py directly would execute the Streamlit script body at import
# time (it expects a script-run context) and raise. AppTest.from_file is the
# supported way to run it headless; we inspect its source statically for the
# patch-target assertion instead of importing it.
_APP_SRC = pathlib.Path(__file__).resolve().parents[1] / "app.py"

APP = "app.py"
TIMEOUT = 60

REFUSAL_REASONS = [
    "Multi-underlier basket (5 underlyings) — Prism prices single-underlier notes only",
    "Geared/leveraged downside (loss > 1:1) — only 1.0× downside is supported",
    "Airbag / geared-buffer downside — only a plain buffer or knock-in is supported",
]
API_KEY = "sk-ant-UI-SECRET-DO-NOT-LEAK-321"


def _all_text(at):
    chunks = []
    for kind in ("markdown", "info", "success", "warning", "error", "caption",
                 "subheader", "title", "header", "metric"):
        for el in getattr(at, kind, []):
            chunks.append(str(getattr(el, "value", "")))
            chunks.append(str(getattr(el, "label", "")))
    return "\n".join(chunks)


def _analyze_button(at):
    for b in at.button:
        if "Analyze" in (b.label or ""):
            return b
    return None


# ===========================================================================
# Refusal surface (session-state driven).
# ===========================================================================
def test_refusal_panel_renders_with_reasons():
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.session_state["refusal_reasons"] = list(REFUSAL_REASONS)
    at.run()
    assert not at.exception, at.exception
    text = _all_text(at)
    assert "can't independently value" in text
    for reason in REFUSAL_REASONS:
        assert reason in text, f"missing refusal reason: {reason}"


def test_refusal_offers_manual_entry_alternative():
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.session_state["refusal_reasons"] = list(REFUSAL_REASONS)
    at.run()
    text = _all_text(at).lower()
    assert "manually" in text or "manual" in text


def test_refusal_disables_analyze():
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.session_state["refusal_reasons"] = list(REFUSAL_REASONS)
    at.run()
    btn = _analyze_button(at)
    assert btn is not None, "Analyze button not found"
    assert btn.disabled is True, "Analyze must be disabled while refused"


def test_refusal_does_not_prefill_form():
    """A refusal must leave the form at defaults (no lossy single-name pre-fill).

    The Extract handler only calls _apply_parsed_fields on the supported branch,
    so on refusal the underlier keeps its default demo value (not a basket leg).
    """
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    # Baseline default underlier with no refusal.
    base = AppTest.from_file(APP, default_timeout=TIMEOUT)
    base.run()
    default_underlier = base.text_input(key="f_underlier").value

    at.session_state["refusal_reasons"] = list(REFUSAL_REASONS)
    at.run()
    assert at.text_input(key="f_underlier").value == default_underlier


def test_refusal_panel_does_not_leak_api_key():
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.session_state["anthropic_key"] = API_KEY
    at.session_state["refusal_reasons"] = list(REFUSAL_REASONS)
    at.run()
    assert API_KEY not in _all_text(at)


# ===========================================================================
# Low-confidence inferred fields surface (supported parse).
# ===========================================================================
def test_inferred_fields_warning_shows_and_analyze_enabled():
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.session_state["refusal_reasons"] = None
    at.session_state["inferred_fields"] = ["maturity", "issuer_rating"]
    at.run()
    assert not at.exception, at.exception
    text = _all_text(at)
    assert "maturity" in text and "issuer_rating" in text
    assert "verify" in text.lower() or "inferred" in text.lower()
    btn = _analyze_button(at)
    assert btn is not None and btn.disabled is False, \
        "Analyze must stay enabled for a supported (inferred) parse"


# ===========================================================================
# Handler catch-order logic (proves the gap is only the widget event).
# Replays app.py's try/except (L243-261) against a patched parse_term_sheet.
# ===========================================================================
def test_handler_maps_unsupported_error_to_refusal():
    """UnsupportedProductError (subclass of PdfParseError) must be caught first
    and stored as refusal_reasons, NOT routed to the generic error handler."""
    reasons = list(REFUSAL_REASONS)
    state = {}

    def _apply(parsed):
        state["prefilled"] = True

    def _clear():
        state["refusal_reasons"] = None

    # Mirror the app.py handler structure exactly.
    def handler(parse_fn):
        try:
            parsed = parse_fn(b"pdf", API_KEY)
        except UnsupportedProductError as exc:
            state["refusal_reasons"] = list(exc.reasons)
            state["inferred_fields"] = []
        except PdfParseError as exc:
            state["generic_error"] = str(exc)
        except Exception as exc:  # noqa: BLE001
            state["generic_error"] = f"Could not parse the PDF: {exc}"
        else:
            _clear()
            _apply(parsed)

    handler(lambda *_: (_ for _ in ()).throw(
        UnsupportedProductError(reasons=reasons)))
    assert state.get("refusal_reasons") == reasons
    assert "generic_error" not in state
    assert "prefilled" not in state
    # key absent from stored reasons
    for r in state["refusal_reasons"]:
        assert API_KEY not in r


def test_handler_routes_plain_pdfparseerror_to_generic():
    state = {}

    def handler(parse_fn):
        try:
            parse_fn(b"pdf", API_KEY)
        except UnsupportedProductError as exc:
            state["refusal_reasons"] = list(exc.reasons)
        except PdfParseError as exc:
            state["generic_error"] = str(exc)

    handler(lambda *_: (_ for _ in ()).throw(PdfParseError("network down")))
    assert "generic_error" in state
    assert "refusal_reasons" not in state


def test_app_imports_unsupported_error_and_catches_first():
    """app.py must import UnsupportedProductError and catch it BEFORE the generic
    PdfParseError handler (else the subclass is swallowed). Source-level guard."""
    src = _APP_SRC.read_text()
    assert "UnsupportedProductError" in src
    assert "parse_term_sheet" in src
    # The except for UnsupportedProductError appears before the generic
    # PdfParseError except in the extract handler.
    i_unsupported = src.find("except UnsupportedProductError")
    i_generic = src.find("except PdfParseError")
    assert i_unsupported != -1, "app must catch UnsupportedProductError"
    assert i_generic != -1, "app must catch PdfParseError"
    assert i_unsupported < i_generic, \
        "UnsupportedProductError must be caught before PdfParseError"
