"""RCA PRISM-RCA-002 §7E (UI smoke) — FRED key / low-confidence-curve surface.

Headless Streamlit ``AppTest`` checks for the in-app BYOK FRED key + graceful
static-curve notice. No network, no real keys, no token spend: ``prism.price_product``
is patched to a stub returning a REAL ``DecompositionResult``.

Behaviour verified:
* Demo OFF + no FRED key -> the live pricing call is made with
  ``fred_api_key=None``, the **low-confidence-curve notice renders** (an
  ``st.warning``, NOT an ``st.error``/exception). This proves the old hard-fail
  ("Could not fetch market data: FRED_API_KEY is not set…") is gone.
* Entering a FRED key flips the live call to forward ``fred_api_key=<key>`` and,
  with a keyed (``low_confidence_curve=False``) result, the notice is absent.
* The FRED field and the Anthropic field are distinct password widgets; the FRED
  key value never appears in any rendered text.

AppTest patching pattern (per UI_NOTES.md): AppTest re-execs app.py on every
``run()``; app.py binds ``from prism import price_product`` at exec time, so we
patch ``prism.price_product`` (and clear ``st.cache_data``) BEFORE each fresh
``AppTest.from_file(...).run()`` so the re-exec picks up the stub.

Coverage gaps (documented, not failures):
* AppTest cannot drive the ``st.file_uploader`` widget, so the PDF upload path is
  out of scope here (covered by the parser/refusal suites).
* The ``st.cache_data`` wrapper serializes its return value, so the stub must
  return a real ``DecompositionResult`` (not an arbitrary fake) to exercise the
  cached live path.
"""
from __future__ import annotations

import os

import pytest

import prism
import streamlit as st
from prism.models import DecompositionResult
from streamlit.testing.v1 import AppTest

APP = "app.py"
TIMEOUT = 60

_TEXTINPUT_PASSWORD = 1  # streamlit TextInput.Type.PASSWORD proto enum value
FRED_KEY = "FRED-UI-SECRET-DO-NOT-LEAK-777"


def _make_result(low_confidence_curve: bool) -> DecompositionResult:
    """A real, serializable DecompositionResult (the cache wrapper requires it)."""
    return DecompositionResult(
        bond_floor=60_000.0,
        option_value=35_000.0,
        fair_value=95_000.0,
        offer_price_dollars=100_000.0,
        embedded_margin=5_000.0,
        margin_pct=5.0,
        greeks={"delta": 0.5, "vega": 100.0, "rho": 50.0},
        prob_loss=0.1,
        return_distribution=[0.0, 0.1, -0.1],
        payoff_curve=[(-50.0, -50.0), (0.0, 5.0), (50.0, 25.0)],
        spot=100.0,
        risk_free=0.04,
        credit_spread=0.009,
        div_yield=0.0,
        atm_vol=0.2,
        low_confidence_vol=False,
        low_confidence_curve=low_confidence_curve,
        notes=[],
    )


@pytest.fixture
def patched_app(monkeypatch):
    """Patch prism.price_product with a capturing stub + clear the cache.

    Returns ``(make_at, captured)``. Call ``make_at(low_conf=...)`` to build a
    freshly-run AppTest whose live pricing returns a result with the given
    ``low_confidence_curve`` flag; ``captured`` records the last call's kwargs.
    """
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    captured: dict = {}

    state = {"low_conf": True}

    def _stub(product, **kwargs):
        captured.clear()
        captured.update(kwargs)
        return _make_result(state["low_conf"])

    monkeypatch.setattr(prism, "price_product", _stub)

    def make_at(low_conf: bool):
        state["low_conf"] = low_conf
        st.cache_data.clear()  # ensure the stub is actually invoked, not cached
        at = AppTest.from_file(APP, default_timeout=TIMEOUT)
        at.run()
        return at

    return make_at, captured


def _warnings(at):
    return [str(w.value) for w in at.warning]


def _all_text(at):
    chunks = []
    for kind in ("markdown", "info", "success", "warning", "error", "caption",
                 "subheader", "title", "header"):
        for el in getattr(at, kind, []):
            chunks.append(str(getattr(el, "value", "")))
            chunks.append(str(getattr(el, "label", "")))
    return "\n".join(chunks)


def _click_analyze(at):
    for b in at.button:
        if "Analyze" in (b.label or ""):
            b.click().run()
            return
    raise AssertionError("Analyze button not found")


def _fred_field(at):
    for t in at.text_input:
        if t.label == "FRED API key":
            return t
    raise AssertionError("FRED API key field not found")


# ===========================================================================
# Demo OFF + no FRED key -> low-confidence notice, NO hard error.
# ===========================================================================
def test_demo_off_no_key_renders_low_confidence_notice(patched_app):
    make_at, captured = patched_app
    at = make_at(low_conf=True)
    assert not at.exception, f"boot raised: {at.exception}"

    # Flip demo OFF (the only checkbox) -> live mode.
    at.checkbox[0].set_value(False).run()
    assert not at.exception, f"toggling demo raised: {at.exception}"

    _click_analyze(at)
    assert not at.exception, f"Analyze in live mode raised: {at.exception}"

    # The low-confidence-curve notice is an st.warning (NOT an error/exception).
    warns = _warnings(at)
    assert any("Low-confidence Treasury" in w for w in warns), (
        f"missing low-confidence-curve warning; warnings={warns}"
    )
    assert not at.error, f"live no-key pricing must not error: {[e.value for e in at.error]}"

    # The live call was made with no FRED key (None) — env-or-fallback path.
    assert captured.get("fred_api_key") is None, captured


def test_demo_off_no_key_does_not_show_old_hardfail(patched_app):
    """The pre-RCA hard-fail message must never appear (degrades gracefully)."""
    make_at, _ = patched_app
    at = make_at(low_conf=True)
    at.checkbox[0].set_value(False).run()
    _click_analyze(at)
    text = _all_text(at)
    assert "FRED_API_KEY is not set" not in text
    assert "Could not fetch market data" not in text


# ===========================================================================
# Entering a FRED key -> live call forwards it, notice absent.
# ===========================================================================
def test_entering_fred_key_forwards_it_and_hides_notice(patched_app):
    make_at, captured = patched_app
    at = make_at(low_conf=False)  # keyed result: live curve, no low-confidence

    at.checkbox[0].set_value(False).run()         # live mode
    _fred_field(at).set_value(FRED_KEY).run()      # enter the FRED key
    _click_analyze(at)
    assert not at.exception, f"Analyze with a FRED key raised: {at.exception}"

    # The entered key is forwarded to the live pricing call.
    assert captured.get("fred_api_key") == FRED_KEY, captured
    # With a keyed (non-low-confidence) result, the notice is absent.
    assert not any("Low-confidence Treasury" in w for w in _warnings(at))


def test_fred_key_never_appears_in_rendered_text(patched_app):
    make_at, _ = patched_app
    at = make_at(low_conf=False)
    at.checkbox[0].set_value(False).run()
    _fred_field(at).set_value(FRED_KEY).run()
    _click_analyze(at)
    assert FRED_KEY not in _all_text(at), "FRED key leaked into rendered UI text"


# ===========================================================================
# Distinct FRED vs Anthropic password fields (§7B / §9).
# ===========================================================================
def test_fred_and_anthropic_fields_are_distinct_password_inputs(patched_app):
    make_at, _ = patched_app
    at = make_at(low_conf=True)

    labels = [t.label for t in at.text_input]
    assert labels.count("FRED API key") == 1, "exactly one FRED field expected"
    assert labels.count("Anthropic API key") == 1, "exactly one Anthropic field"

    fred = _fred_field(at)
    anthropic = [t for t in at.text_input if t.label == "Anthropic API key"][0]
    # Distinct widgets, both rendered as password fields (proto type == PASSWORD).
    assert fred is not anthropic
    assert fred.proto.type == _TEXTINPUT_PASSWORD, "FRED field must be a password input"
    assert anthropic.proto.type == _TEXTINPUT_PASSWORD, "Anthropic field must be a password input"


# ===========================================================================
# Demo mode unchanged: still forwards demo market, no fred_api_key.
# ===========================================================================
def test_demo_mode_does_not_forward_fred_key(patched_app):
    make_at, captured = patched_app
    at = make_at(low_conf=False)
    # Demo stays ON (default) -> demo branch; no fred_api_key kwarg.
    _click_analyze(at)
    assert not at.exception
    assert "fred_api_key" not in captured, (
        f"demo mode should not pass fred_api_key; got {captured}"
    )
    assert not any("Low-confidence Treasury" in w for w in _warnings(at))
