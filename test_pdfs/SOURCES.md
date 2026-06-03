# Test Fixture Sources — SEC EDGAR Structured-Product Term Sheets

Two real U.S. structured-product pricing supplements (Form 424B2) downloaded from
SEC EDGAR for use as PDF-parsing test fixtures. Both are single-product term sheets
(8 printed pages each), not base prospectuses.

All EDGAR requests used the header `User-Agent: prism-test syum1122@gmail.com`
with rate-limiting (sleeps between requests). Discovery used the EDGAR full-text
search API (`https://efts.sec.gov/LATEST/search-index`), and each filing's
`index.json` was fetched to confirm the exact primary-document filename before
downloading the HTML.

---

## 1. JPMorgan — Autocallable

- **File:** `jpm_autocallable_424b2_0001839882-24-040464.pdf`
- **Issuer:** JPMorgan Chase Financial Company LLC (guaranteed by JPMorgan Chase & Co.)
- **CIK (filer / guarantor):** 0001665650 / 0000019617
- **Product type:** Autocallable — "Auto Callable Contingent Interest Notes".
  Pays contingent interest coupons; the notes are automatically called if the
  underlyings close at or above the call threshold on a review date.
  Confirmed in the document text: "auto callable" (18x), "contingent interest" (50x),
  "automatically called" (20x), "review date" (56x).
- **Form:** 424B2 (preliminary pricing supplement)
- **EDGAR accession number:** 0001839882-24-040464
- **Filing date:** 2024-11-21
- **Primary document:** `jpm_424b2-23905.htm` (358 KB HTML)
- **Source URL:**
  https://www.sec.gov/Archives/edgar/data/1665650/000183988224040464/jpm_424b2-23905.htm
- **Filing index:**
  https://www.sec.gov/Archives/edgar/data/1665650/000183988224040464/
- **PDF result:** 554,598 bytes (542 KB), 8 pages, PDF v1.4 (header `%PDF-`, ends with `%%EOF`)
- **Conversion:** Headless Google Chrome from the locally downloaded HTML:
  `"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=old --disable-gpu --no-sandbox --user-data-dir=<tmp> --no-pdf-header-footer --print-to-pdf=<out>.pdf file:///<local>.html`

---

## 2. Citigroup — Reverse Convertible

- **File:** `citi_reverse_convertible_424b2_0000950103-23-008306.pdf`
- **Issuer:** Citigroup Global Markets Holdings Inc. (guaranteed by Citigroup Inc.)
- **CIK (filer / guarantor):** 0000200245 / 0000831001
- **Product type:** Reverse convertible — "Contingent Income Securities" (worst-of,
  Principal at Risk Securities). Pays a contingent coupon and is NOT auto-callable;
  if the worst-performing underlying is below its barrier at maturity, the holder is
  exposed to (and may be physically settled into) the underlying shares instead of
  receiving full principal — the defining reverse-convertible payoff.
  Confirmed in the document text: "Contingent Income Securities" (16x),
  "contingent coupon" (60x), "worst" (76x), "barrier" (23x), "underlying shares",
  "Principal at Risk Securities"; "callable"/"automatically called"/"auto-callable"
  all 0x.
- **Form:** 424B2 (pricing supplement, final)
- **EDGAR accession number:** 0000950103-23-008306
- **Filing date:** 2023-06-01 (priced May 30, 2023)
- **Primary document:** `dp194793_424b2-us2331552.htm` (154 KB HTML)
- **Source URL:**
  https://www.sec.gov/Archives/edgar/data/200245/000095010323008306/dp194793_424b2-us2331552.htm
- **Filing index:**
  https://www.sec.gov/Archives/edgar/data/200245/000095010323008306/
- **PDF result:** 614,217 bytes (600 KB), 8 pages, PDF v1.4 (header `%PDF-`, ends with `%%EOF`)
- **Conversion:** Headless Google Chrome from the locally downloaded HTML (same command as above).

---

## Notes

- Neither JPMorgan nor Citigroup uses the literal title "Reverse Convertible" on its
  shelf term sheets (that exact wording is used mainly by UBS / Goldman / Morgan
  Stanley / Barclays / Credit Suisse). Citigroup's structural equivalent is the
  non-auto-callable "Contingent Income Securities" (Principal at Risk Securities),
  which carries the defining reverse-convertible features: a contingent coupon plus
  downside exposure to / settlement in the worst-performing underlying's shares when
  it finishes below the barrier at maturity. That filing is the document used here.
- No project files outside `test_pdfs/` were modified. No packages were installed
  (Google Chrome was already present). PDF validation was done with an inline Python
  byte-scan (header `%PDF-`, trailing `%%EOF`, byte size, and page-tree `/Count`),
  cross-checked with the macOS `file` command (both report "PDF document, version 1.4,
  8 pages"), because no `.venv` / `pdfinfo` / `pypdf` was available on this machine.

---

# Additional Fixtures (single-underlier, supported products only)

Five more real Form 424B2 structured-product term sheets added as PDF-parsing
fixtures. Every one passed the single-underlier / supported-product filter:
exactly one stock or one broad index, product type in {accelerated/buffered,
digital/barrier, principal-protected, autocallable, reverse-convertible family},
no worst-of / best-of / basket, no "Airbag" / geared / >1.0x downside leverage.
Discovery used the EDGAR full-text search API; each filing's `index.json` was
fetched to confirm the real primary document before download; each document's
text was read to verify the single-underlier and no-gearing conditions.
All requests used header `User-Agent: prism-test syum1122@gmail.com` with
~0.25s sleeps between requests. Conversion (for all five): the primary HTML was
downloaded with the User-Agent header, then rendered to PDF with headless
Google Chrome:
`"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=old --disable-gpu --no-sandbox --user-data-dir=<tmp> --no-pdf-header-footer --print-to-pdf=<out>.pdf file://<local>.html`
Validation: macOS `file` reports "PDF document, version 1.4"; sizes are well over
50 KB; page counts were measured with `pypdf` (6.12.2) in
`/Users/sangjunyum/codes/prism/.venv` (installed for this task). The "8 pages"
that `file` prints comes from Chrome's embedded XMP metadata; the real page
count (pypdf `len(reader.pages)`) is reported below.

---

## 3. Royal Bank of Canada — Accelerated (single stock)

- **File:** `rbc_accelerated_NVDA_424b2_0000950103-25-005298.pdf`
- **Issuer:** Royal Bank of Canada (CIK 0001000275)
- **Product type:** Buffered / accelerated note — "Accelerated Return Notes(R)"
  (3-to-1 accelerated upside to a cap; plain 1-to-1 downside, no buffer/gearing).
- **Single underlier:** NVIDIA Corporation common stock — ticker **NVDA**.
- **Key economic params:** 3-to-1 upside participation; capped return 48.30%;
  1-to-1 downside (100% of principal at risk, no gearing, no buffer); maturity
  approx. 14 months (priced Apr 24, 2025; maturity June 26, 2026); $10/unit.
- **Form:** 424B2 (final pricing supplement, has CUSIP 78017M140)
- **EDGAR accession:** 0000950103-25-005298
- **Filing date:** 2025-04-28 (pricing date Apr 24, 2025)
- **Primary document:** `dp228115_424b2-mlzxe.htm`
- **Source URL:** https://www.sec.gov/Archives/edgar/data/1000275/000095010325005298/dp228115_424b2-mlzxe.htm
- **Filing index:** https://www.sec.gov/Archives/edgar/data/1000275/000095010325005298/
- **PDF result:** 489,520 bytes, 17 pages (pypdf), PDF v1.4.
- **Conversion:** headless Chrome from local HTML (command above).
- **Supported: YES — single-underlier (NVDA), no gearing.** (Verified: "worst" 0,
  "basket" 0, "least performing" 0, "airbag" 0, "geared" 0, "downside leverage" 0;
  downside described as "1-to-1".)

---

## 4. JPMorgan — Digital / Barrier (single stock)

- **File:** `jpm_digital_BAC_424b2_0001213900-25-058737.pdf`
- **Issuer:** JPMorgan Chase Financial Company LLC (guaranteed by JPMorgan Chase & Co.;
  filer CIK 0001665650, guarantor CIK 0000019617)
- **Product type:** Digital / barrier note — "Capped Digital Barrier Notes".
- **Single underlier:** Bank of America Corporation common stock — ticker **BAC**.
- **Key economic params:** digital return 15.00%; maximum return 31.30%; barrier
  amount $75.00 = 75.00% of initial (knock-in; below barrier => 1-to-1 loss, no
  gearing); due July 6, 2027.
- **Form:** 424B2 (preliminary pricing supplement)
- **EDGAR accession:** 0001213900-25-058737
- **Filing date:** 2025-06-27
- **Primary document:** `ea0247302-01_424b2.htm`
- **Source URL:** https://www.sec.gov/Archives/edgar/data/1665650/000121390025058737/ea0247302-01_424b2.htm
- **Filing index:** https://www.sec.gov/Archives/edgar/data/1665650/000121390025058737/
- **PDF result:** 751,475 bytes, 10 pages (pypdf), PDF v1.4.
- **Conversion:** headless Chrome from local HTML (command above).
- **Supported: YES — single-underlier (BAC), no gearing.** (Verified: "worst" 0,
  "basket" 0, "least performing" 0, "airbag" 0, "geared" 0, "downside leverage" 0;
  single "Common Stock of Bank of America Corporation" underlier.)

---

## 5. Jefferies — Principal-Protected Note (single index)

- **File:** `jefferies_ppn_DJIA_424b2_0001140361-25-000023.pdf`
- **Issuer:** Jefferies Financial Group Inc. (CIK 0000096223)
- **Product type:** Principal-protected note (PPN) — "Market Linked Notes —
  Leveraged Upside Participation to a Cap and Principal Return at Maturity".
- **Single underlier:** Dow Jones Industrial Average (single broad index) — **DJIA**.
- **Key economic params:** upside participation rate 125%; maximum return at least
  52.50%; 100% principal returned at maturity regardless of index performance
  (full principal protection, no downside exposure, no gearing); due Feb 4, 2031;
  $1,000/note.
- **Form:** 424B2 (preliminary pricing supplement)
- **EDGAR accession:** 0001140361-25-000023
- **Filing date:** 2025-01-02
- **Primary document:** `ef20040978_424b2.htm`
- **Source URL:** https://www.sec.gov/Archives/edgar/data/96223/000114036125000023/ef20040978_424b2.htm
- **Filing index:** https://www.sec.gov/Archives/edgar/data/96223/000114036125000023/
- **PDF result:** 775,977 bytes, 23 pages (pypdf), PDF v1.4.
- **Conversion:** headless Chrome from local HTML (command above).
- **Supported: YES — single-underlier (DJIA), no gearing.** (Verified: "worst" 0,
  "basket" 0, "least performing" 0, "airbag" 0, "geared" 0; single index = DJIA
  ("S&P 500" 0 occurrences); downside: principal fully returned.)

---

## 6. Royal Bank of Canada — Autocallable (single stock)

- **File:** `rbc_autocallable_AAPL_424b2_0000950103-25-008791.pdf`
- **Issuer:** Royal Bank of Canada (CIK 0001000275)
- **Product type:** Autocallable — "Trigger Autocallable GEARS" (auto-call if
  underlying >= initial on a call date; otherwise upside-geared participation;
  plain 1-to-1 downside below the trigger). The "GEARS"/"Gearing" applies to the
  UPSIDE only (Upside Gearing 1.4x); the downside is plain 1-to-1 (no airbag/geared
  downside).
- **Single underlier:** Apple Inc. common stock — ticker **AAPL**.
- **Key economic params:** Call Return 14.00%; Upside Gearing 1.4x (upside only);
  Downside Threshold 75% of initial; below threshold 1-to-1 loss (up to 100% at
  risk, no downside gearing); due July 14, 2028.
- **Form:** 424B2 (final pricing supplement, priced July 11, 2025)
- **EDGAR accession:** 0000950103-25-008791
- **Filing date:** 2025-07-14 (pricing supplement dated July 11, 2025)
- **Primary document:** `dp231578_424b2-ubseln1671.htm`
- **Source URL:** https://www.sec.gov/Archives/edgar/data/1000275/000095010325008791/dp231578_424b2-ubseln1671.htm
- **Filing index:** https://www.sec.gov/Archives/edgar/data/1000275/000095010325008791/
- **PDF result:** 453,196 bytes, 20 pages (pypdf), PDF v1.4.
- **Conversion:** headless Chrome from local HTML (command above).
- **Supported: YES — single-underlier (AAPL), no gearing.** (Verified: "worst" 0,
  "basket" 0, "least performing" 0, "airbag" 0; downside "proportionate to the
  negative Underlying Return" = 1-to-1; the only gearing is "Upside Gearing" 1.4x.)

---

## 7. GS Finance Corp. (Goldman Sachs) — Reverse-Convertible family (single stock)

- **File:** `gs_reverse-convertible_TSLA_424b2_0000886982-25-000028.pdf`
- **Issuer:** GS Finance Corp. (guaranteed by The Goldman Sachs Group, Inc.;
  filer CIK 0001419828, guarantor CIK 0000886982)
- **Product type:** Reverse-convertible / contingent-income family —
  "Autocallable Contingent Coupon (with Memory) Barrier Notes" (high contingent
  coupon + knock-in barrier with downside exposure at maturity; the defining
  reverse-convertible payoff, with an auto-call feature).
- **Single underlier:** Tesla, Inc. common stock — ticker **TSLA**.
- **Key economic params:** contingent coupon rate between 17.00% and 18.00% p.a.
  (memory); coupon barrier 60% of starting value; auto-call at/above starting
  value beginning ~3 months after pricing; downside threshold 60% (if final < 60%
  of start, i.e. drop > 40%, 1-to-1 loss up to 100%, no gearing); maturity approx.
  Aug 2026; $10/unit.
- **Form:** 424B2 (preliminary term sheet dated Aug 7, 2025; CUSIP 36271J583)
- **EDGAR accession:** 0000886982-25-000028
- **Filing date:** 2025-08-07
- **Primary document:** `tslaca80_prelim.htm`
- **Source URL:** https://www.sec.gov/Archives/edgar/data/1419828/000088698225000028/tslaca80_prelim.htm
- **Filing index:** https://www.sec.gov/Archives/edgar/data/1419828/000088698225000028/
- **PDF result:** 650,097 bytes, 25 pages (pypdf), PDF v1.4.
- **Conversion:** headless Chrome from local HTML (command above).
- **Supported: YES — single-underlier (TSLA), no gearing.** (Verified: "worst" 0,
  "basket" 0, "least performing" 0, "airbag" 0, "geared" 0, "downside leverage" 0,
  "leverage factor" 0; downside stated as "1-to-1 downside exposure".)

---

## Selection notes (additional fixtures)

- Target categories covered: (1) accelerated/buffered = RBC ARN on NVDA;
  (2) digital/barrier = JPM Capped Digital Barrier on BAC; (3) principal-protected
  = Jefferies Market Linked Notes on DJIA; (4) autocallable on a single U.S. stock
  (different issuer/underlier than the existing JPM fixture) = RBC Trigger
  Autocallable on AAPL; (5) reverse-convertible family on a single name (different
  issuer than the existing Citi fixture) = GS contingent-coupon barrier note on TSLA.
- Issuer variety: RBC (x2), JPMorgan, Jefferies, Goldman Sachs. Underlier variety:
  NVDA, BAC, DJIA, AAPL, TSLA. Four of five are single U.S.-listed stocks; the PPN
  is a single broad U.S. index (DJIA), which the boundary permits.
- Several strong-looking hits were DISCARDED for violating the single-underlier
  filter: a BofA ARN linked to a "Basket of 20 Cross-Sector Stocks"; a Morgan
  Stanley "Callable Contingent Income Securities" linked to the "Worst Performing
  of" AMZN/GOOG/AAPL/MSFT; and two issuer base prospectus supplements (UBS Trigger
  Yield Optimization, MS Auto Callable / Reverse Convertibles) that were generic
  templates rather than priced single-name term sheets.
- Only files under `test_pdfs/` were created/modified; `pypdf` was installed into
  the existing `.venv` for page-count validation.
