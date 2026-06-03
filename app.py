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

import datetime

import streamlit as st

from prism import price_product
from prism.market_data import MarketDataError
from prism.pdf_parser import PdfParseError, parse_term_sheet

from prism_ui import charts
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


st.set_page_config(page_title="Prism — Structured Product Decomposition",
                   page_icon="🔷", layout="wide")


# ----------------------------------------------------------------------------
# Cached pricing wrapper (PRD §15.6 — don't re-hit APIs on every rerun)
# ----------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def _price_cached(product_type: str, shared_key: tuple, per_type_key: tuple,
                  demo_mode: bool, n_paths: int):
    """Cache keyed on serializable inputs only.

    Rebuilds the product dataclass from primitive keys so the cache key is
    hashable and stable. Returns the DecompositionResult (a frozen-ish
    dataclass of primitives + lists, safe to cache).
    """
    shared = dict(shared_key)
    # maturity comes through as an ISO string in the key; restore to date.
    shared["maturity"] = datetime.date.fromisoformat(shared["maturity"])
    per_type_human = dict(per_type_key)
    product = build_product(product_type, shared, per_type_human)

    if demo_mode:
        return price_product(product, n_paths=n_paths, seed=DEMO_SEED,
                             **DEMO_MARKET)
    return price_product(product, n_paths=n_paths)


# ----------------------------------------------------------------------------
# Session state init
# ----------------------------------------------------------------------------
def _init_state():
    ss = st.session_state
    ss.setdefault("anthropic_key", "")
    ss.setdefault("product_type", DEFAULT_PRODUCT_TYPE)
    ss.setdefault("result", None)
    ss.setdefault("priced_meta", None)
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
    n_paths = st.select_slider(
        "Monte Carlo paths",
        options=[10_000, 25_000, 50_000, 100_000],
        value=50_000,
        help="More paths = smoother estimates, slower pricing. 100k targets "
             "<5s (PRD §9).",
    )

    st.divider()
    st.subheader("🔑 Anthropic API key (BYOK)")
    st.caption(
        "Used only for PDF term-sheet parsing. Stored in this session's memory "
        "only — never written to disk or logged (PRD §9)."
    )
    key_input = st.text_input(
        "Anthropic API key",
        value=st.session_state["anthropic_key"],
        type="password",
        placeholder="sk-ant-...",
        label_visibility="collapsed",
    )
    st.session_state["anthropic_key"] = key_input.strip()
    has_key = bool(st.session_state["anthropic_key"])

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
        if st.button("Extract fields from PDF", width='stretch'):
            with st.spinner("Parsing term sheet with Claude…"):
                try:
                    parsed = parse_term_sheet(uploaded.getvalue(),
                                              st.session_state["anthropic_key"])
                    _apply_parsed_fields(parsed)
                    st.success("Fields extracted. Review/edit below, then click "
                               "Analyze.")
                    st.rerun()
                except PdfParseError as exc:
                    st.error(str(exc))
                except Exception as exc:  # defensive: never leak a traceback
                    st.error(f"Could not parse the PDF: {exc}")


# ============================================================================
# HEADER
# ============================================================================
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
st.session_state["product_type"] = product_type

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

    submitted = st.form_submit_button("🔍 Analyze", type="primary",
                                      width='stretch')


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
        try:
            with st.spinner("Pricing… (Monte Carlo, Greeks, payoff curve)"):
                result = _price_cached(product_type, shared_key, per_type_key,
                                       demo_mode, int(n_paths))
            st.session_state["result"] = result
            st.session_state["priced_meta"] = {
                "notional": shared["notional"],
                "product_label": PRODUCT_LABELS[product_type],
                "underlier": shared["underlier"],
                "demo_mode": demo_mode,
            }
        except MarketDataError as exc:
            st.session_state["result"] = None
            st.error(
                f"Could not fetch market data: {exc}\n\n"
                "Tip: turn on **Use demo market data** in the sidebar, or set a "
                "`FRED_API_KEY` for the live Treasury curve.")
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
if getattr(result, "low_confidence_vol", False):
    st.warning("⚠️ Low-confidence volatility: the options chain was sparse, so a "
               "flat ATM vol was used. Treat the option value as approximate.")
for note in getattr(result, "notes", []) or []:
    st.caption(f"ℹ️ {note}")

d1, d2, d3, d4 = st.columns(4)
d1.metric("Bond floor", fmt_currency(result.bond_floor),
          fmt_pct(pct_of_notional(result.bond_floor, notional)) + " of notional")
d2.metric("Option value", fmt_currency(result.option_value),
          fmt_pct(pct_of_notional(result.option_value, notional)) + " of notional")
d3.metric("Fair value", fmt_currency(result.fair_value),
          fmt_pct(pct_of_notional(result.fair_value, notional)) + " of notional")
d4.metric(
    "Embedded margin", fmt_signed_currency(result.embedded_margin),
    fmt_pct(result.margin_pct) + " of notional",
    delta_color="inverse",
    help="Offer price minus fair value — the issuer's embedded fee. Positive "
         "means you pay more than the components are worth.")

st.plotly_chart(charts.decomposition_bar(result, notional),
                width='stretch')

if result.embedded_margin > 0:
    st.caption(f"You pay **{fmt_currency(result.embedded_margin)}** "
               f"({fmt_pct(result.margin_pct)} of notional) above the estimated "
               "fair value of the components.")
else:
    st.caption("The offer price is at or below the estimated fair value of the "
               "components (no positive embedded margin detected for these inputs).")


# ----------------------------------------------------------------------------
st.divider()
st.subheader("3 · Payoff at maturity")
st.plotly_chart(charts.payoff_diagram(result), width='stretch')
st.caption("Blue = this note's total return to the investor (incl. coupons). "
           "Grey dotted = a direct 1:1 position in the underlier, for comparison. "
           "Hover for exact values.")


# ----------------------------------------------------------------------------
st.divider()
st.subheader("4 · Risk metrics")

returns = result.return_distribution
mean_return_pct = (sum(returns) / len(returns) * 100.0) if returns else 0.0
max_loss_pct = (min(returns) * 100.0) if returns else 0.0

r1, r2, r3 = st.columns(3)
r1.metric("Delta", f"${result.greeks.get('delta', 0):,.0f}",
          help="$ change in fair value per +1% move in the underlier.")
r2.metric("Vega", f"${result.greeks.get('vega', 0):,.0f}",
          help="$ change per +1 vol point.")
r3.metric("Rho", f"${result.greeks.get('rho', 0):,.0f}",
          help="$ change per +1bp parallel shift in the risk-free rate.")

r4, r5, r6 = st.columns(3)
r4.metric("P(loss)", fmt_pct(result.prob_loss * 100.0, dp=1),
          help="Fraction of Monte Carlo paths ending with principal loss.")
r5.metric("Expected return", fmt_pct(mean_return_pct, dp=1),
          help="Mean total return across all simulated paths (over the life of "
               "the note).")
r6.metric("Max-loss scenario", fmt_pct(max_loss_pct, dp=1),
          help="Worst per-path total return observed in the simulation.")

st.plotly_chart(charts.return_histogram(result), width='stretch')


# ----------------------------------------------------------------------------
# Market inputs used (transparency)
# ----------------------------------------------------------------------------
with st.expander("Market inputs used in this valuation"):
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
