"""Prism — Structured Product Pricing & Decomposition Engine (Streamlit UI).

Single-process app (PRD §7): this UI calls the `prism` package directly via
in-process function calls. No server, no network between UI and engine.

Run:
    streamlit run app.py        # opens http://localhost:8501

Sections (PRD §8):
  - Sidebar: Settings (BYOK Anthropic key), demo-mode toggle, PDF upload
  - Main: term-sheet input form -> Analyze -> decomposition, payoff, risk

See UI_NOTES.md for the full layout and backend contract mapping.
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import os

import streamlit as st

import prism
from prism import build_report_pdf, price_product, report_filename
from prism.market_data import MarketDataError

# RCA-002 §7C / §A: autoload a local `.env` ONCE at startup, before any env var
# is read, so FRED_API_KEY (and optionally the Anthropic key) can live there.
# Safe no-op if python-dotenv / .env is absent; never raises (BACKEND_NOTES.md).
# `override=False` upstream means already-exported env vars win over .env.
if not st.session_state.get("_env_loaded"):
    prism.load_local_env()
    st.session_state["_env_loaded"] = True
from prism.pdf_parser import (
    PdfParseError,
    UnsupportedProductError,
    parse_term_sheet,
)

from prism_ui import charts
from prism_ui.narrative import (
    decomposition_takeaway as _decomposition_takeaway,
    histogram_takeaway as _histogram_takeaway,
    margin_verdict as _margin_verdict,
    payoff_takeaway as _payoff_takeaway,
)
from prism_ui.config import (
    BARRIER_TYPES,
    OBSERVATION_FREQS,
    PRODUCT_LABELS,
    TYPE_FIELDS,
    build_product,
    parsed_value_to_human,
)
from prism_ui.formatting import (
    fmt_currency,
    fmt_pct,
    fmt_signed_currency,
    pct_of_notional,
)

# ----------------------------------------------------------------------------
# Demo / offline market overrides (PRD: frictionless first run, no network/keys)
# ----------------------------------------------------------------------------
# Mirrors the BACKEND_NOTES.md offline example. Default ON so Analyze works
# immediately at launch without network or a FRED key.
DEMO_MARKET = dict(
    spot=200.0,
    risk_free=0.045,
    div_yield=0.005,
    credit_spread=0.045,
    flat_vol=0.30,
)
DEMO_SEED = 7

# Canonical PRD §15.7 5-year AAPL autocallable defaults for the form.
DEFAULT_PRODUCT_TYPE = "autocallable"


# ----------------------------------------------------------------------------
# Branding assets (hand-authored SVG; see assets/ and UI_NOTES.md)
# ----------------------------------------------------------------------------
_ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
_LOGO_SVG_PATH = os.path.join(_ASSETS_DIR, "prism_logo.svg")
_FAVICON_PNG_PATH = os.path.join(_ASSETS_DIR, "prism_favicon.png")


@st.cache_resource(show_spinner=False)
def _logo_data_uri() -> str | None:
    """Return the full Prism lockup SVG as a base64 `data:` URI.

    Streamlit sanitizes raw inline SVG in `st.markdown`, so we embed the SVG as
    a base64 data URI inside an <img>, which renders reliably and stays crisp at
    any size. Returns None if the asset is missing (header then falls back to a
    plain text title) so a missing file can never crash the app.
    """
    try:
        with open(_LOGO_SVG_PATH, "rb") as fh:
            b64 = base64.b64encode(fh.read()).decode("ascii")
        return f"data:image/svg+xml;base64,{b64}"
    except OSError:
        return None


@st.cache_resource(show_spinner=False)
def _favicon():
    """Page icon: the custom Prism mark.

    Prefer the PNG mark (rendered from assets/prism_mark.svg) loaded via PIL —
    the most reliable page_icon input. Falls back to the prism emoji if the
    asset can't be loaded, so set_page_config never fails.
    """
    try:
        from PIL import Image
        return Image.open(_FAVICON_PNG_PATH)
    except Exception:
        return "🔷"


st.set_page_config(page_title="Prism — Structured Product Decomposition",
                   page_icon=_favicon(), layout="wide")


# ----------------------------------------------------------------------------
# Brand styling for the Analyze submit button (2026-06-15 UX — moderate #1)
# ----------------------------------------------------------------------------
# The `type="primary"` submit button otherwise renders in the theme primary
# color (reads as red). We override JUST the form-submit button (Streamlit 1.58
# wrapper testid `stFormSubmitButton`) with the brand violet→magenta gradient
# from the diamond mark. Scope is deliberately narrow so other buttons (Dismiss,
# Extract from PDF) are untouched, and the disabled state stays visually
# distinct (muted, not-allowed cursor) so a blocked Analyze never looks
# clickable. Verified selector against streamlit/static (FormSubmitContent JS).
st.markdown(
    """
    <style>
    div[data-testid="stFormSubmitButton"] button {
        background: linear-gradient(95deg, #7c3aed 0%, #c026d3 55%, #ec4899 100%);
        color: #ffffff !important;
        border: 0 !important;
        font-weight: 600;
        box-shadow: 0 1px 6px rgba(124, 58, 237, 0.35);
        transition: filter 0.15s ease, box-shadow 0.15s ease;
    }
    div[data-testid="stFormSubmitButton"] button:hover:not(:disabled) {
        filter: brightness(1.08);
        box-shadow: 0 2px 10px rgba(192, 38, 211, 0.45);
        color: #ffffff !important;
    }
    div[data-testid="stFormSubmitButton"] button:active:not(:disabled) {
        filter: brightness(0.95);
    }
    /* Keep the disabled (refusal-blocked) state clearly NON-clickable. */
    div[data-testid="stFormSubmitButton"] button:disabled {
        background: #3a3550;
        color: rgba(255, 255, 255, 0.45) !important;
        box-shadow: none;
        cursor: not-allowed;
        filter: none;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ----------------------------------------------------------------------------
# Cached pricing wrapper (PRD §15.6 — don't re-hit APIs on every rerun)
# ----------------------------------------------------------------------------
def _fred_key_token(fred_api_key: str | None) -> str:
    """Stable, non-reversible cache token for the FRED key (RCA-002 §7B.3).

    The cache key must change when the key's *presence* changes (so toggling a
    key on/off re-runs pricing) WITHOUT ever placing the raw secret into the
    cache key — Streamlit may surface/persist cache keys, and the key must never
    be logged. We use a short SHA-256 prefix so two distinct keys also map to
    distinct cache slots, but the digest can't be reversed back to the secret.
    Empty/None -> a fixed "nokey" sentinel (the static-fallback slot).
    """
    if not fred_api_key:
        return "nokey"
    return "k:" + hashlib.sha256(fred_api_key.encode("utf-8")).hexdigest()[:16]


@st.cache_data(show_spinner=False)
def _price_cached(product_type: str, shared_key: tuple, per_type_key: tuple,
                  demo_mode: bool, n_paths: int, fred_key_token: str,
                  _fred_api_key: str | None):
    """Cache keyed on serializable inputs only.

    Rebuilds the product dataclass from primitive keys so the cache key is
    hashable and stable. Returns the DecompositionResult (a frozen-ish
    dataclass of primitives + lists, safe to cache).

    `fred_key_token` is the hashed, non-reversible FRED-key token that PARTICIPATES
    in the cache key (so changing/clearing the key re-runs). `_fred_api_key` is
    the real secret, forwarded to `price_product` only in live mode; the leading
    underscore tells `st.cache_data` to EXCLUDE it from the cache key, so the raw
    key is never hashed/persisted by the cache (RCA-002 §7B.3, PRD §9).
    """
    shared = dict(shared_key)
    # maturity comes through as an ISO string in the key; restore to date.
    shared["maturity"] = datetime.date.fromisoformat(shared["maturity"])
    per_type_human = dict(per_type_key)
    product = build_product(product_type, shared, per_type_human)

    if demo_mode:
        return price_product(product, n_paths=n_paths, seed=DEMO_SEED,
                             **DEMO_MARKET)
    # Live mode: forward the FRED key (used only when no treasury_curve/risk_free
    # override is given). None -> backend uses FRED_API_KEY env, else static
    # fallback flagged low_confidence_curve (BACKEND_NOTES.md).
    return price_product(product, n_paths=n_paths,
                         fred_api_key=(_fred_api_key or None))


# ----------------------------------------------------------------------------
# Cached PDF-report builder (PRD §8.5 — "Download PDF report")
# ----------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def _report_bytes_cached(product_type: str, shared_key: tuple,
                         per_type_key: tuple, demo_mode: bool, n_paths: int,
                         generated_at: str, _result):
    """Build the analysis-only valuation PDF and return raw bytes.

    Keyed on the SAME serializable primitives as `_price_cached` (product_type +
    shared_key + per_type_key + demo_mode + n_paths) PLUS the pinned
    `generated_at` timestamp, so the bytes are stable across Streamlit reruns and
    are not regenerated on every interaction. `generated_at` is pinned to when
    Analyze ran (stored in session at price time) so the cache key — and the
    report's header/filename date — don't drift on each rerun.

    `_result` is the already-computed `DecompositionResult`; the leading
    underscore excludes it from the cache key (it's a dataclass of lists/floats
    that's expensive/unstable to hash, and it's fully determined by the keyed
    primitives anyway). `build_report_pdf` is pure/offline — no re-pricing, no
    network. The product dataclass is rebuilt from the same primitive keys used
    for pricing (mirrors `_price_cached`).

    `meta` carries ONLY presentation context (demo flag, timestamp, path count,
    data-source label) — never any API key or secret (BACKEND_NOTES.md §meta).
    """
    shared = dict(shared_key)
    shared["maturity"] = datetime.date.fromisoformat(shared["maturity"])
    per_type_human = dict(per_type_key)
    product = build_product(product_type, shared, per_type_human)

    meta = {
        "demo_mode": demo_mode,
        "generated_at": generated_at,
        "n_paths": int(n_paths),
        "data_source": ("Demo (offline)" if demo_mode
                        else "Live (yfinance + FRED)"),
    }
    pdf_bytes = build_report_pdf(product, _result, meta=meta)
    fname = report_filename(product, meta)
    return pdf_bytes, fname


# ----------------------------------------------------------------------------
# Session state init
# ----------------------------------------------------------------------------
def _init_state():
    ss = st.session_state
    ss.setdefault("anthropic_key", "")
    # RCA-002 §7B: BYOK FRED key — session-state only, distinct from the
    # Anthropic key. Pre-seed from the environment (incl. anything loaded from
    # .env) so a key set there is honored without forcing a retype; the sidebar
    # field can still override it.
    ss.setdefault("fred_key", (os.environ.get("FRED_API_KEY") or "").strip())
    ss.setdefault("product_type", DEFAULT_PRODUCT_TYPE)
    ss.setdefault("result", None)
    ss.setdefault("priced_meta", None)
    # PDF-report (PRD §8.5) inputs captured at price time so the "Download PDF
    # report" button can rebuild the priced product + assemble meta WITHOUT
    # re-pricing, and with a timestamp pinned to the Analyze run (not the rerun).
    #   report_keys      -> (product_type, shared_key, per_type_key, demo, n_paths)
    #   report_generated -> ISO timestamp pinned at the moment Analyze succeeded
    ss.setdefault("report_keys", None)
    ss.setdefault("report_generated", None)
    # Refusal surface (RCA §7C): set when a PDF upload is out-of-scope.
    #   None        -> no active refusal
    #   list[str]   -> reasons to render; while set, Analyze is disabled so the
    #                  user can't price the (intentionally un-prefilled) form.
    ss.setdefault("refusal_reasons", None)
    # Inferred / low-confidence fields from the most recent successful parse
    # (RCA §D / §7C.5). Cleared when the user edits the form.
    ss.setdefault("inferred_fields", [])
    # Identity of the last extracted upload, so a *new* file clears stale state.
    ss.setdefault("last_upload_id", None)
    # Seed form-field defaults so widgets can be driven purely by `key=`
    # (avoids Streamlit's "both value= and key=" warning, and lets PDF parsing
    # populate fields by writing session_state before the widgets render).
    ss.setdefault("f_underlier", "AAPL")
    ss.setdefault("f_notional", 100_000.0)
    ss.setdefault("f_maturity",
                  datetime.date.today() + datetime.timedelta(days=365 * 5))
    ss.setdefault("f_offer", 100.0)
    ss.setdefault("f_issuer", "JPMorgan Chase")
    ss.setdefault("f_rating", "A")
    # Per-type field defaults (human units). Keys are namespaced by product type
    # (pt_<ptype>_<key>) so fields that share a name across types — e.g. `cap`
    # in PPN (30%) and Buffered (25%), or `barrier` in RC and Barrier Note —
    # keep their own distinct defaults and values.
    for ptype, fields in TYPE_FIELDS.items():
        for (key, _label, _kind, default, _help) in fields:
            ss.setdefault(_pt_key(ptype, key), default)


def _pt_key(product_type: str, field_key: str) -> str:
    """Namespaced session-state key for a per-type form field."""
    return f"pt_{product_type}_{field_key}"


def _key_field(*, session_key: str, edit_flag: str, label: str, caption: str,
               link_md: str, placeholder: str, current_value: str) -> str:
    """Render a BYOK key field as either an input or a collapsed ✓ chip.

    2026-06-15 UX (moderate #2 + #4): once a key is set, hide the raw password
    input behind a compact green "✓ key set · edit" chip; an Edit toggle reveals
    the input again so the user can change/clear it. The caption (with the
    get-a-key link) is always shown so a key-less user sees the link, and an
    editing user does too. The key value is NEVER displayed in plaintext.

    Returns the (stripped) key string to store back in session state. Callers
    keep their existing session keys / fallback semantics unchanged.

    Args:
        session_key:  st.session_state key holding the secret (e.g. "anthropic_key").
        edit_flag:    st.session_state key for the show/edit toggle (bool).
        label:        accessibility label for the input (collapsed visually).
        caption:      explanatory caption shown above the field/chip.
        link_md:      markdown "get a key" link, shown under the caption.
        placeholder:  input placeholder text.
        current_value: the current key value (already in session_state).
    """
    ss = st.session_state
    ss.setdefault(edit_flag, False)
    st.caption(caption)
    st.markdown(link_md)

    has_value = bool(current_value)
    # Show the input when there's no key yet, OR the user clicked Edit.
    show_input = (not has_value) or ss[edit_flag]

    if not show_input:
        # Collapsed: green ✓ status chip + an Edit affordance. The value is
        # never rendered — only the fact that a key is set.
        chip_col, btn_col = st.columns([3, 1])
        with chip_col:
            st.success("✓ key set", icon="✅")
        with btn_col:
            if st.button("Edit", key=f"{edit_flag}_edit_btn",
                         help="Change or clear this key."):
                ss[edit_flag] = True
                st.rerun()
        return current_value

    # Expanded: the password input (today's behavior).
    typed = st.text_input(
        label,
        value=current_value,
        type="password",
        placeholder=placeholder,
        label_visibility="collapsed",
    )
    typed = typed.strip()
    # If a key is set and the user is in edit mode, offer a Done button to
    # collapse back to the chip (only meaningful once a value exists).
    if has_value and ss[edit_flag]:
        if st.button("Done", key=f"{edit_flag}_done_btn",
                     help="Collapse this field back to the status chip."):
            ss[edit_flag] = False
            st.rerun()
    return typed


_init_state()


def _apply_parsed_fields(parsed: dict):
    """Populate session_state form fields from a pdf_parser result dict.

    Does NOT run pricing (PRD §8.1 Option B: review/edit before pricing).
    """
    ss = st.session_state
    ptype = parsed.get("product_type")
    if ptype in PRODUCT_LABELS:
        ss["product_type"] = ptype
    else:
        ptype = ss["product_type"]

    # Shared fields.
    if parsed.get("underlier") is not None:
        ss["f_underlier"] = str(parsed["underlier"])
    if parsed.get("notional") is not None:
        ss["f_notional"] = float(parsed["notional"])
    if parsed.get("maturity") is not None:
        ss["f_maturity"] = parsed["maturity"]
    if parsed.get("issuer") is not None:
        ss["f_issuer"] = str(parsed["issuer"])
    if parsed.get("issuer_rating") is not None:
        ss["f_rating"] = str(parsed["issuer_rating"])
    if parsed.get("offer_price") is not None:
        ss["f_offer"] = float(parsed["offer_price"]) * 100.0  # fraction -> %

    # Per-type fields (fractions/mults from parser -> human units for the form).
    for (key, _label, _kind, _default, _help) in TYPE_FIELDS.get(ptype, []):
        if key in parsed and parsed[key] is not None:
            ss[_pt_key(ptype, key)] = parsed_value_to_human(key, parsed[key])

    # Low-confidence inferred fields (RCA §7C.5) — surface as a caption later.
    ss["inferred_fields"] = list(parsed.get("inferred_fields") or [])


def _clear_refusal():
    """Drop any active refusal so manual pricing / a fresh parse can proceed.

    Called whenever the user signals a new intent: changing the product type,
    editing the form, or extracting a different PDF. This guarantees the
    refusal flag never *permanently* blocks legitimate single-name manual entry.
    """
    st.session_state["refusal_reasons"] = None


# ============================================================================
# SIDEBAR — Settings (BYOK), demo toggle, PDF upload
# ============================================================================
with st.sidebar:
    st.header("⚙️ Settings")

    demo_mode = st.checkbox(
        "Use demo market data (offline)",
        value=True,
        help="ON: deterministic offline pricing — no network or API keys "
             "needed. OFF: fetch live spot/vol/rates (needs internet and a "
             "FRED_API_KEY for the Treasury curve).",
    )

    st.divider()
    st.subheader("🔑 Anthropic API key (BYOK)")
    # 2026-06-15 UX (#2 + #4): collapse to a ✓ chip once set; always-visible
    # get-a-key link. Order preserved (Anthropic before FRED). Session key and
    # `has_key` semantics unchanged.
    st.session_state["anthropic_key"] = _key_field(
        session_key="anthropic_key",
        edit_flag="_anthropic_key_edit",
        label="Anthropic API key",
        caption=("Used only for PDF term-sheet parsing. Stored in this "
                 "session's memory only — never written to disk or logged "
                 "(PRD §9)."),
        link_md="[Get an Anthropic API key ↗](https://console.anthropic.com/)",
        placeholder="sk-ant-...",
        current_value=st.session_state["anthropic_key"],
    )
    has_key = bool(st.session_state["anthropic_key"])

    st.divider()
    st.subheader("🔑 FRED API key (optional)")
    # Sidebar field overrides the env/.env-seeded value, but a blank field falls
    # back to whatever the environment provides (so a .env key still works even
    # if the user never types into the box). The chip treats an env/.env-seeded
    # key as "set" because `fred_key` was pre-seeded from FRED_API_KEY at init.
    st.session_state["fred_key"] = _key_field(
        session_key="fred_key",
        edit_flag="_fred_key_edit",
        label="FRED API key",
        caption=("Used only for the **live U.S. Treasury yield curve** (live "
                 "market mode, demo OFF). Without it, Prism uses a documented "
                 "static fallback curve and flags the result **low "
                 "confidence**. Stored in this session's memory only — never "
                 "written to disk or logged (PRD §9). Can also live in a local "
                 "`.env` as `FRED_API_KEY`."),
        link_md=("[Get a free FRED API key ↗]"
                 "(https://fredaccount.stlouisfed.org/apikeys)"),
        placeholder="FRED API key (optional)",
        current_value=st.session_state["fred_key"],
    )
    fred_key = st.session_state["fred_key"] or (os.environ.get("FRED_API_KEY") or "").strip()

    st.divider()
    st.subheader("📄 Upload term sheet (PDF)")
    if not has_key:
        st.caption("🔒 Enter your Anthropic API key above to enable PDF upload "
                   "(bring-your-own-key).")
    uploaded = st.file_uploader(
        "Term sheet PDF",
        type=["pdf"],
        disabled=not has_key,
        help=("Bring your own Anthropic key to extract parameters from a PDF. "
              "Extracted fields populate the form for your review before "
              "pricing." if has_key else
              "Disabled until an Anthropic API key is provided (BYOK)."),
        label_visibility="collapsed",
    )
    if uploaded is not None and has_key:
        # A *new* upload supersedes any prior refusal/extract state.
        upload_id = (uploaded.name, uploaded.size)
        if upload_id != st.session_state["last_upload_id"]:
            _clear_refusal()
            st.session_state["last_upload_id"] = upload_id
        if st.button("Extract fields from PDF", width='stretch'):
            with st.spinner("Parsing term sheet with Claude…"):
                try:
                    parsed = parse_term_sheet(uploaded.getvalue(),
                                              st.session_state["anthropic_key"])
                # NOTE: UnsupportedProductError subclasses PdfParseError, so it
                # MUST be caught FIRST (BACKEND_NOTES.md) or the generic handler
                # below would swallow the refusal.
                except UnsupportedProductError as exc:
                    # Refuse: do NOT pre-fill the form (no lossy single-name
                    # approximation). Raise the flag so Analyze is disabled and
                    # render the refusal panel in the main area below.
                    st.session_state["refusal_reasons"] = list(exc.reasons)
                    st.session_state["inferred_fields"] = []
                    st.rerun()
                except PdfParseError as exc:
                    st.error(str(exc))
                except Exception as exc:  # defensive: never leak a traceback
                    st.error(f"Could not parse the PDF: {exc}")
                else:
                    _clear_refusal()
                    _apply_parsed_fields(parsed)
                    st.success("Fields extracted. Review/edit below, then click "
                               "Analyze.")
                    st.rerun()

    # ------------------------------------------------------------------------
    # Advanced settings (2026-06-15 UX — moderate #3): the Monte Carlo paths
    # slider moves out of the top of the sidebar into a collapsed expander near
    # the bottom, so the common path (demo → keys → upload) stays uncluttered.
    # Same options/default; `n_paths` still feeds `_price_cached` unchanged.
    # ------------------------------------------------------------------------
    st.divider()
    with st.expander("⚙️ Advanced settings", expanded=False):
        n_paths = st.select_slider(
            "Monte Carlo paths",
            options=[10_000, 25_000, 50_000, 100_000],
            value=50_000,
            help="More paths = smoother estimates, slower pricing. 100k targets "
                 "<5s (PRD §9).",
        )


# ============================================================================
# HEADER
# ============================================================================
_logo_uri = _logo_data_uri()
if _logo_uri:
    # Branded lockup (mark + wordmark). base64 data-URI <img> renders reliably
    # in Streamlit markdown and stays crisp (SVG). height ~44px reads well in the
    # header and on the dark background; width auto-scales to preserve aspect.
    st.markdown(
        f'<img src="{_logo_uri}" alt="Prism" '
        'style="height:44px;width:auto;display:block;margin:0.1rem 0 0.25rem;" />',
        unsafe_allow_html=True,
    )
else:
    # Asset missing — keep a working text title rather than a broken image.
    st.title("🔷 Prism")
st.caption("Independent structured-product pricing & decomposition. "
           "Educational / research tool — not investment advice.")
if demo_mode:
    st.info("**Demo market data is ON** — pricing is deterministic and offline "
            "(spot $200, r 4.5%, vol 30%, credit spread 4.5%). Turn it off in "
            "the sidebar to use live market data.", icon="🧪")


# ============================================================================
# TERM SHEET INPUT FORM (PRD §8.1 Option A)
# ============================================================================
st.subheader("1 · Term sheet")

# Product-type selector (outside the form so it reveals the right fields live).
type_keys = list(PRODUCT_LABELS.keys())
selected_label = st.selectbox(
    "Product type",
    options=[PRODUCT_LABELS[k] for k in type_keys],
    index=type_keys.index(st.session_state["product_type"]),
)
# Map label back to key.
product_type = type_keys[[PRODUCT_LABELS[k] for k in type_keys].index(selected_label)]
# Changing the product type is an explicit "I'm entering this myself" signal:
# clear any active refusal (so manual pricing works) and drop stale inferred
# flags carried over from a parse of a different product.
if product_type != st.session_state["product_type"]:
    _clear_refusal()
    st.session_state["inferred_fields"] = []
st.session_state["product_type"] = product_type

# ----------------------------------------------------------------------------
# Refusal panel (RCA §7C) — shown when the last upload was out-of-scope.
# Pricing is intentionally blocked: the form was NOT pre-filled, and the
# Analyze button is disabled while this flag is set (see `refused` below).
# ----------------------------------------------------------------------------
refusal_reasons = st.session_state.get("refusal_reasons")
refused = bool(refusal_reasons)
if refused:
    st.error("🚫 **Prism can't independently value this note:**", icon="🚫")
    st.markdown("\n".join(f"- {r}" for r in refusal_reasons))
    st.info(
        "We don't show a fair value here on purpose — approximating a "
        "multi-underlier or geared note as a single-name product would give a "
        "silently-wrong number. **You can still value a single-underlier note** "
        "by entering its parameters manually in the form below (pick the product "
        "type and fill in the fields), then click Analyze.",
        icon="✍️",
    )
    if st.button("Dismiss and enter a note manually",
                 help="Clears this notice and re-enables Analyze so you can "
                      "price a single-underlier note from the form below."):
        _clear_refusal()
        st.session_state["last_upload_id"] = None
        st.rerun()
    st.caption("Changing the product type or uploading a different term sheet "
               "also clears this notice.")

with st.form("term_sheet_form"):
    c1, c2, c3 = st.columns(3)
    with c1:
        underlier = st.text_input("Underlier ticker", key="f_underlier")
        notional = st.number_input("Notional ($)", min_value=1_000.0,
                                   step=1_000.0, format="%.0f", key="f_notional")
    with c2:
        maturity = st.date_input(
            "Maturity date", key="f_maturity",
            min_value=datetime.date.today() + datetime.timedelta(days=1),
        )
        offer_pct = st.number_input(
            "Offer price (% of par)", min_value=1.0, max_value=200.0, step=0.5,
            format="%.2f", key="f_offer", help="100 = issued at par.")
    with c3:
        issuer = st.text_input("Issuer", key="f_issuer")
        issuer_rating = st.text_input(
            "Issuer credit rating", key="f_rating",
            help="Used for the rating-based credit-spread fallback (e.g. AAA, A, BBB).")

    st.markdown(f"**{PRODUCT_LABELS[product_type]} parameters**")
    fields = TYPE_FIELDS[product_type]
    cols = st.columns(min(3, len(fields)))
    per_type_human: dict = {}
    for i, (key, label, kind, default, help_txt) in enumerate(fields):
        col = cols[i % len(cols)]
        skey = _pt_key(product_type, key)
        # Normalize any stale session value to the right type for this widget.
        cur = st.session_state.get(skey, default)
        with col:
            if kind == "freq":
                if cur not in OBSERVATION_FREQS:
                    st.session_state[skey] = default
                per_type_human[key] = st.selectbox(
                    label, OBSERVATION_FREQS, key=skey, help=help_txt)
            elif kind == "btype":
                if cur not in BARRIER_TYPES:
                    st.session_state[skey] = default
                per_type_human[key] = st.selectbox(
                    label, BARRIER_TYPES, key=skey, help=help_txt)
            elif kind == "mult":
                if not isinstance(cur, (int, float)):
                    st.session_state[skey] = float(default)
                per_type_human[key] = st.number_input(
                    label, min_value=0.0, max_value=10.0, step=0.1, format="%.2f",
                    key=skey, help=help_txt)
            else:  # pct / rate / cap
                if not isinstance(cur, (int, float)):
                    st.session_state[skey] = float(default)
                per_type_human[key] = st.number_input(
                    label, min_value=0.0, max_value=200.0, step=0.5, format="%.2f",
                    key=skey, help=help_txt)

    # Low-confidence inferred fields (RCA §7C.5): surface, don't block.
    inferred = st.session_state.get("inferred_fields") or []
    if inferred and not refused:
        st.warning(
            "⚠️ **Inferred (please verify):** " + ", ".join(inferred) +
            " — these were defaulted/approximated from the term sheet rather "
            "than read verbatim. Double-check them before pricing.")

    # While a refusal is active, disable Analyze so the (un-prefilled) form
    # can't be used to price the dropped basket/geared note. The flag clears
    # the moment the user changes the product type, edits the form, or uploads
    # a different PDF — manual single-name entry is never permanently blocked.
    submitted = st.form_submit_button(
        "🔍 Analyze", type="primary", width='stretch', disabled=refused)


# ----------------------------------------------------------------------------
# Validation (PRD §8.1)
# ----------------------------------------------------------------------------
def _validate(shared: dict, per_type_human: dict) -> list[str]:
    errors: list[str] = []
    if not shared["underlier"].strip():
        errors.append("Underlier ticker is required.")
    if shared["notional"] <= 0:
        errors.append("Notional must be positive.")
    if shared["maturity"] <= datetime.date.today():
        errors.append("Maturity date must be in the future.")
    if not shared["issuer"].strip():
        errors.append("Issuer is required.")
    if not shared["issuer_rating"].strip():
        errors.append("Issuer credit rating is required.")
    # Barrier/level bounds (0-200%) on pct/cap/rate-style fields.
    for (key, label, kind, _d, _h) in TYPE_FIELDS[product_type]:
        if kind in ("pct", "cap", "rate"):
            v = per_type_human[key]
            if v < 0 or v > 200:
                errors.append(f"{label} must be between 0% and 200%.")
    return errors


if submitted:
    shared = dict(
        underlier=underlier.strip(),
        notional=float(notional),
        maturity=maturity,
        issuer=issuer.strip(),
        issuer_rating=issuer_rating.strip(),
        offer_price=float(offer_pct) / 100.0,
    )
    errs = _validate(shared, per_type_human)
    if errs:
        for e in errs:
            st.error(e)
    else:
        # Build hashable cache keys (sorted tuples of primitives).
        shared_key = tuple(sorted({**shared, "maturity": maturity.isoformat()}.items()))
        per_type_key = tuple(sorted(per_type_human.items()))
        # FRED key (live mode only). The hashed token participates in the cache
        # key so toggling/changing the key re-runs; the raw secret is passed via
        # the underscore-prefixed arg, which st.cache_data excludes from the key.
        live_fred_key = "" if demo_mode else fred_key
        fred_token = _fred_key_token(live_fred_key)
        try:
            with st.spinner("Pricing… (Monte Carlo, Greeks, payoff curve)"):
                result = _price_cached(product_type, shared_key, per_type_key,
                                       demo_mode, int(n_paths),
                                       fred_token, (live_fred_key or None))
            st.session_state["result"] = result
            st.session_state["priced_meta"] = {
                "notional": shared["notional"],
                "product_label": PRODUCT_LABELS[product_type],
                "underlier": shared["underlier"],
                "demo_mode": demo_mode,
            }
            # PRD §8.5: stash the serializable pricing keys + a pinned timestamp
            # so the results page can build the export PDF (rebuilding the priced
            # product from the same primitives) without re-pricing, and so the
            # report's date/filename stay stable across reruns.
            st.session_state["report_keys"] = (
                product_type, shared_key, per_type_key, demo_mode, int(n_paths))
            st.session_state["report_generated"] = (
                datetime.datetime.now().isoformat())
        except MarketDataError as exc:
            # RCA-003 §7C: the backend now raises a SPECIFIC, user-safe message
            # for a genuine keyed-fetch failure — SSL/cert + one-line remedy,
            # "FRED API key appears invalid or unregistered.", or "Could not reach
            # FRED (network).". These never contain the API key (BACKEND_NOTES.md),
            # so we surface str(exc) verbatim as the HEADLINE cause instead of the
            # old generic "no data" message. The demo/FRED-key guidance drops to a
            # secondary line. We still never render a raw traceback.
            st.session_state["result"] = None
            st.error(f"Live market data unavailable: {exc}", icon="🚫")
            st.caption(
                "Tip: turn on **Use demo market data** in the sidebar for "
                "deterministic offline pricing, or check your **FRED API key** "
                "for the live Treasury curve.")
        except Exception as exc:  # defensive: friendly message, no traceback
            st.session_state["result"] = None
            st.error(f"Pricing failed: {exc}")


# ============================================================================
# RESULTS
# ============================================================================
result = st.session_state.get("result")
meta = st.session_state.get("priced_meta")

if result is None:
    st.divider()
    st.info("Fill in the term sheet above and click **Analyze** to see the "
            "decomposition, payoff diagram, and risk metrics.")
    st.stop()

notional = meta["notional"]

st.divider()
st.subheader("2 · Component decomposition")
# RCA-002 §7B.4 — inline LOW-CONFIDENCE notice when the static Treasury curve was
# used (no FRED key in live mode). This is NOT an error: pricing succeeded with a
# documented fallback curve. Independent of low_confidence_vol; render separately.
if getattr(result, "low_confidence_curve", False):
    # RCA-003 §7C.2: this banner is specifically the NO-KEY path. A keyed live
    # fetch that genuinely FAILS now raises MarketDataError (surfaced in the
    # error path above with its specific cause), so it never reaches this banner.
    # Keep this wording unambiguously about "no FRED key provided".
    st.warning(
        "⚠️ **Low-confidence Treasury curve — no FRED key provided.** A static "
        "fallback curve was used, so interest-rate inputs are approximate. Add a "
        "**FRED API key** in the sidebar (or set `FRED_API_KEY`) for the live "
        "curve.",
        icon="⚠️")
if getattr(result, "low_confidence_vol", False):
    st.warning("⚠️ Low-confidence volatility: the options chain was sparse, so a "
               "flat ATM vol was used. Treat the option value as approximate.")
for note in getattr(result, "notes", []) or []:
    st.caption(f"ℹ️ {note}")

# ----------------------------------------------------------------------------
# HERO verdict (UX feedback 2026-06-15): the embedded margin is the headline.
# Plain-language verdict from the INVESTOR's perspective. A positive margin
# (you pay above fair value) is BAD for the buyer, so we color it as a caution,
# not as "good/up". No ambiguous green↑/red↑ arrows — a clear worded verdict
# with a subtle colored callout.
# ----------------------------------------------------------------------------
headline, explanation, sentiment = _margin_verdict(result)
# Subtle sentiment color via Streamlit's status callouts:
#   bad-for-buyer (overpriced) -> warning (amber)
#   good-for-buyer (at/below fair value) -> success (green)
#   roughly fair -> neutral info
_hero = {"warn": st.warning, "good": st.success, "neutral": st.info}[sentiment]
st.markdown(f"### {headline}")
# Hero metric: the embedded margin, given the dominant (wide) slot. delta_color
# "off" avoids the ambiguous green↑/red↑ arrows flagged in user feedback — the
# colored verdict callout below carries the sentiment instead.
hero_col, _spacer = st.columns([2, 3])
# 2026-06-20 UX — polish #2: the "% of notional" descriptor used to ride in the
# metric's DELTA slot, which Streamlit renders as a colored ↑/↓ arrow even though
# it is NOT a change. Fold it into the value string and a caption instead, and
# pass NO delta — so the hero magnitude reads cleanly with no (even greyed) arrow.
hero_col.metric(
    "Embedded margin",
    f"{fmt_signed_currency(result.embedded_margin)} "
    f"({fmt_pct(result.margin_pct)} of notional)",
    help="Offer price minus fair value — the issuer's embedded fee. Positive "
         "means you pay more than the components are worth.")
hero_col.caption(f"{fmt_pct(result.margin_pct)} of notional.")
_hero(explanation)
st.caption("Embedded margin is the headline result: offer price minus the "
           "independently estimated fair value of the note's components.")

# ----------------------------------------------------------------------------
# Download PDF report (PRD §8.5) — the clean, analysis-only export. This replaces
# the browser Print button (which dumped the sidebar/settings too). Built
# server-side from the already-priced `result` (no re-pricing, no network) and
# cached on the same primitives as pricing + a pinned timestamp, so repeated
# reruns don't regenerate it. Failures degrade to a friendly warning — the
# results page never crashes and no secret/traceback is ever shown.
# ----------------------------------------------------------------------------
_report_keys = st.session_state.get("report_keys")
if _report_keys is not None:
    try:
        _r_ptype, _r_shared, _r_per_type, _r_demo, _r_paths = _report_keys
        _pdf_bytes, _pdf_fname = _report_bytes_cached(
            _r_ptype, _r_shared, _r_per_type, _r_demo, _r_paths,
            st.session_state.get("report_generated") or
            datetime.datetime.now().isoformat(),
            result,
        )
        st.download_button(
            "⬇️ Download PDF report",
            data=_pdf_bytes,
            file_name=_pdf_fname,
            mime="application/pdf",
            type="secondary",
            help="Download a clean, analysis-only PDF of this valuation "
                 "(decomposition, payoff, risk, market snapshot, methodology). "
                 "Use this instead of the browser Print button — it excludes the "
                 "Settings sidebar.",
        )
    except Exception as exc:  # never crash the results; never leak a traceback
        st.warning(
            f"Couldn't generate the PDF report: {exc} — the on-screen analysis "
            "above is unaffected.", icon="⚠️")

# Demoted breakdown — the three component metrics, now visually subordinate.
# 2026-06-20 UX — polish #2: the "% of notional" descriptor no longer rides in
# the DELTA slot (which rendered a misleading colored arrow). It now shows as an
# on-screen caption under each metric; the metrics carry no delta/arrow at all.
st.markdown("**Breakdown**")
b1, b2, b3 = st.columns(3)
b1.metric("Bond floor", fmt_currency(result.bond_floor))
b1.caption(fmt_pct(pct_of_notional(result.bond_floor, notional)) + " of notional")
b2.metric("Option value", fmt_currency(result.option_value))
b2.caption(fmt_pct(pct_of_notional(result.option_value, notional)) + " of notional")
b3.metric("Fair value", fmt_currency(result.fair_value))
b3.caption(fmt_pct(pct_of_notional(result.fair_value, notional)) + " of notional")

st.plotly_chart(charts.decomposition_bar(result, notional),
                width='stretch')

# 2026-06-20 UX — polish #1: a SHORT, secondary "how to read this" orientation
# note (how to read the chart itself), visually lighter (st.caption) and kept
# DISTINCT from the "What this means" finding callout (st.info) below. Order is
# consistent across all three charts: chart → 📖 How to read → What this means.
st.caption("📖 **How to read this:** A single stacked bar showing how your offer "
           "price splits into the bond floor, the option value, and the issuer's "
           "embedded margin — a taller margin segment = more you pay over fair "
           "value.")
# Plain-language takeaway for the decomposition chart (UX feedback): break the
# offer price into bond / option / margin shares so it's specific, not generic.
st.info("**What this means:** " + _decomposition_takeaway(result))


# ----------------------------------------------------------------------------
st.divider()
st.subheader("3 · Payoff at maturity")
st.plotly_chart(charts.payoff_diagram(result), width='stretch')
# 2026-06-20 UX — polish #1: single "how to read this" orientation caption. The
# prior technical blue/grey legend caption is FOLDED IN here so there aren't two
# overlapping caption lines saying similar things. Kept distinct from (and above)
# the "What this means" finding callout. Order matches the other two charts.
st.caption("📖 **How to read this:** X-axis = how the underlier moves by "
           "maturity, Y-axis = your total return. The solid blue line is this "
           "note (incl. coupons); the grey dotted line is a plain 1:1 position "
           "in the underlier, for comparison. Hover for exact values.")
# Plain-language takeaway (UX feedback): the finding, in lay terms.
st.info("**What this means:** " + _payoff_takeaway(result, meta))


# ----------------------------------------------------------------------------
st.divider()
st.subheader("4 · Risk metrics")

returns = result.return_distribution
mean_return_pct = (sum(returns) / len(returns) * 100.0) if returns else 0.0
max_loss_pct = (min(returns) * 100.0) if returns else 0.0

# UX feedback (2026-06-15): lead with the intuitive, plain-English metrics;
# Greeks stay visible but become the secondary, more-technical row. Each metric
# gets a one-line plain definition shown ON SCREEN (st.caption), not just in the
# hover `help=` tooltip.
r4, r5, r6 = st.columns(3)
r4.metric("P(loss)", fmt_pct(result.prob_loss * 100.0, dp=1),
          help="Fraction of Monte Carlo paths ending with principal loss.")
r4.caption("How often you end up losing money, across all simulated outcomes.")
r5.metric("Expected return", fmt_pct(mean_return_pct, dp=1),
          help="Mean total return across all simulated paths (over the life of "
               "the note).")
r5.caption("The average total return over the note's life across all scenarios.")
r6.metric("Max-loss scenario", fmt_pct(max_loss_pct, dp=1),
          help="Worst per-path total return observed in the simulation.")
r6.caption("The worst single outcome the simulation produced.")

st.markdown("**Sensitivities (Greeks)** — more technical")
r1, r2, r3 = st.columns(3)
r1.metric("Delta", f"${result.greeks.get('delta', 0):,.0f}",
          help="$ change in fair value per +1% move in the underlier.")
r1.caption("How much the note's value moves when the underlier moves.")
r2.metric("Vega", f"${result.greeks.get('vega', 0):,.0f}",
          help="$ change per +1 vol point.")
r2.caption("Sensitivity to changes in volatility.")
r3.metric("Rho", f"${result.greeks.get('rho', 0):,.0f}",
          help="$ change per +1bp parallel shift in the risk-free rate.")
r3.caption("Sensitivity to changes in interest rates.")

st.plotly_chart(charts.return_histogram(result), width='stretch')
# 2026-06-20 UX — polish #1: secondary "how to read this" orientation caption,
# distinct from the "What this means" finding below. Order consistent across charts.
st.caption("📖 **How to read this:** Each bar counts how many simulated scenarios "
           "landed in that return range; bars left of 0% are losses, bars right "
           "of 0% are gains.")
# Plain-language takeaway (UX feedback): reuse the SAME numbers shown in the
# metric cards above so the finding and the cards stay consistent.
st.info("**What this means:** " + _histogram_takeaway(
    result, mean_return_pct, max_loss_pct))


# ----------------------------------------------------------------------------
# Market inputs used (transparency)
# ----------------------------------------------------------------------------
with st.expander("Market inputs used in this valuation", expanded=True):
    m = st.columns(5)
    m[0].metric("Spot", f"${getattr(result, 'spot', float('nan')):,.2f}")
    m[1].metric("Risk-free", fmt_pct(getattr(result, 'risk_free', 0) * 100, 2))
    m[2].metric("Credit spread", fmt_pct(getattr(result, 'credit_spread', 0) * 100, 2))
    m[3].metric("Dividend yield", fmt_pct(getattr(result, 'div_yield', 0) * 100, 2))
    m[4].metric("ATM vol", fmt_pct(getattr(result, 'atm_vol', 0) * 100, 1))
    st.caption(("Demo (offline) market data." if meta["demo_mode"]
                else "Live market data (yfinance + FRED)."))

st.divider()
st.caption("Prism is an educational/research tool and does not constitute "
           "investment advice. Fair-value estimates depend on model assumptions "
           "and public market data (15–20 min delayed).")
