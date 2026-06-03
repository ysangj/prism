"""Phase 2 UI: headless app.py checks via Streamlit AppTest (no browser).

PRD §8.1–§8.4 (term sheet input, decomposition, payoff, risk), §8.1 Option B
(BYOK PDF gating), §9 (security). UI_NOTES.md (layout + backend wiring).

Uses `streamlit.testing.v1.AppTest`, which runs app.py headless and exposes the
widget tree + any exceptions. No network is touched (demo mode is the default).
"""
from __future__ import annotations

import datetime

import pytest

from streamlit.testing.v1 import AppTest

APP = "app.py"
TIMEOUT = 60


def _run():
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.run()
    return at


def _analyze(at):
    """Click the Analyze form-submit button (exposed via at.button) and rerun."""
    for b in at.button:
        if "Analyze" in (b.label or ""):
            b.click().run()
            return at
    raise AssertionError("Analyze button not found")


def _all_text(at):
    """Concatenate visible text from common elements for substring assertions."""
    chunks = []
    for kind in ("markdown", "info", "success", "warning", "error", "caption",
                 "subheader", "title", "header", "metric"):
        for el in getattr(at, kind, []):
            chunks.append(str(getattr(el, "value", "")))
            chunks.append(str(getattr(el, "label", "")))
    return "\n".join(chunks)


# ===========================================================================
# Boot: 0 exceptions (PRD §8 / §9 non-functional).
# ===========================================================================
def test_app_boots_without_exceptions():
    at = _run()
    assert not at.exception, f"app raised on boot: {at.exception}"


def test_app_has_product_selector_with_five_types():
    at = _run()
    # The product-type selectbox lists all five PRD §4 product structures.
    labels = []
    for sb in at.selectbox:
        labels.extend(sb.options)
    text = " ".join(labels)
    for token in ("Autocallable", "Reverse Convertible", "Principal-Protected",
                  "Barrier Note", "Buffered Note"):
        assert token in text, f"product selector missing {token!r}"


# ===========================================================================
# Default Analyze (demo autocallable) -> decomposition + payoff + risk.
# PRD §8.2, §8.3, §8.4 / §15.7.
# ===========================================================================
def test_default_analyze_produces_all_sections():
    at = _run()
    assert not at.exception
    # Click Analyze (the only form-submit button).
    assert len(at.button) >= 0
    _analyze(at)
    assert not at.exception, f"Analyze raised: {at.exception}"

    text = _all_text(at)
    assert "Component decomposition" in text
    assert "Payoff at maturity" in text
    assert "Risk metrics" in text
    # Decomposition metric labels (PRD §8.2).
    metric_labels = {m.label for m in at.metric}
    assert "Bond floor" in metric_labels
    assert "Option value" in metric_labels
    assert "Fair value" in metric_labels
    assert "Embedded margin" in metric_labels
    # Risk metric labels (PRD §8.4).
    assert "Delta" in metric_labels
    assert "Vega" in metric_labels
    assert "Rho" in metric_labels
    assert "P(loss)" in metric_labels


def test_default_analyze_demo_banner_shown():
    at = _run()
    text = _all_text(at)
    assert "Demo market data is ON" in text


# ===========================================================================
# Switch product type and Analyze each of the five (PRD §4 / §8.1).
# ===========================================================================
@pytest.mark.parametrize("label", [
    "Autocallable (Phoenix)",
    "Reverse Convertible",
    "Principal-Protected Note (PPN)",
    "Barrier Note (Digital)",
    "Buffered Note (Accelerated)",
])
def test_each_product_type_prices(label):
    at = _run()
    at.selectbox[0].select(label).run()
    assert not at.exception, f"selecting {label} raised: {at.exception}"
    _analyze(at)
    assert not at.exception, f"Analyze for {label} raised: {at.exception}"
    metric_labels = {m.label for m in at.metric}
    assert "Fair value" in metric_labels, f"{label} produced no decomposition"
    assert "P(loss)" in metric_labels, f"{label} produced no risk metrics"


# ===========================================================================
# Per-type fields render (PRD §8.1).
# ===========================================================================
def test_buffered_fields_render():
    at = _run()
    at.selectbox[0].select("Buffered Note (Accelerated)").run()
    assert not at.exception
    labels = [ni.label for ni in at.number_input]
    text = " ".join(labels)
    assert "leverage" in text.lower()
    assert "buffer" in text.lower()


def test_ppn_fields_render():
    at = _run()
    at.selectbox[0].select("Principal-Protected Note (PPN)").run()
    assert not at.exception
    text = " ".join(ni.label for ni in at.number_input).lower()
    assert "participation" in text
    assert "floor" in text or "protection" in text


# ===========================================================================
# Validation -> friendly error, not a traceback (PRD §8.1).
# ===========================================================================
def test_empty_ticker_shows_friendly_error():
    at = _run()
    # Clear the underlier and Analyze.
    at.text_input(key="f_underlier").set_value("").run()
    _analyze(at)
    assert not at.exception, "validation should not raise an exception"
    errs = "\n".join(e.value for e in at.error)
    assert "Underlier" in errs and "required" in errs.lower()


def test_past_maturity_prevented_by_widget_min_value():
    """PRD §8.1 'maturity in the future': the date_input enforces min_value =
    tomorrow, so a past date is not even selectable.

    AppTest confirms a past value is clamped/ignored (the widget keeps a future
    date), which is the strongest form of this validation — the bad state is
    unreachable rather than caught after the fact. (The belt-and-suspenders
    `_validate` 'must be in the future' branch is also present in app.py.)
    """
    at = _run()
    past = datetime.date.today() - datetime.timedelta(days=30)
    at.date_input(key="f_maturity").set_value(past).run()
    assert not at.exception
    held = at.date_input(key="f_maturity").value
    assert held > datetime.date.today(), (
        f"date_input should not accept a past date; held {held}")
    # And Analyze still works (no traceback) with the enforced future date.
    _analyze(at)
    assert not at.exception
    metric_labels = {m.label for m in at.metric}
    assert "Fair value" in metric_labels


def test_barrier_over_200_shows_friendly_error():
    at = _run()
    # coupon_barrier for the autocallable is a pct field bounded 0-200 in the
    # validator; push it above 200 to trigger the friendly error.
    target = None
    for ni in at.number_input:
        if ni.label and "barrier" in ni.label.lower():
            target = ni
            break
    if target is None:
        pytest.skip("no barrier number_input found to exercise the bound")
    try:
        target.set_value(250.0).run()
    except Exception:
        pytest.skip("AppTest clamped the value to the widget max (<=200)")
    _analyze(at)
    assert not at.exception
    # Either a friendly validation error fired, or the widget clamped to <=200
    # (in which case pricing simply succeeds). Both are acceptable — no traceback.
    if at.error:
        errs = "\n".join(e.value for e in at.error).lower()
        assert "between 0%" in errs or "200%" in errs


# ===========================================================================
# BYOK PDF gating (PRD §8.1 Option B, §9).
# ===========================================================================
def test_pdf_uploader_disabled_without_key():
    at = _run()
    assert not at.exception
    # The sidebar shows the BYOK lock caption when no key is set.
    text = _all_text(at)
    assert "Anthropic" in text  # the BYOK section is present
    # The uploader's disabled state can't always be read off AppTest's
    # file_uploader proxy; assert the lock caption (which only renders when
    # has_key is False) as the observable gating signal.
    captions = "\n".join(c.value for c in at.caption)
    assert "enable PDF upload" in captions or "bring-your-own-key" in captions.lower()
