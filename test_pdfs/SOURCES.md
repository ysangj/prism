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
