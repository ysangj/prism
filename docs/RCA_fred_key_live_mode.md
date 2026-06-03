# RCA & Bug Report — Live Market Data Hard-Fails Without a FRED Key

| | |
|---|---|
| **ID** | PRISM-RCA-002 |
| **Date** | 2026-06-03 |
| **Severity** | Medium (usability + docs/behavior mismatch) |
| **Status** | Open — awaiting fix |
| **Component** | `prism/market_data.py`, `prism/__init__.py`, `app.py`, `README.md` |
| **Owner** | backend-developer (fallback + key plumbing), ui-developer (sidebar field), orchestrator (docs/scope) |
| **Reporter** | Manual UI test (demo mode OFF) |

## 1. Summary

Turning **demo mode off** to use live market data throws `Could not fetch market data: FRED_API_KEY is not set…` and pricing fails entirely. There is currently **no way to use live data without exporting an env var and restarting**, and the documented graceful fallback does not exist in code.

## 2. Impact

- Live (non-demo) pricing is **unusable out of the box** — it hard-fails instead of degrading.
- **Docs/behavior mismatch:** `README.md` promises a static-curve fallback that the code does not implement (credibility/QA gap).
- Inconsistent UX: the Anthropic key is BYOK in the sidebar, but the FRED key is env-only.

## 3. Reproduction

1. Launch the app; in the sidebar untick **Use demo market data (offline)**.
2. Click **Analyze**.
3. Error: `FRED_API_KEY is not set; cannot fetch the Treasury curve…` and no result is produced.

**Expected:** live mode should either (a) degrade gracefully to a documented static curve flagged `low_confidence`, or (b) let the user supply a FRED key in-app — ideally both.

## 4. Root Cause Analysis

**Primary — documented fallback never implemented.** `get_treasury_curve` raises instead of falling back:

```248:253:prism/market_data.py
    key = api_key or os.environ.get("FRED_API_KEY")
    if not key:
        raise MarketDataError(
            "FRED_API_KEY is not set; cannot fetch the Treasury curve. "
            "Set the environment variable or pass api_key=..."
        )
```

But the README claims otherwise:

```53:53:README.md
| `FRED_API_KEY` | Optional | Live U.S. Treasury yield curve from FRED. Without it, Prism falls back to a documented static curve and flags the result `low_confidence`. Get a free key at <https://fred.stlouisfed.org/docs/api/api_key.html>. |
```

**Contributing — `price_product` doesn't catch it.** `_resolve_market_inputs` calls `get_treasury_curve()` with no `try/except` and no static fallback, so the exception propagates and live pricing dies (`prism/__init__.py`, risk-free resolution path ~L99–102).

**Contributing — FRED key not wired to the UI.** `get_treasury_curve(api_key=...)` accepts a key, and the sidebar already does BYOK for Anthropic (`app.py` ~L204–216), but there is no FRED key field and the app never passes one through.

**Contributing — no `.env` autoload.** The app reads `os.environ` only; nothing loads a local `.env`, though README L63 and `.gitignore` imply `.env` is the intended place for keys.

## 5. Decision (choose before delegating)

Recommended: **do all three** small fixes (they're complementary):

- **(A) Graceful fallback** — add a documented static Treasury curve; when no key/fetch fails, use it and set `low_confidence` + a note. Makes live mode never hard-fail and matches the README.
- **(B) In-app BYOK FRED key** — add a sidebar field mirroring the Anthropic key; pass it through to `get_treasury_curve(api_key=...)`.
- **(C) `.env` autoload** — load a local `.env` at startup so `FRED_API_KEY` (and optionally the Anthropic key) can live there.

If you only want one: **(A)** is the highest-value (fixes the crash and the docs mismatch).

## 6. TODO — Fixes

### A. Graceful fallback (backend-developer) — `prism/market_data.py`, `prism/__init__.py`
- [ ] Add a documented static fallback curve constant (representative CMT rates by tenor) with a clear source/date comment.
- [ ] In `get_treasury_curve`, when no key (or FRED fetch yields nothing): return the static curve and signal low-confidence (e.g. return value + flag, or a dedicated path) instead of only raising. Keep raising for genuinely broken fetches if a key *was* supplied.
- [ ] In `_resolve_market_inputs` (`prism/__init__.py`), wrap the curve fetch so a missing key falls back to the static curve, appends a `low_confidence`/notes entry, and never crashes pricing.

### B. In-app BYOK FRED key (ui-developer) — `app.py`
- [ ] Add a sidebar "FRED API key (optional)" field mirroring the Anthropic BYOK field (session-state only, never logged/persisted).
- [ ] Plumb the entered key into the live pricing path → `get_treasury_curve(api_key=...)` (extend the pricing wrapper to accept/forward it).
- [ ] When live + no key: show an inline notice that the static curve is being used (`low_confidence`), not a hard error.

### C. `.env` autoload (backend-developer)
- [ ] Load a local `.env` at app startup (e.g. `python-dotenv`) so `FRED_API_KEY` can be set there; keep `.env` git-ignored (already is). Add `python-dotenv` to `requirements.txt` if adopted.

### D. Docs alignment (orchestrator)
- [ ] Update `README.md` so the FRED behavior matches the chosen implementation (static fallback + low-confidence, and/or in-app key, and/or `.env`).
- [ ] Note the live-mode behavior (graceful degradation) in `PRD.md`/notes if scope-relevant (§15.6 graceful degradation).

### E. Tests (tester)
- [ ] Unit: `get_treasury_curve()` with no key returns the static curve + low-confidence flag (no raise) — and still raises on a real fetch failure when a key is provided (mock FRED).
- [ ] Integration: `price_product` in live path with no key completes using the static curve and sets `low_confidence` (no exception).
- [ ] UI smoke: demo OFF + no key → result renders with a "static curve / low confidence" notice, not an error; entering a FRED key uses the live curve.
- [ ] Confirm no key value is ever logged or written to disk.

## 7. Acceptance

- With demo mode OFF and **no** FRED key, Analyze produces a result flagged low-confidence (static curve) — no crash.
- Supplying a FRED key (sidebar or env/`.env`) uses the live FRED curve.
- README/PRD match actual behavior.
- Keys are never persisted or logged.

## 8. References

- `prism/market_data.py` L240–275 (`get_treasury_curve`), L226–237 (FRED series map).
- `prism/__init__.py` risk-free resolution in `_resolve_market_inputs` (~L99–102).
- `app.py` L85–87 (demo path), L188–194 (demo toggle), L204–216 (Anthropic BYOK field), L441–454 (error + tip).
- `README.md` L49–63 (API keys); PRD §15.6 (graceful degradation).
