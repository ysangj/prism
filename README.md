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

| Key | Required? | Purpose |
|-----|-----------|---------|
| `FRED_API_KEY` | Optional | Live U.S. Treasury yield curve from FRED. Without it, Prism falls back to a documented static curve and flags the result `low_confidence`. Get a free key at <https://fred.stlouisfed.org/docs/api/api_key.html>. |

```bash
export FRED_API_KEY=your-key-here
```

| Anthropic / Claude key | Optional (BYOK) | Only for the **PDF term-sheet upload** feature. You enter it directly in the app's sidebar — it is held in Streamlit session state only, never written to disk or logged (PRD §9). Create one at <https://console.anthropic.com/>. The rest of the app works without it. |

You do **not** need to export the Anthropic key — paste it into the app's sidebar at runtime.

**Never commit API keys.** Keep them in your shell env or a local `.env` that stays out of git.

## Run the web app

```bash
.venv/bin/python -m streamlit run app.py
```

Opens at <http://localhost:8501>. The form is pre-filled with the canonical 5-year AAPL autocallable and **Demo market data** is on by default, so you can click **Analyze** immediately — no network or API key required. Switch the product type to try the other four; toggle demo mode off to pull live market data; paste an Anthropic key in the sidebar to enable PDF term-sheet upload (sample term sheets live in [`test_pdfs/`](test_pdfs/)).

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

`price_product(product, seed=None, **market_overrides)` returns a `DecompositionResult` with `bond_floor`, `option_value`, `fair_value`, `embedded_margin`, `margin_pct`, `greeks`, `prob_loss`, `return_distribution`, and `payoff_curve`. Pass `seed=` for reproducibility and any of `spot=, risk_free=, div_yield=, credit_spread=, flat_vol=` to run fully offline; with none, it fetches live market data. The five product dataclasses are importable from `prism` (`Autocallable`, `ReverseConvertible`, `PrincipalProtected`, `BarrierNote`, `BufferedNote`). Full contract in [`BACKEND_NOTES.md`](BACKEND_NOTES.md).

## Run the tests

```bash
.venv/bin/python -m pytest tests/ -v
```

The suite covers the PRD §15.5 build-order checkpoints, both §15.7 acceptance cases (5-year value-band + 18-month structural), all five product types, the BYOK PDF parser (mocked, incl. API-key security checks), and the Streamlit UI (headless via `AppTest`). It runs **119 passed / 1 skipped** — the skip is the live FRED Treasury curve when no `FRED_API_KEY` is set. The deterministic, network-free tests must all pass.

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
