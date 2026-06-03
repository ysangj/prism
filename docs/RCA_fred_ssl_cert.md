# RCA & Bug Report — Live FRED Fetch Silently Fails (SSL Cert + Swallowed Errors)

| | |
|---|---|
| **ID** | PRISM-RCA-003 |
| **Date** | 2026-06-03 |
| **Severity** | Medium-High (live market data unusable on a common macOS setup; root cause masked) |
| **Status** | Open — awaiting fix |
| **Component** | `prism/market_data.py` (`_fetch_fred_curve`, `get_treasury_curve`), `requirements.txt`, `README.md` |
| **Owner** | backend-developer (error handling + SSL/cert robustness), orchestrator (docs), tester (verification) |
| **Reporter** | Manual UI test (demo OFF, valid FRED key entered) |

## 1. Summary

With demo mode OFF and a **valid** FRED API key entered in the sidebar, live pricing fails with:

> `Could not fetch market data: FRED returned no Treasury data for any tenor`

The key is valid and FRED is healthy. The real failure is an **SSL certificate verification error** inside `fredapi`'s `urllib`-based fetch on the python.org macOS build, which has no CA trust store configured. Prism's `_fetch_fred_curve` **swallows every per-series exception** (`except Exception: continue`), so the SSL error is hidden and reported as a misleading "no data for any tenor".

## 2. Impact

- **Live Treasury curve is unusable** on a very common environment (python.org Python on macOS without `Install Certificates.command` run) — even with a correct, paid-free FRED key.
- **Misdiagnosable:** the surfaced message ("no Treasury data for any tenor") points users at their key or at FRED, when the true cause is local SSL trust. Cost a full debugging session to find.
- The same swallow-all masks other real failures identically: invalid/unregistered key, rate limiting, network outage, and individual-series gaps all collapse into one misleading message.

## 3. Reproduction

Environment: macOS, python.org framework build of Python 3.12, **`Install Certificates.command` not run**; venv with `fredapi 0.5.2`, `pandas 3.0.3`.

1. Launch app; untick **Use demo market data**.
2. Enter a valid FRED key in the sidebar **FRED API key** field.
3. Click **Analyze** → `FRED returned no Treasury data for any tenor`.

Confirmation that key + FRED are fine, and that it's an SSL/cert issue:

```bash
# Raw HTTPS via curl (uses system certs) — WORKS:
curl -s "https://api.stlouisfed.org/fred/series/observations?series_id=DGS10&api_key=<KEY>&file_type=json&sort_order=desc&limit=1"
# -> {"observations":[{"date":"2026-06-02","value":"4.46"}]}

# fredapi via the venv (urllib, no cert store) — FAILS:
.venv/bin/python -c 'from fredapi import Fred; Fred(api_key="<KEY>").get_series("DGS10")'
# -> ssl.SSLCertVerificationError: [SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer certificate

# fredapi pointed at certifi's CA bundle — WORKS:
SSL_CERT_FILE="$(.venv/bin/python -c 'import certifi;print(certifi.where())')" \
  .venv/bin/python -c 'from fredapi import Fred; print(Fred(api_key="<KEY>").get_series("DGS10").dropna().iloc[-1])'
# -> 4.46
```

## 4. Root Cause Analysis

**Primary — SSL trust store missing for `fredapi`.** `fredapi 0.5.2` fetches with `urllib.request.urlopen` (its `fred.py:__fetch_data`). On the python.org macOS build, Python ships its own OpenSSL and does **not** read the macOS Keychain; unless `certifi`'s bundle is wired in (via `Install Certificates.command` or `SSL_CERT_FILE`), `urlopen` raises `SSLCertVerificationError`. `curl` succeeds because it uses the system trust store — hence the confusing split.

**Primary — Prism masks the real error.** Every per-series exception is swallowed, so an SSL failure (which hits *every* series) is indistinguishable from a couple of unavailable series:

```291:305:prism/market_data.py
    fred = Fred(api_key=key)
    curve: dict[float, float] = {}
    for series_id, tenor in _FRED_TREASURY_SERIES.items():
        try:
            series = fred.get_series(series_id)
        except Exception:  # noqa: BLE001 - skip unavailable series, keep others
            continue
        if series is None or series.dropna().empty:
            continue
        latest = float(series.dropna().iloc[-1])
        # FRED reports CMT rates in percent; convert to a fraction.
        curve[tenor] = latest / 100.0

    if not curve:
        raise MarketDataError("FRED returned no Treasury data for any tenor")
```

When *all* series fail for the same systemic reason (SSL / bad key / outage), the loop produces an empty curve and the generic `"no Treasury data for any tenor"` message — discarding the actual exception that would have named the cause.

**Contributing — stale, fragile FRED client.** `fredapi 0.5.2` (with `pandas 3.0.3`) uses bare `urllib` and provides no way to pass an SSL context or a `requests` session, so it can't pick up `certifi` without environment-level configuration.

## 5. Immediate user workaround (no code change)

Run the python.org cert installer once (permanent), then relaunch:

```bash
/Applications/Python\ 3.12/Install\ Certificates.command
```

Or, per shell session:

```bash
export SSL_CERT_FILE="$(.venv/bin/python -c 'import certifi; print(certifi.where())')"
.venv/bin/python -m streamlit run app.py
```

## 6. TODO — Fixes

### A. Stop masking systemic failures (backend-developer) — `prism/market_data.py` `_fetch_fred_curve`
- [ ] Track per-series exceptions instead of blindly `continue`-ing. Keep skipping *individual* unavailable series, but if **zero** series succeed, raise a `MarketDataError` that includes the **last/most-common underlying exception** (type + message), e.g. `FRED fetch failed for all tenors: SSLCertVerificationError: ... unable to get local issuer certificate`.
- [ ] Detect and special-case the common classes so the message is actionable:
  - SSL cert verify failure → message pointing to the cert fix (Install Certificates.command / `SSL_CERT_FILE` / certifi).
  - HTTP 400/403 / "api_key" rejection → "FRED key appears invalid or unregistered."
  - Network/timeout → "Could not reach FRED (network)."
- [ ] Do **not** swallow the exception type for the all-fail case; chain it (`raise ... from exc`).

### B. Make HTTPS robust to the macOS cert gap (backend-developer)
- [ ] Ensure `certifi` is an explicit dependency and is actually used for FRED requests. Options (pick one):
  - Upgrade/replace the FRED client so requests go through `requests`/`httpx` (which use `certifi` by default), **or**
  - Before constructing `Fred(...)`, configure an SSL context from `certifi.where()` (e.g. set a default `ssl` context / `SSL_CERT_FILE` programmatically) so `urllib` trusts FRED without the user running the installer.
- [ ] Add `certifi` to `requirements.txt` (and `fredapi`/replacement pin) so a fresh `pip install -r requirements.txt` works out of the box.
- [ ] Re-verify against `pandas 3.0.3` (fredapi 0.5.2 is old; confirm no other deprecation breaks, or pin a compatible/newer client).

### C. Surface the cause in the UI (ui-developer) — `app.py`
- [ ] When live fetch raises, show the specific `MarketDataError` message (cert/key/network) rather than only the generic tip. Keep the existing "turn on demo / set FRED key" tip as a secondary line.
- [ ] (Optional) If the static-curve fallback path is taken because the *key fetch failed* (not just "no key"), make the low-confidence banner say *why* (e.g. "live fetch failed: <reason> — using static curve").

### D. Docs (orchestrator) — `README.md`
- [ ] Add a macOS troubleshooting note: if live FRED data fails with an SSL / "no Treasury data" error, run `Install Certificates.command` (or set `SSL_CERT_FILE` to `certifi`).
- [ ] Note that a valid FRED key + demo OFF is the live path, and that Prism now reports the specific failure reason.

### E. Tests (tester)
- [ ] Unit: `_fetch_fred_curve` where the FRED client raises `SSLCertVerificationError` for all series → raises `MarketDataError` whose message contains the SSL cause (not "no Treasury data for any tenor"). Mock `fredapi.Fred`.
- [ ] Unit: all series raise an auth-style error → message indicates an invalid/unregistered key.
- [ ] Unit: a *subset* of series unavailable but ≥1 succeeds → still returns a curve (no regression of the legitimate skip behavior).
- [ ] Integration/env: with `certifi` configured, a keyed live fetch succeeds (network test, or mocked transport).
- [ ] Confirm no key value appears in any raised message or log.

## 7. Acceptance

- On a fresh macOS python.org install, demo OFF + valid FRED key produces a live curve **without** the user manually running the cert installer (Fix B) — or, at minimum, fails with a message that explicitly names the SSL/cert cause and the one-line remedy (Fix A).
- Invalid key, network outage, and SSL failures each produce distinct, accurate messages.
- Partial-series outages still succeed.
- `pip install -r requirements.txt` yields a working live FRED path.
- No key material is ever logged or surfaced in errors.

## 8. References

- `prism/market_data.py` L277–305 (`_fetch_fred_curve`, swallow-all loop + generic raise), L308–328 (`get_treasury_curve` no-key static fallback / keyed live path), L243–274 (static fallback curve, PRISM-RCA-002).
- `app.py` live pricing path forwards the sidebar FRED key (`_fred_key_token`, `compute_*`), low-confidence banner (~L600–606).
- Related: `docs/RCA_fred_key_live_mode.md` (PRISM-RCA-002 — graceful fallback + BYOK key, already implemented).
- Observed exception: `ssl.SSLCertVerificationError: [SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer certificate`, raised from `fredapi/fred.py:__fetch_data` via `urllib`.
- Env at time of report: Python 3.12 (python.org framework build), `fredapi 0.5.2`, `pandas 3.0.3`.
