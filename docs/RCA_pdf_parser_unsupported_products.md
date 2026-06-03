# RCA & Bug Report — PDF Parser Silently Prices Unsupported Products

| | |
|---|---|
| **ID** | PRISM-RCA-001 |
| **Date** | 2026-06-02 |
| **Severity** | High (correctness / credibility) |
| **Status** | Accepted — fix via Option A (Refuse) |
| **Component** | `prism/pdf_parser.py`, `app.py`, `prism/__init__.py` |
| **Owner** | backend-developer (detection/guard), ui-developer (refusal surface), orchestrator (scope/PRD) |
| **Reporter** | Manual UI test (BYOK PDF upload) |

## 1. Summary

When a user uploads a term-sheet PDF whose product is outside Prism's modeling scope (multi-underlier basket, geared/"airbag" downside, worst-of, etc.), the parser **silently reduces it to the nearest supported single-underlier shape** and returns a confident-looking fair value. There is no warning that material features were dropped. For a tool whose value proposition is *independent, transparent valuation*, a silently-wrong number is a high-severity defect.

## 2. Impact

- **Wrong fair value / embedded margin** presented as authoritative.
- **No signal** to the user that the result is an approximation.
- Undermines the core trust thesis (regulator / advisor / academic audience).
- Affects the **PDF-upload path only**; manual entry is inherently single-name and unaffected.

## 3. Reproduction

1. Run the app in demo/offline mode.
2. Upload `sec.gov_..._ea0292976-01_424b2.htm.pdf` — JPMorgan **Airbag In-Digital Notes**, linked to an **unequally weighted basket of 5 indices** (EURO STOXX 50 40%, Nikkei 225 25%, FTSE 100 17.5%, SMI 10%, S&P/ASX 200 7.5%), with a 90% barrier and **1.11111× geared downside**.
3. Observe parsed fields: `underlier = SX5E` (only the 40% leg), product type `Barrier Note (Digital)`, barrier 90%, digital return 13.40%, type european.
4. Click Analyze → a fair value is produced with **no indication** that (a) 60% of the basket and (b) the airbag/gearing were discarded.

**Expected (post-fix):** Prism refuses to price, listing the reasons (basket, geared downside), and disables Analyze for the uploaded product.

## 4. Root Cause Analysis

**Primary cause — the parser is instructed to hide the problem.** The extraction prompt forces a basket into a single ticker:

```
- underlier:     primary underlying ticker symbol (e.g. "AAPL", "SPY"); if a basket/worst-of, use the primary or first ticker
```
(`prism/pdf_parser.py`, prompt line ~107)

**Contributing cause — the schema has no room for unsupported features.** The `barrier_note` field set cannot express a geared/airbag downside, so the information is structurally lost at extraction time:

```
"barrier_note": [
    "underlier", "notional", "maturity", "issuer", "issuer_rating",
    "offer_price", "fixed_return", "barrier", "barrier_type",
],
```
(`prism/pdf_parser.py`, lines ~68–71)

**Contributing cause — no capability gate.** `price_product` validates the *type* but nothing about multi-underlier / gearing, so anything that maps to a known dataclass is priced:

```
if not isinstance(product, (Autocallable, ReverseConvertible)):
    raise TypeError(...)
```
(`prism/__init__.py`, lines ~270–274 — note the message is also stale; all five types are now supported.)

**Minor — date conflation.** The prompt treats maturity and final valuation date as one field, so the displayed maturity can be the valuation date (`prism/pdf_parser.py`, line ~109).

**Minor — Bloomberg vs Yahoo tickers.** The doc gives Bloomberg tickers (`SX5E`, `NKY`, …); these won't resolve in yfinance live mode.

**5 Whys:** Wrong number shown → basket + airbag dropped → parser collapsed to one ticker & schema had no feature slots → prompt explicitly instructed the collapse and there was no capability check → the scope boundary was never defined or enforced anywhere in the pipeline.

## 5. Decision

Adopt **Option A — Refuse**: detect out-of-scope products and **block pricing** with a clear, reasoned message, rather than approximating. Rationale: for a transparency tool, "we can't value this" is acceptable; a silent wrong value is not. Engine work for baskets/airbags is deferred (see §9).

## 6. Support Boundary (the spec `check_supported` enforces)

**Prism will price** a product only if ALL hold:

- Exactly **one** underlying (no basket, no worst-of / best-of).
- Product type ∈ {autocallable, reverse_convertible, principal_protected, barrier_note, buffered_note}.
- **No downside gearing/leverage** on the protection (effective gearing = 1.0; the only leverage allowed is `buffered_note.upside_leverage` on the upside).
- No airbag / geared-buffer combinations beyond the modeled `buffer` (plain buffer) or `knock_in` semantics.
- `barrier_type` ∈ {european, american}.
- Underlier ticker resolves to a supported single equity/index.

**Prism will refuse** (list all that apply) if it detects: `basket` / multi-underlier, `worst_of`, `geared_downside` / `airbag`, `range_accrual`, `dual_directional`, `snowball`, or any feature it cannot map to the above.

## 7. TODO — Fixes (Option A)

### A. Parser detection (backend-developer) — `prism/pdf_parser.py`

- [ ] Rewrite `_PROMPT` to **stop** collapsing baskets (remove the "use the primary or first ticker" instruction). Instead require the model to return:
  - [ ] `num_underlyings` (int) and `is_basket` (bool)
  - [ ] `basket_constituents`: list of `{ticker, weight}` (for the message/report)
  - [ ] `unsupported_features`: list from a fixed vocabulary (`basket`, `worst_of`, `geared_downside`, `airbag`, `range_accrual`, `dual_directional`, `snowball`)
- [ ] Keep the "DO NOT GUESS / null when unsure" rule intact.
- [ ] Add `check_supported(extracted: dict) -> list[str]` — single source of truth for the §6 boundary; returns `[]` if priceable, else human-readable reasons.
- [ ] Add `UnsupportedProductError` (sibling/subclass of `PdfParseError`) carrying the reason list (never includes the API key).
- [ ] In `parse_term_sheet`, after extraction, call `check_supported`; raise `UnsupportedProductError` when non-empty (refuse at the parse boundary — before mapping to a dataclass, since dataclasses can't hold these fields).

### B. Engine backstop (backend-developer) — `prism/__init__.py`

- [ ] Refresh the stale guard message (lines ~270–274) to reflect the 5 supported types.
- [ ] (Defensive) If a future caller passes structured feature flags, reject in `price_product` too. Low priority since parse-boundary refusal is primary.

### C. Refusal surface (ui-developer) — `app.py`

- [ ] Catch `UnsupportedProductError`; render a clean panel: "Prism can't independently value this note:" + bulleted reasons.
- [ ] **Disable Analyze** for the uploaded product; do not pre-fill the form with the lossy single-name approximation.
- [ ] Offer the manual-entry path as the explicit alternative (single-name only).

### D. Minor correctness (backend-developer)

- [ ] Split date capture: `final_valuation_date` vs `maturity_date`; label maturity correctly (fixes the line-~109 conflation).
- [ ] Add Bloomberg→Yahoo ticker normalization (or warn on unresolved ticker) so live mode fails cleanly, not silently. Map e.g. `SX5E→^STOXX50E`, `NKY→^N225`, `UKX→^FTSE`, `SMI→^SSMI`, `AS51→^AXJO`.
- [ ] Mark inferred fields (e.g. `issuer_rating` defaulted to `A`) as low-confidence rather than presenting them as parsed.

### E. Scope & docs (orchestrator)

- [ ] Add the §6 support boundary to `PRD.md` (explicit in-scope / out-of-scope list + acceptance criteria).
- [ ] Note in `README.md` that PDF upload supports single-underlier products only; baskets / geared notes are refused.

### F. Tests (tester)

- [ ] Fixture: the Airbag In-Digital basket PDF → assert `UnsupportedProductError` with reasons `{basket, geared_downside}` (mock the Anthropic call; no live key).
- [ ] Regression: existing single-name JPM autocallable & Citi reverse-convertible fixtures still parse and price (guard against false positives).
- [ ] Unit tests for `check_supported` across the §6 matrix (each unsupported feature individually; a clean single-name passes).
- [ ] UI smoke: refusal panel renders and Analyze is disabled.

## 8. Test Plan (acceptance)

- Uploading the basket PDF yields a **refusal with correct reasons**, no fair value shown.
- Uploading a supported single-name term sheet still parses, pre-fills, and prices.
- `check_supported` returns `[]` for all supported fixtures and non-empty for each unsupported feature.
- No API key ever appears in any error message / log.

## 9. Out of Scope (future / not in Option A)

- Geared/airbag digital payoff math (single-underlier) — future "Option B" enhancement.
- Multi-underlier **basket** engine: correlated GBM (Cholesky), weighted basket level, per-constituent vol surfaces/data — tracked under PRD §4 Post-MVP (worst-of / multi-underlier).
- "Indicative result" labeling + acknowledge UX (belongs to Option B, not A).

## 10. References

- Filing: JPMorgan Airbag In-Digital Notes, 424B2, accession `0001213900-26-063599` (basket lines 49–56, 102–129; gearing/threshold lines 362–374; issuer estimated value $9.87/$10 lines 147–149).
- Code: `prism/pdf_parser.py` (prompt ~L95–128, schema ~L54–76, date ~L109), `prism/__init__.py` (guard ~L270–274), `prism/models.py` (dataclasses).
