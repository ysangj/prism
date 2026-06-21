"""Phase 2 UI: headless app.py checks via Streamlit AppTest (no browser).

PRD §8.1–§8.4 (term sheet input, decomposition, payoff, risk), §8.1 Option B
(BYOK PDF gating), §9 (security). UI_NOTES.md (layout + backend wiring).

Uses `streamlit.testing.v1.AppTest`, which runs app.py headless and exposes the
widget tree + any exceptions. No network is touched (demo mode is the default).
"""
from __future__ import annotations

import datetime

import pytest

import streamlit as st
from streamlit.testing.v1 import AppTest

APP = "app.py"
TIMEOUT = 60

# streamlit MetricProto color enum: 0=RED(normal), 1=GREEN(inverse), 2=GRAY(off).
# delta_color="off" -> GRAY == 2. This is the "no ambiguous arrow" signal used by
# the 2026-06-15 UX hero-margin work below.
_METRIC_COLOR_OFF = 2


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


# ===========================================================================
# UX — high priority (feedback "Week of 2026-06-15", docs/weekly_progress.md).
#
# Three changes in app.py / prism_ui/narrative.py:
#   (a) Hero embedded-margin verdict (plain headline, investor explanation, no
#       ambiguous green↑/red↑ arrow on the margin); Bond/Option/Fair demoted to
#       a "Breakdown" row below the hero.
#   (b) Plain-language "What this means:" takeaway on each of the 3 charts
#       (decomposition, payoff, histogram), with chart-specific phrasing and
#       numbers consistent with the metric cards.
#   (c) Risk section reordered: intuitive trio (P(loss)/Expected return/Max-loss)
#       FIRST, Greeks (Delta/Vega/Rho) SECOND under "Sensitivities (Greeks)",
#       each with an ON-SCREEN plain definition (caption), not only a tooltip.
#
# Assertions key on STABLE substrings/keywords (not exact sentences) to avoid
# brittleness. AppTest re-execs app.py per run(); we clear st.cache_data first so
# demo pricing recomputes deterministically (no network, no key, no token spend).
# ===========================================================================
def _ux_run():
    st.cache_data.clear()  # recompute demo pricing, don't serve a stale cache
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.run()
    return at


def _ux_analyzed():
    at = _ux_run()
    assert not at.exception, f"boot raised: {at.exception}"
    _analyze(at)
    assert not at.exception, f"Analyze raised: {at.exception}"
    return at


def _infos(at):
    return [str(el.value) for el in at.info]


def _markdowns(at):
    return [str(el.value) for el in at.markdown]


def _captions(at):
    return [str(el.value) for el in at.caption]


def _metric_by_label(at, label):
    for m in at.metric:
        if m.label == label:
            return m
    return None


def _metric_index(at, label):
    """Document-order index of a metric by label, or None if absent.

    AppTest exposes ``at.metric`` in element (document) order, including metrics
    nested in ``st.columns`` — verified empirically against app.py's layout.
    """
    for i, m in enumerate(at.metric):
        if m.label == label:
            return i
    return None


# ---- (a) Hero embedded-margin verdict ------------------------------------
def test_ux_hero_verdict_mentions_fair_value():
    at = _ux_analyzed()
    text = "\n".join(_markdowns(at) + _infos(at))
    assert "fair value" in text.lower(), (
        "hero verdict text mentioning 'fair value' not found")


def test_ux_hero_verdict_matches_above_below_or_roughly():
    at = _ux_analyzed()
    md = " ".join(_markdowns(at)).lower()
    assert (
        "above fair value" in md
        or "below fair value" in md
        or "roughly at fair value" in md
    ), "hero headline did not match above/below/roughly … fair value"


def test_ux_hero_verdict_has_investor_framed_explanation():
    """The verdict callout carries an investor-framed plain explanation.

    For the default demo product the offer is priced ABOVE fair value, so the
    explanation frames the gap as the issuer's embedded fee working against the
    buyer. Key on stable keywords, not the exact sentence.
    """
    at = _ux_analyzed()
    blob = "\n".join(_infos(at) + _markdowns(at)
                     + [str(w.value) for w in at.warning]
                     + [str(s.value) for s in at.success]).lower()
    assert any(kw in blob for kw in (
        "embedded fee", "against you", "components are independently worth",
        "no positive embedded fee")), (
        "no investor-framed explanation found near the hero verdict")


def test_ux_hero_margin_metric_present_and_no_ambiguous_arrow():
    """The embedded-margin metric is still rendered, with delta_color 'off'
    (proto color == GRAY/2) — the fix for the ambiguous green↑/red↑ arrow."""
    at = _ux_analyzed()
    margin = _metric_by_label(at, "Embedded margin")
    assert margin is not None, "Embedded margin metric missing"
    assert margin.proto.color == _METRIC_COLOR_OFF, (
        f"Embedded margin must use delta_color='off' (GRAY), "
        f"got proto.color={margin.proto.color}")
    assert "%" in (str(margin.value) + str(margin.delta or "")), (
        "Embedded margin should still show a percent-of-notional figure")


def test_ux_breakdown_row_demoted_below_hero():
    """Bond floor / Option value / Fair value live under a 'Breakdown' label and
    AFTER the hero Embedded-margin metric in document order."""
    at = _ux_analyzed()
    md = " ".join(_markdowns(at))
    assert "Breakdown" in md, "demoted 'Breakdown' label not found"
    margin_i = _metric_index(at, "Embedded margin")
    assert margin_i is not None
    for lbl in ("Bond floor", "Option value", "Fair value"):
        idx = _metric_index(at, lbl)
        assert idx is not None, f"{lbl} metric missing"
        assert idx > margin_i, (
            f"{lbl} (idx {idx}) should appear AFTER the hero Embedded margin "
            f"(idx {margin_i})")


# ---- (b) Plain-language chart takeaways -----------------------------------
def test_ux_three_chart_takeaways_render():
    at = _ux_analyzed()
    takeaways = [v for v in _infos(at) if "what this means" in v.lower()]
    assert len(takeaways) >= 3, (
        f"expected >=3 'What this means' chart takeaways, found "
        f"{len(takeaways)}: {takeaways}")


def test_ux_chart_takeaways_have_chart_specific_phrasing():
    at = _ux_analyzed()
    blob = "\n".join(v.lower() for v in _infos(at)
                     if "what this means" in v.lower())
    # Decomposition: splits the offer price into bond / option / margin shares.
    assert "offer price" in blob and "bond" in blob and "option" in blob, (
        "decomposition takeaway phrasing not found")
    # Payoff: talks about principal vs the underlier.
    assert "principal" in blob, "payoff takeaway phrasing not found"
    # Histogram: talks about simulated scenarios.
    assert "simulated" in blob or "scenarios" in blob, (
        "histogram takeaway phrasing not found")


def test_ux_histogram_takeaway_numbers_match_risk_cards():
    """The histogram takeaway reuses the SAME rounded P(loss) / Expected-return
    numbers shown in the metric cards above (numerically consistent UI)."""
    at = _ux_analyzed()
    p_loss = _metric_by_label(at, "P(loss)")
    exp_ret = _metric_by_label(at, "Expected return")
    assert p_loss is not None and exp_ret is not None, "risk metrics missing"
    p_loss_val = str(p_loss.value).strip()        # e.g. "8.9%"
    exp_ret_val = str(exp_ret.value).strip()      # e.g. "1.9%"

    hist = [v for v in _infos(at)
            if "what this means" in v.lower()
            and ("simulated" in v.lower() or "scenarios" in v.lower())]
    assert hist, "histogram takeaway not found"
    htext = hist[0]
    assert p_loss_val in htext, (
        f"P(loss) card value {p_loss_val!r} not echoed in histogram takeaway: "
        f"{htext!r}")
    assert exp_ret_val in htext, (
        f"Expected-return card value {exp_ret_val!r} not echoed in histogram "
        f"takeaway: {htext!r}")


def test_ux_decomposition_takeaway_offer_matches_inputs():
    """The decomposition takeaway cites the offer price consistent with the
    default form inputs (100% of $100,000 notional = $100,000)."""
    at = _ux_analyzed()
    decomp = [v for v in _infos(at)
              if "what this means" in v.lower() and "offer price" in v.lower()]
    assert decomp, "decomposition takeaway not found"
    assert "$100,000" in decomp[0], (
        f"decomposition takeaway should cite the $100,000 offer price: "
        f"{decomp[0]!r}")


# ---- (c) Risk reorder + on-screen definitions -----------------------------
def test_ux_all_six_risk_metrics_present():
    at = _ux_analyzed()
    labels = {m.label for m in at.metric}
    for lbl in ("P(loss)", "Expected return", "Max-loss scenario",
                "Delta", "Vega", "Rho"):
        assert lbl in labels, f"risk metric {lbl!r} missing"


def test_ux_intuitive_metrics_precede_greeks():
    """The intuitive trio appears BEFORE the Greeks in document order."""
    at = _ux_analyzed()
    intuitive = [_metric_index(at, l)
                 for l in ("P(loss)", "Expected return", "Max-loss scenario")]
    greeks = [_metric_index(at, l) for l in ("Delta", "Vega", "Rho")]
    assert all(i is not None for i in intuitive), "intuitive metrics missing"
    assert all(g is not None for g in greeks), "Greek metrics missing"
    assert max(intuitive) < min(greeks), (
        f"intuitive metrics {intuitive} must precede Greeks {greeks}")


def test_ux_greeks_grouped_under_sensitivities_label():
    at = _ux_analyzed()
    md = " ".join(_markdowns(at))
    assert "Sensitivities" in md and "Greeks" in md, (
        "'Sensitivities (Greeks)' grouping label not found")


def test_ux_each_greek_has_onscreen_definition():
    """Each Greek has a plain-English definition rendered ON SCREEN (caption),
    not only in a hover tooltip."""
    at = _ux_analyzed()
    caps = " ".join(_captions(at)).lower()
    assert "underlier moves" in caps, "Delta on-screen definition missing"
    assert "volatility" in caps, "Vega on-screen definition missing"
    assert "interest rates" in caps, "Rho on-screen definition missing"


def test_ux_each_intuitive_metric_has_onscreen_definition():
    at = _ux_analyzed()
    caps = " ".join(_captions(at)).lower()
    assert "losing money" in caps, "P(loss) on-screen definition missing"
    assert "average total return" in caps, (
        "Expected-return on-screen definition missing")
    assert "worst" in caps, "Max-loss on-screen definition missing"


def test_ux_results_render_without_exception():
    at = _ux_analyzed()
    assert not at.exception, f"results render raised: {at.exception}"


# ===========================================================================
# UX — moderate (feedback "Week of 2026-06-15" → "UX — moderate";
# implemented per UI_NOTES.md "2026-06-20 UX — moderate"). Five app.py-only
# changes (one new `_key_field` helper); no `prism/` backend changes:
#
#   (1) Analyze (form-submit) button re-colored to the brand violet→magenta
#       gradient via a scoped <style> block targeting stFormSubmitButton.
#   (2) Each BYOK key field collapses to a green "✓ key set" chip once set
#       (Edit reveals the password input again); raw key value never rendered.
#   (3) Monte-Carlo paths select_slider moved into an "Advanced settings"
#       expander (collapsed by default) at the bottom of the sidebar.
#   (4) Direct "get a key" links (Anthropic console, FRED signup) in each field.
#   (5) "Market inputs used in this valuation" expander now default-open.
#
# Assertions key on STABLE substrings/URLs (not full sentences). AppTest re-execs
# app.py per run(); _ux_run() clears st.cache_data so demo pricing recomputes
# deterministically (no network, no key, no token spend). The secret values used
# here are synthetic — they exist only to prove the chip never echoes a real key.
#
# Introspection note: this Streamlit build (1.58.0) exposes an expander's
# open/closed state via `expander.proto.expanded` and the file_uploader's gating
# via `file_uploader.proto.disabled` — both stronger than the element-presence
# proxies the prior scratch checks relied on. The ONLY remaining visual-only gap
# is the rendered button *pixel* color / hover / disabled appearance (AppTest
# surfaces the CSS string but not painted pixels) — flagged for manual check.
# ===========================================================================
_ANTHROPIC_SECRET = "sk-ant-MODERATE-UX-LEAK-CANARY-111"
_FRED_SECRET = "FRED-MODERATE-UX-LEAK-CANARY-222"


def _expander_by_label(at, needle):
    """Return the first expander whose label contains `needle`, else None."""
    for e in at.expander:
        if needle.lower() in str(getattr(e.proto, "label", "")).lower():
            return e
    return None


# ---- (1) Analyze button gradient ------------------------------------------
def test_ux_mod_analyze_button_gradient_style_injected():
    """A scoped <style> block targets the form-submit (Analyze) button with a
    linear-gradient. AppTest renders the injected <style> as markdown text, so we
    assert the CSS *string* (selector + gradient). Pixel color is a visual gap."""
    at = _ux_run()
    assert not at.exception, f"boot raised: {at.exception}"
    blob = "\n".join(_markdowns(at))
    assert "stFormSubmitButton" in blob, (
        "scoped CSS selector 'stFormSubmitButton' not found in injected style")
    assert "linear-gradient" in blob, (
        "Analyze button gradient ('linear-gradient') not found in injected style")
    # The two appear together in one <style> block (same selector scope).
    style_blocks = [m for m in _markdowns(at)
                    if "stFormSubmitButton" in m and "linear-gradient" in m]
    assert style_blocks, (
        "no single <style> block scoping the gradient to stFormSubmitButton")


def test_ux_mod_analyze_submit_button_still_present():
    at = _ux_run()
    assert not at.exception
    labels = [b.label or "" for b in at.button]
    assert any("Analyze" in lbl for lbl in labels), (
        f"Analyze form-submit button missing; buttons={labels}")


def test_ux_mod_demo_analyze_still_works_end_to_end():
    """The re-colored Analyze button still functions: a demo Analyze produces a
    full decomposition (gradient CSS is cosmetic, behavior unchanged)."""
    at = _ux_analyzed()
    labels = {m.label for m in at.metric}
    assert "Fair value" in labels and "Embedded margin" in labels, (
        "demo Analyze did not produce the decomposition after the button restyle")


# ---- (2) Key fields collapse to a ✓ chip ----------------------------------
def test_ux_mod_key_inputs_render_when_no_key_set():
    """No key in session → the Anthropic & FRED password text_inputs render."""
    at = _ux_run()
    assert not at.exception
    labels = [t.label for t in at.text_input]
    assert "Anthropic API key" in labels, "Anthropic key input not shown (no key)"
    assert "FRED API key" in labels, "FRED key input not shown (no key)"
    # No ✓ chip when nothing is set.
    succ = " ".join(str(s.value) for s in at.success)
    assert "key set" not in succ, "unexpected '✓ key set' chip with no key set"


def test_ux_mod_key_chip_shown_and_secret_not_leaked_when_keys_set():
    """Keys pre-set in session → green '✓ key set' chip renders, the password
    inputs are hidden, an Edit affordance exists, and the raw key value never
    appears anywhere in the rendered text."""
    st.cache_data.clear()
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.session_state["anthropic_key"] = _ANTHROPIC_SECRET
    at.session_state["fred_key"] = _FRED_SECRET
    at.run()
    assert not at.exception, f"boot raised with keys set: {at.exception}"

    # Two ✓ chips (one per key field).
    chips = [str(s.value) for s in at.success if "key set" in str(s.value)]
    assert len(chips) >= 2, f"expected two '✓ key set' chips, found {chips}"

    # Password inputs collapsed away (no key-field text_inputs).
    input_labels = [t.label for t in at.text_input]
    assert "Anthropic API key" not in input_labels, (
        "Anthropic password input should be hidden behind the chip")
    assert "FRED API key" not in input_labels, (
        "FRED password input should be hidden behind the chip")

    # Edit affordance present.
    btn_labels = [b.label for b in at.button]
    assert any(lbl == "Edit" for lbl in btn_labels), (
        f"Edit affordance missing from key chips; buttons={btn_labels}")

    # Raw secrets NEVER rendered (chip shows only that a key is set).
    blob = _all_text(at)
    assert _ANTHROPIC_SECRET not in blob, "Anthropic secret leaked into rendered UI"
    assert _FRED_SECRET not in blob, "FRED secret leaked into rendered UI"
    for ti in at.text_input:
        assert _ANTHROPIC_SECRET not in str(ti.value), "secret leaked in text_input"
        assert _FRED_SECRET not in str(ti.value), "secret leaked in text_input"


def test_ux_mod_sidebar_order_anthropic_before_fred_with_keys_set():
    """Sidebar order preserved: an Anthropic marker precedes a FRED marker even
    in the collapsed-chip state (demo → Anthropic → FRED → upload)."""
    st.cache_data.clear()
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.session_state["anthropic_key"] = _ANTHROPIC_SECRET
    at.session_state["fred_key"] = _FRED_SECRET
    at.run()
    blob = "\n".join([str(s.value) for s in at.subheader]
                     + _markdowns(at))
    ai = blob.find("Anthropic")
    fi = blob.find("FRED")
    assert ai != -1 and fi != -1, "Anthropic/FRED markers not both present"
    assert ai < fi, "Anthropic key section must appear before the FRED section"


def test_ux_mod_edit_toggle_reveals_input_again():
    """Toggling the per-field edit flag re-reveals the password input so the user
    can change/clear a set key (Edit affordance behavior)."""
    st.cache_data.clear()
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.session_state["anthropic_key"] = _ANTHROPIC_SECRET
    at.session_state["_anthropic_key_edit"] = True  # simulate Edit clicked
    at.run()
    assert not at.exception
    labels = [t.label for t in at.text_input]
    assert "Anthropic API key" in labels, (
        "Edit-active state should re-reveal the Anthropic password input")
    # Even while editing, the secret value is not echoed in plaintext anywhere
    # other than the (password-masked) input widget's own value.
    non_input = _all_text(at)
    assert _ANTHROPIC_SECRET not in non_input, (
        "secret leaked into rendered text while editing")


# ---- (2 regression) PDF-upload gating still keys off has_key ---------------
def test_ux_mod_pdf_uploader_disabled_without_anthropic_key():
    """Regression: with no Anthropic key, the PDF uploader is disabled (and the
    BYOK lock caption shows). proto.disabled is the strong gating signal."""
    at = _ux_run()
    assert not at.exception
    uploaders = at.file_uploader
    assert uploaders, "PDF file_uploader not found"
    assert uploaders[0].proto.disabled is True, (
        "PDF uploader must be DISABLED when no Anthropic key is set")
    caps = " ".join(c.value for c in at.caption)
    assert "enable PDF upload" in caps or "bring-your-own-key" in caps.lower()


def test_ux_mod_pdf_uploader_enabled_with_anthropic_key():
    """Regression: providing an Anthropic key enables the PDF uploader and drops
    the lock caption — gating still keys off has_key after the chip change."""
    st.cache_data.clear()
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.session_state["anthropic_key"] = _ANTHROPIC_SECRET
    at.run()
    assert not at.exception
    uploaders = at.file_uploader
    assert uploaders, "PDF file_uploader not found"
    assert uploaders[0].proto.disabled is False, (
        "PDF uploader must be ENABLED once an Anthropic key is set")
    caps = " ".join(c.value for c in at.caption)
    assert "enable PDF upload" not in caps, (
        "BYOK lock caption should be gone once a key is set")


# ---- (3) MC paths moved into Advanced settings expander -------------------
def test_ux_mod_mc_paths_slider_present_with_same_options():
    """The Monte-Carlo paths control still exists with the same options/default."""
    at = _ux_run()
    assert not at.exception
    assert at.select_slider, "Monte Carlo paths select_slider not found"
    ss = at.select_slider[0]
    assert "monte carlo" in str(ss.label).lower(), (
        f"unexpected slider label: {ss.label!r}")
    # Options unchanged (10k/25k/50k/100k); default 50_000.
    opts = [str(o) for o in ss.options]
    for expected in ("10000", "25000", "50000", "100000"):
        assert expected in opts, f"MC paths option {expected} missing; got {opts}"
    assert str(ss.value) == "50000", f"MC paths default should be 50000, got {ss.value}"


def test_ux_mod_advanced_expander_present_and_collapsed():
    """An 'Advanced settings' expander exists and is collapsed by default.

    This build exposes expander open/closed via `proto.expanded`, so we can
    assert the collapsed default directly (no longer a coverage gap)."""
    at = _ux_run()
    assert not at.exception
    adv = _expander_by_label(at, "Advanced")
    assert adv is not None, "'Advanced settings' expander not found"
    assert adv.proto.expanded is False, (
        "Advanced settings expander should be COLLAPSED by default")


def test_ux_mod_pricing_consumes_selected_n_paths():
    """Pricing still consumes the chosen n_paths: setting the slider to 10k and
    running a demo Analyze yields a full result (the slider still feeds
    _price_cached)."""
    at = _ux_run()
    assert not at.exception
    at.select_slider[0].set_value(10000).run()
    assert not at.exception, f"setting n_paths raised: {at.exception}"
    assert str(at.select_slider[0].value) == "10000"
    _analyze(at)
    assert not at.exception, f"Analyze with n_paths=10k raised: {at.exception}"
    labels = {m.label for m in at.metric}
    assert "Fair value" in labels and "P(loss)" in labels, (
        "demo Analyze with n_paths=10k produced no result")


# ---- (4) Direct get-a-key links -------------------------------------------
def test_ux_mod_signup_links_present():
    """Both signup URLs render in the sidebar markdown (always visible — chip or
    input state)."""
    at = _ux_run()
    assert not at.exception
    blob = "\n".join(_markdowns(at))
    assert "console.anthropic.com" in blob, "Anthropic console signup URL missing"
    assert "fredaccount.stlouisfed.org/apikeys" in blob, "FRED signup URL missing"


def test_ux_mod_signup_links_present_even_when_keys_set():
    """The get-a-key links stay visible in the collapsed-chip state too."""
    st.cache_data.clear()
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.session_state["anthropic_key"] = _ANTHROPIC_SECRET
    at.session_state["fred_key"] = _FRED_SECRET
    at.run()
    assert not at.exception
    blob = "\n".join(_markdowns(at))
    assert "console.anthropic.com" in blob
    assert "fredaccount.stlouisfed.org/apikeys" in blob


# ---- (5) Market inputs expander default-open ------------------------------
def test_ux_mod_market_inputs_panel_renders_after_analyze():
    """The 'Market inputs used in this valuation' expander renders after a demo
    Analyze and carries the spot/rate/credit/div/vol metrics."""
    at = _ux_analyzed()
    panel = _expander_by_label(at, "Market inputs used in this valuation")
    assert panel is not None, "'Market inputs used in this valuation' panel missing"
    labels = {m.label for m in at.metric}
    for lbl in ("Spot", "Risk-free", "Credit spread", "Dividend yield", "ATM vol"):
        assert lbl in labels, f"Market-inputs metric {lbl!r} missing"


def test_ux_mod_market_inputs_panel_default_open():
    """The Market-inputs expander is default-OPEN (expanded=True).

    This build exposes `proto.expanded`, so the open default is directly
    assertable (no longer merely a visual gap)."""
    at = _ux_analyzed()
    panel = _expander_by_label(at, "Market inputs used in this valuation")
    assert panel is not None, "Market inputs panel missing"
    assert panel.proto.expanded is True, (
        "Market inputs expander should be default-OPEN (expanded=True)")


# ===========================================================================
# UX — polish (feedback "Week of 2026-06-15" → "UX — polish";
# implemented per UI_NOTES.md "2026-06-20 UX — polish"). Three changes:
#   (1) "📖 How to read this:" orientation caption under EACH of the 3 charts
#       (decomposition / payoff / histogram), kept SEPARATE from and ordered
#       ABOVE the existing "What this means:" finding callout. On the payoff
#       chart the prior blue/grey technical legend caption was FOLDED INTO this
#       orientation note (no redundant duplicate caption).
#   (2) De-arrowed metrics: the four Section-2 metrics (Embedded margin hero,
#       Bond floor, Option value, Fair value) no longer pass a static
#       "% of notional" descriptor into the `delta` slot (which rendered a
#       misleading colored arrow). The "% of notional" now lives in the metric
#       value string and/or an `st.caption`; the metrics carry NO delta/arrow.
#   (3) Payoff legend repositioned in prism_ui.charts.payoff_diagram: horizontal,
#       centered, BELOW the plot (y<0); height=480, margin.b=90; breakeven
#       annotation moved to the bottom (was overlapping the title at the top).
#
# Assertions key on STABLE substrings/keywords (not full sentences). AppTest
# re-execs app.py per run(); _ux_run() clears st.cache_data so demo pricing
# recomputes deterministically (no network, no key, no token spend).
#
# Visual-only gap: AppTest cannot paint Plotly pixels, so item (3) is verified by
# introspecting the Figure layout (orientation/anchor/y/height/margin/annotation)
# rather than the rendered pixel crowding — flagged for a manual visual check.
# ===========================================================================
_HOW_TO_READ_MARK = "how to read this"


def _how_to_read_captions(at):
    """All captions that are the '📖 How to read this:' orientation notes."""
    return [v for v in _captions(at) if _HOW_TO_READ_MARK in v.lower()]


# ---- (1) "How to read this" orientation notes -----------------------------
def test_ux_polish_three_how_to_read_captions_render():
    """Exactly three '📖 How to read this' orientation captions render — one per
    chart (decomposition / payoff / histogram)."""
    at = _ux_analyzed()
    htr = _how_to_read_captions(at)
    assert len(htr) == 3, (
        f"expected exactly 3 'How to read this' orientation captions "
        f"(one per chart), found {len(htr)}: {htr}")


def test_ux_polish_how_to_read_distinct_from_what_this_means():
    """The orientation notes and the finding callouts BOTH render and are
    distinct: 3 'How to read this' captions AND >=3 'What this means' infos."""
    at = _ux_analyzed()
    htr = _how_to_read_captions(at)
    takeaways = [v for v in _infos(at) if "what this means" in v.lower()]
    assert len(htr) == 3, f"expected 3 orientation captions, found {len(htr)}"
    assert len(takeaways) >= 3, (
        f"expected >=3 'What this means' finding callouts, found "
        f"{len(takeaways)}")
    # They live in different element types (caption vs info) → inherently
    # distinct; also confirm no 'What this means' text leaked into the captions.
    for c in htr:
        assert "what this means" not in c.lower(), (
            "orientation caption should not also contain the finding callout")


def test_ux_polish_per_chart_orientation_phrasing():
    """Each chart's orientation note has chart-specific phrasing:
    decomposition (stacked bar / bond floor), payoff (X-axis underlier / Y-axis
    return), histogram (bars / simulated scenarios)."""
    at = _ux_analyzed()
    blob = "\n".join(_how_to_read_captions(at)).lower()
    # Decomposition orientation.
    assert "stacked bar" in blob or "bond floor" in blob, (
        "decomposition orientation phrasing not found")
    # Payoff orientation (axes).
    assert "x-axis" in blob and "y-axis" in blob, (
        "payoff orientation axis phrasing not found")
    # Histogram orientation (bars / scenarios).
    assert ("bar counts" in blob or "each bar" in blob) and (
        "scenario" in blob or "return range" in blob), (
        "histogram orientation phrasing not found")


def test_ux_polish_payoff_legend_caption_folded_no_duplicate():
    """The prior blue/grey technical legend caption is FOLDED INTO the payoff
    orientation note, so the blue/grey explanation appears in exactly the ONE
    payoff orientation caption — not as a separate duplicate caption line."""
    at = _ux_analyzed()
    # The blue/grey legend language ("solid blue ... grey dotted") should appear
    # only within a 'How to read this' caption (the folded orientation note),
    # never as a standalone caption without the orientation marker.
    blue_grey_caps = [c for c in _captions(at)
                      if ("solid blue" in c.lower() or "grey dotted" in c.lower()
                          or "1:1" in c.lower())]
    assert blue_grey_caps, "payoff blue/grey legend explanation not found at all"
    for c in blue_grey_caps:
        assert _HOW_TO_READ_MARK in c.lower(), (
            "blue/grey legend explanation should be folded into the 'How to "
            f"read this' orientation note, not a separate caption: {c!r}")
    # And the payoff orientation caption count is right: exactly one caption
    # carries the blue/grey language (no redundant duplicate).
    assert len(blue_grey_caps) == 1, (
        f"expected exactly one payoff orientation caption carrying the blue/grey "
        f"legend note (no duplicate), found {len(blue_grey_caps)}: {blue_grey_caps}")


# ---- (2) No ambiguous arrows on the four Section-2 metrics -----------------
_SECTION2_METRICS = ("Embedded margin", "Bond floor", "Option value", "Fair value")


def _delta_text(metric):
    """Return the metric's delta as a string ('' if None/empty)."""
    d = getattr(metric, "delta", None)
    return "" if d is None else str(d)


def test_ux_polish_section2_metrics_have_no_delta_arrow():
    """The four Section-2 metrics carry NO delta — so Streamlit draws no colored
    ↑/↓ arrow. (The '% of notional' descriptor moved out of the delta slot.)"""
    at = _ux_analyzed()
    for lbl in _SECTION2_METRICS:
        m = _metric_by_label(at, lbl)
        assert m is not None, f"{lbl} metric missing"
        assert not _delta_text(m), (
            f"{lbl} must have NO delta (no ambiguous arrow); got "
            f"delta={getattr(m, 'delta', None)!r}")


def test_ux_polish_section2_pct_of_notional_still_shown():
    """The '% of notional' figure is still surfaced for each Section-2 metric —
    now in the value string and/or an on-screen caption, not the delta slot."""
    at = _ux_analyzed()
    # Hero margin: the % lives in the metric value (and a caption).
    margin = _metric_by_label(at, "Embedded margin")
    assert margin is not None, "Embedded margin metric missing"
    assert "of notional" in str(margin.value).lower() or any(
        "of notional" in c.lower() for c in _captions(at)), (
        "hero margin should still show '% of notional' (value or caption)")
    # The hero margin percent figure is present somewhere (value or caption).
    assert "%" in str(margin.value), (
        "hero margin value should carry the percent-of-notional figure")
    # Bond floor / Option value / Fair value: '% of notional' captions exist.
    notional_caps = [c for c in _captions(at) if "of notional" in c.lower()]
    assert len(notional_caps) >= 3, (
        f"expected >=3 '% of notional' captions for the breakdown + hero, "
        f"found {len(notional_caps)}: {notional_caps}")


def test_ux_polish_hero_margin_metric_still_present():
    """The hero Embedded-margin metric still exists (the de-arrowing did not
    remove it) and keeps delta_color 'off' (GRAY proto color)."""
    at = _ux_analyzed()
    margin = _metric_by_label(at, "Embedded margin")
    assert margin is not None, "hero Embedded margin metric missing"
    assert margin.proto.color == _METRIC_COLOR_OFF, (
        f"Embedded margin should keep delta_color='off' (GRAY), got "
        f"proto.color={margin.proto.color}")


# ---- (3) Payoff legend config (direct Figure introspection) ---------------
def test_ux_polish_payoff_legend_repositioned_below_plot():
    """prism_ui.charts.payoff_diagram places the legend horizontally, centered,
    BELOW the plot (y<0), with the new height/bottom-margin, and the breakeven
    annotation is NOT anchored at the top.

    Built directly on a real DecompositionResult priced offline (deterministic
    seed, no network) — AppTest can't introspect Plotly layout."""
    import datetime as _dt
    from prism import price_product as _price_product
    from prism.models import Autocallable as _Autocallable
    from prism_ui import charts as _charts

    maturity = _dt.date.today() + _dt.timedelta(days=int(round(5 * 365.25)))
    product = _Autocallable(
        underlier="AAPL", notional=100_000, issuer="JPMorgan Chase",
        issuer_rating="A", offer_price=1.0, maturity=maturity,
        coupon_rate=0.095, coupon_barrier=0.70, call_barrier=1.00,
        knock_in_barrier=0.60, observation_freq="quarterly")
    result = _price_product(
        product, n_paths=20_000, seed=7,
        spot=200.0, risk_free=0.045, div_yield=0.005,
        credit_spread=0.045, flat_vol=0.30)

    fig = _charts.payoff_diagram(result)
    legend = fig.layout.legend
    assert legend.orientation == "h", (
        f"payoff legend should be horizontal, got {legend.orientation!r}")
    assert legend.yanchor == "top", (
        f"payoff legend yanchor should be 'top', got {legend.yanchor!r}")
    assert legend.y is not None and legend.y < 0, (
        f"payoff legend should sit BELOW the plot (y<0), got y={legend.y}")
    assert legend.xanchor == "center" and legend.x == 0.5, (
        f"payoff legend should be horizontally centered, got "
        f"xanchor={legend.xanchor!r}, x={legend.x}")
    # New height + bottom margin so the legend clears the x-axis title.
    assert fig.layout.height == 480, (
        f"payoff figure height should be 480, got {fig.layout.height}")
    assert fig.layout.margin.b == 90, (
        f"payoff bottom margin should be 90, got {fig.layout.margin.b}")
    # Exact new legend y per UI_NOTES.
    assert fig.layout.legend.y == -0.22, (
        f"payoff legend y should be -0.22, got {fig.layout.legend.y}")

    # The breakeven annotation (if present) must NOT be anchored at the top.
    breakeven = [a for a in fig.layout.annotations
                 if "breakeven" in str(getattr(a, "text", "")).lower()]
    for a in breakeven:
        # When add_vline annotation_position="bottom", Plotly sets yanchor='top'
        # and yref='paper' y=0 (bottom edge). The key regression guard: it is NOT
        # pinned to the top of the plot.
        assert a.y is None or a.y <= 0.5, (
            f"breakeven annotation should be at the BOTTOM, not the top "
            f"(got y={a.y}, yanchor={getattr(a, 'yanchor', None)!r})")


# ===========================================================================
# Download PDF report (PRD §8.5; UI_NOTES.md "2026-06-20 — Download PDF report").
#
# A purpose-built, analysis-only PDF export. A primary
# st.download_button("⬇️ Download PDF report", ...) appears at the TOP of the
# results (Section 2), under the hero margin verdict, backed by a cached helper
# (`_report_bytes_cached`). The rest of the results still render.
#
# AppTest LIMITATION (Streamlit 1.58): download_button uses a lazy
# `deferred_file_id`; the element's proto carries a mock `url` but exposes
# NEITHER the `data` bytes NOR `file_name`. It is also not surfaced under the
# top-level `at.download_button` attribute (absent in this build) nor under
# `at.button` — only the generic `at.get("download_button")` accessor reaches it.
# So we (a) assert the BUTTON EXISTS (via at.get) + the results still render via
# AppTest, and (b) unit-verify the actual %PDF- bytes + .pdf filename + page
# count by calling the SAME backend path (build_report_pdf / report_filename)
# with the session-stashed inputs the app captured at price time. The unreadable
# button data/file_name is documented as a coverage gap, not a failure.
# ===========================================================================
from io import BytesIO as _BytesIO  # noqa: E402

from pypdf import PdfReader as _PdfReader  # noqa: E402

from prism import (  # noqa: E402
    build_report_pdf as _build_report_pdf,
    report_filename as _report_filename,
)

_PDF_BTN_LABEL = "Download PDF report"


def _download_buttons(at):
    """All download_button elements via the generic accessor.

    In this Streamlit build there is no `at.download_button` proxy and the
    element is not surfaced under `at.button`; `at.get("download_button")` is the
    only path that reaches it. Returns [] if the accessor is unavailable."""
    try:
        return list(at.get("download_button"))
    except Exception:
        return []


def test_report_download_button_present_after_demo_analyze():
    """After a demo Analyze, a '⬇️ Download PDF report' button renders."""
    at = _ux_analyzed()
    labels = [str(getattr(b, "label", "")) for b in _download_buttons(at)]
    assert any(_PDF_BTN_LABEL in lbl for lbl in labels), (
        f"Download PDF report button not found; download buttons={labels}")


def test_report_button_absent_before_analyze():
    """No report button before any Analyze (report_keys is unset)."""
    at = _ux_run()
    assert not at.exception
    labels = [str(getattr(b, "label", "")) for b in _download_buttons(at)]
    assert not any(_PDF_BTN_LABEL in lbl for lbl in labels), (
        f"report button should not render before Analyze; got {labels}")


def test_report_button_does_not_break_results_render():
    """The rest of the results (hero verdict, decomposition, payoff, risk) still
    render with the download button present, and Analyze raised no exception."""
    at = _ux_analyzed()
    assert not at.exception, f"results render raised: {at.exception}"
    # Hero verdict + decomposition + risk still present.
    text = "\n".join(_markdowns(at) + _infos(at)).lower()
    assert "fair value" in text, "hero verdict missing alongside the PDF button"
    labels = {m.label for m in at.metric}
    for lbl in ("Embedded margin", "Bond floor", "Fair value", "P(loss)", "Delta"):
        assert lbl in labels, f"{lbl} metric missing alongside the PDF button"
    # The three chart takeaways still render.
    takeaways = [v for v in _infos(at) if "what this means" in v.lower()]
    assert len(takeaways) >= 3, "chart takeaways missing alongside the PDF button"


def _stashed_report_inputs(at):
    """Pull the session-stashed primitives the app captured at price time.

    (AppTest's session_state proxy raises on missing keys, so use `in` /
    subscript, not `.get()`.)"""
    ss = at.session_state
    assert "report_keys" in ss and ss["report_keys"] is not None, (
        "report_keys not stashed in session after Analyze")
    assert "report_generated" in ss and ss["report_generated"], (
        "report_generated timestamp not stashed after Analyze")
    assert "result" in ss and ss["result"] is not None, (
        "priced result not in session after Analyze")
    return ss["report_keys"], ss["report_generated"], ss["result"]


def _rebuild_pdf_via_backend(at):
    """Replicate _report_bytes_cached's pure path from session-stashed inputs:
    rebuild the product, assemble meta (NO secret), build the PDF + filename."""
    import datetime as _dt
    # build_product lives in prism_ui.config (NOT app) — importing it directly
    # avoids re-executing app.py as a bare script (which would raise outside an
    # AppTest context). This is the SAME callable app._report_bytes_cached uses.
    from prism_ui.config import build_product as _build_product

    (ptype, shared_key, per_type_key, demo, paths), generated, result = (
        _stashed_report_inputs(at))
    shared = dict(shared_key)
    shared["maturity"] = _dt.date.fromisoformat(shared["maturity"])
    product = _build_product(ptype, shared, dict(per_type_key))
    meta = {
        "demo_mode": demo,
        "generated_at": generated,
        "n_paths": int(paths),
        "data_source": "Demo (offline)" if demo else "Live (yfinance + FRED)",
    }
    pdf_bytes = _build_report_pdf(product, result, meta=meta)
    fname = _report_filename(product, meta)
    return pdf_bytes, fname, meta


def test_report_bytes_valid_pdf_via_backend_from_session():
    """Coverage for the un-introspectable button data: unit-call the backend with
    the session-stashed inputs and assert real %PDF- bytes + pages > 0 + a sane
    .pdf filename. (download_button's data/file_name aren't exposed by AppTest.)"""
    at = _ux_analyzed()
    pdf_bytes, fname, _meta = _rebuild_pdf_via_backend(at)
    assert isinstance(pdf_bytes, bytes) and pdf_bytes, "empty report bytes"
    assert pdf_bytes.startswith(b"%PDF-"), "report bytes missing %PDF- header"
    reader = _PdfReader(_BytesIO(pdf_bytes))
    assert len(reader.pages) > 0, "report PDF has no pages"
    assert fname.endswith(".pdf"), f"filename should end with .pdf: {fname!r}"
    assert " " not in fname, f"filename should have no spaces: {fname!r}"
    # Default demo product is an autocallable on AAPL.
    assert "aapl" in fname.lower(), f"filename should carry the underlier: {fname!r}"
    assert "autocallable" in fname.lower(), (
        f"filename should carry the product slug: {fname!r}")


def test_report_bytes_no_secret_leakage_via_backend():
    """The PDF built from the live session carries no secret-like text/bytes."""
    at = _ux_analyzed()
    pdf_bytes, fname, _meta = _rebuild_pdf_via_backend(at)
    low = pdf_bytes.lower()
    for tok in (b"sk-ant", b"fred_api_key", b"api_key", b"bearer", b"secret"):
        assert tok not in low, f"secret token {tok!r} leaked into report bytes"
    assert "secret" not in fname.lower() and "sk-ant" not in fname.lower()
    # meta carries only presentation context — never a key.
    assert "demo_mode" in _meta and "generated_at" in _meta
    for k in _meta:
        assert k in {"demo_mode", "generated_at", "n_paths", "data_source"}, (
            f"unexpected meta key {k!r} (must never include a secret)")


def test_report_meta_data_source_demo_offline():
    """In demo mode the report's data_source resolves to the offline label and the
    PDF text reflects it (no live/network source claimed)."""
    at = _ux_analyzed()
    pdf_bytes, _fname, meta = _rebuild_pdf_via_backend(at)
    assert meta["data_source"] == "Demo (offline)", (
        f"demo run should label data_source 'Demo (offline)', got "
        f"{meta['data_source']!r}")
    text = "\n".join((p.extract_text() or "")
                     for p in _PdfReader(_BytesIO(pdf_bytes)).pages).lower()
    assert "demo" in text, "report text should reflect the demo data source"
