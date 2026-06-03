# Prism — Structured Product Pricing & Decomposition Engine

Prism decomposes a structured financial product (autocallables, reverse convertibles, and more) into its bond floor and embedded option(s), prices each independently from public market data, and reports the issuer's embedded margin — the gap between the offer price and fair value. It's an open, transparent alternative to institutional tools like Bloomberg OVAS.

> **Status: Phase 2 complete.** Prism now ships the full **Streamlit web app** plus the importable pricing library, covering all **five product types** — Autocallable, Reverse Convertible, Principal-Protected Note, Barrier (Digital) Note, and Buffered (Accelerated) Note — and BYOK Claude-powered PDF term-sheet extraction. See [`PRD.md`](PRD.md) §13 for the roadmap (Phase 3: PDF report export, methodology docs, deployment).

## Quickstart

```bash
# 1. set up the environment (first time only)
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. launch the web app
streamlit run app.py
```

Then open **<http://localhost:8501>** in your browser. The form is pre-filled with a sample product and **Demo market data** is on by default, so just click **Analyze** — no network or API key needed.

> Already inside the `.venv`? Just run `streamlit run app.py`. Prefer not to activate it? Use `.venv/bin/python -m streamlit run app.py`.

## What works today

- **Streamlit web app** (`app.py`): term-sheet input form, interactive Plotly payoff diagram, decomposition stacked-bar chart, and a risk dashboard
- **Five product types** priced end-to-end via `price_product()`
- **PDF term-sheet upload** — BYOK: upload an issuer term sheet and Claude extracts the parameters into the form for review
- Black-Scholes analytics (`bs_call`, `bs_put`, `bs_digital`)
- Vectorized Monte Carlo GBM simulator (100k paths, <0.5s)
- Bond floor valuation (risk-free + issuer credit spread discounting)
- Implied vol surface built from the live options chain (SVI-style smile)
- Greeks (delta, vega, rho via bump-and-reprice), P(loss), return distribution
- Market data via free public APIs: **yfinance** (spot, options, dividends, history) and **FRED** (Treasury curve)
- A **demo-data mode** that runs the whole app offline (no network, no keys)

## Prerequisites

- Python 3.11+
- Network access for live market data (yfinance, FRED). The engine also runs fully offline when you inject your own `MarketData` (see below).

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## API keys

Both keys are **optional** — the app is fully usable without either (demo mode needs nothing; live mode without a FRED key just uses a static curve).

| Key | Used for | How to provide it |
|-----|----------|-------------------|
| `FRED_API_KEY` | Live U.S. Treasury yield curve (FRED). **Without it, live mode does not fail** — Prism falls back to a built-in static curve and flags the result low-confidence. Free key at <https://fred.stlouisfed.org/docs/api/api_key.html>. | Any of: enter it in the app sidebar ("FRED API key"), put `FRED_API_KEY=...` in a local `.env` (auto-loaded at startup), or `export FRED_API_KEY=...`. |
| Anthropic / Claude key | The **PDF term-sheet upload** feature only (BYOK). | Paste it into the app sidebar at runtime. |

Both keys are held in Streamlit session state only — never written to disk or logged (PRD §9). The sidebar value overrides the environment/`.env` value.

```bash
# optional — only if you want the live FRED curve from the shell or a .env file
export FRED_API_KEY=your-key-here
# or create a .env file (auto-loaded; stays git-ignored):
echo 'FRED_API_KEY=your-key-here' > .env
```

**Never commit API keys.** Keep them in your shell env or a local `.env` that stays out of git.

### Live FRED data on macOS (SSL/certificates)

The live Treasury-curve fetch verifies HTTPS against the bundled `certifi` CA store, so it works out of the box after `pip install -r requirements.txt` — no extra setup needed. If you still see an SSL / certificate error when pulling live FRED data (older python.org builds whose certs were never installed), either run the one-time installer or point Python at certifi:

```bash
# one-time, permanent (python.org build):
/Applications/Python\ 3.12/Install\ Certificates.command
# or per shell session:
export SSL_CERT_FILE="$(.venv/bin/python -c 'import certifi; print(certifi.where())')"
```

When a live fetch fails, the app now reports the **specific** cause — SSL/cert (with this remedy), an invalid/unregistered key, or a network problem — instead of a generic error. With no FRED key at all, it just uses the static fallback curve (low-confidence).

## Run the web app

```bash
.venv/bin/python -m streamlit run app.py
```

Opens at <http://localhost:8501>. The form is pre-filled with the canonical 5-year AAPL autocallable and **Demo market data** is on by default, so you can click **Analyze** immediately — no network or API key required. Switch the product type to try the other four; toggle demo mode off to pull live market data (works even without a FRED key — it uses a static curve and shows a low-confidence notice); paste an Anthropic key in the sidebar to enable PDF term-sheet upload (sample term sheets live in [`test_pdfs/`](test_pdfs/)).

## PDF term-sheet upload — what can and can't be parsed

With your Anthropic key entered, you can upload an issuer term sheet (pricing supplement / 424B2) and Claude extracts the parameters into the form for your review before pricing. Because Prism's value is an *independent, transparent* valuation, it **refuses to price** anything it can't model accurately rather than silently approximating it.

**Supported — Prism will parse and price a note only if all of these hold:**
- Exactly **one underlying** (a single stock or index).
- One of the five product types: **autocallable, reverse convertible, principal-protected, barrier (digital), buffered**.
- **No downside gearing/leverage** on the protection (1:1 downside). The only leverage modeled is a buffered note's *upside* participation.
- A plain `buffer` or `knock-in` downside — no airbag / geared-buffer variants.
- A European or American barrier.

**Refused — the upload is blocked with the reasons listed, and no value is shown** — for:
- **Multi-underlier baskets**, **worst-of / best-of** notes.
- **Geared / "airbag" downside** (e.g. a 1.11× loss multiplier).
- Range accrual, dual-directional, and snowball structures, or any feature outside the list above.

When a PDF is refused, the app explains why and points you to manual entry for a single-underlier note. Inferred/uncertain fields (e.g. a maturity date that had to be guessed) are flagged "⚠️ Inferred (please verify)" so you can double-check them. Multi-underlier and geared products are on the roadmap ([`PRD.md`](PRD.md) §4 Post-MVP).

> Your API key is held in session memory only — never written to disk or logged (PRD §9). Term-sheet contents are sent to the Anthropic API solely to extract the parameters.

## Library usage

### Offline / deterministic (inject market data)

Pass market overrides and a fixed `seed` to price without any network calls — reproducible for tests and demos:

```python
from datetime import date
from prism import price_product
from prism.models import Autocallable

product = Autocallable(
    underlier="AAPL", notional=100_000, maturity=date(2027, 11, 30),
    issuer="JPMorgan Chase", issuer_rating="A", offer_price=1.0,
    coupon_rate=0.095, coupon_barrier=0.70, call_barrier=1.00,
    knock_in_barrier=0.60, observation_freq="quarterly",
)

result = price_product(
    product, seed=42,
    spot=190.0, risk_free=0.045, div_yield=0.005,
    credit_spread=0.012, flat_vol=0.28,
)
print(f"Fair value: {result.fair_value:,.0f}  |  margin {result.margin_pct:.1f}%")
```

### Live pricing

```python
from datetime import date
from prism import price_product
from prism.models import Autocallable

product = Autocallable(
    underlier="AAPL", notional=100_000, maturity=date(2027, 11, 30),
    issuer="JPMorgan Chase", issuer_rating="A", offer_price=1.0,
    coupon_rate=0.095, coupon_barrier=0.70, call_barrier=1.00,
    knock_in_barrier=0.60, observation_freq="quarterly",
)

result = price_product(product)   # fetches live market data for AAPL
print(f"Fair value:      {result.fair_value:,.0f}")
print(f"Embedded margin: {result.margin_pct:.1f}% of notional")
print(f"P(loss):         {result.prob_loss:.0%}")
```

`price_product(product, seed=None, **market_overrides)` returns a `DecompositionResult` with `bond_floor`, `option_value`, `fair_value`, `embedded_margin`, `margin_pct`, `greeks`, `prob_loss`, `return_distribution`, and `payoff_curve`. Pass `seed=` for reproducibility and any of `spot=, risk_free=, div_yield=, credit_spread=, flat_vol=` to run fully offline; with none, it fetches live market data. Pass `fred_api_key=` to use the live Treasury curve (otherwise a static fallback curve is used and `result.low_confidence_curve` is set). The five product dataclasses are importable from `prism` (`Autocallable`, `ReverseConvertible`, `PrincipalProtected`, `BarrierNote`, `BufferedNote`). Full contract in [`BACKEND_NOTES.md`](BACKEND_NOTES.md).

## Run the tests

```bash
.venv/bin/python -m pytest tests/ -v
```

The suite covers the PRD §15.5 build-order checkpoints, both §15.7 acceptance cases (5-year value-band + 18-month structural), all five product types, the BYOK PDF parser (mocked, incl. API-key security checks and the unsupported-product refusal boundary), and the Streamlit UI (headless via `AppTest`). It runs **172 passed / 1 skipped** — the skip is the live FRED Treasury curve when no `FRED_API_KEY` is set. The deterministic, network-free tests must all pass.

## Project layout

```
app.py            # Streamlit web app
prism_ui/         # UI helpers (form config, Plotly charts, formatting)
prism/            # core pricing engine (importable package)
  pricing/        # black_scholes, monte_carlo, payoffs
  models.py       # product dataclasses + DecompositionResult
  market_data.py  # yfinance + FRED fetchers
  pdf_parser.py   # BYOK Claude term-sheet extraction
  vol_surface.py  bond_floor.py  risk.py
tests/            # pytest suite
test_pdfs/        # real EDGAR term-sheet PDFs (fixtures + manual PDF-upload testing)
PRD.md            # full product spec & roadmap
```
