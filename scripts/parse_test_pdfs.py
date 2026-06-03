#!/usr/bin/env python
"""BYOK harness: run the real PDF parser over every fixture in test_pdfs/.

This exercises the full upload path (Claude extraction -> support-boundary check
-> dataclass build -> offline pricing) against the real term sheets, so you can
see which PDFs Prism *accepts and prices* and which it *refuses* (multi-underlier
baskets, worst-of, geared/airbag downside).

The parser deliberately never reads the API key from the environment or disk, so
this dev harness passes it explicitly. Provide your key via --key or the
PRISM_TEST_ANTHROPIC_KEY environment variable (a name distinct from anything the
app uses). The key is used only for this run and is never written anywhere.

    python scripts/parse_test_pdfs.py --key sk-ant-...
    # or
    PRISM_TEST_ANTHROPIC_KEY=sk-ant-... python scripts/parse_test_pdfs.py

Without a key it just validates that each PDF loads and prints what would run.
"""

from __future__ import annotations

import argparse
import glob
import os
import sys
from datetime import date, timedelta

# Allow running as `python scripts/parse_test_pdfs.py` from the repo root: ensure
# the repo root (this file's parent's parent) is importable, not just scripts/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prism import (  # noqa: E402
    Autocallable,
    BarrierNote,
    BufferedNote,
    PrincipalProtected,
    ReverseConvertible,
    price_product,
)
from prism.pdf_parser import (  # noqa: E402
    PdfParseError,
    UnsupportedProductError,
    parse_term_sheet,
)

# Offline market snapshot so pricing never needs network/keys.
DEMO_MARKET = dict(
    spot=100.0, risk_free=0.045, div_yield=0.005, credit_spread=0.012, flat_vol=0.28
)

_TWO_YEARS = date.today() + timedelta(days=730)


def _f(value, default):
    return default if value is None else value


def _future(d):
    return d if isinstance(d, date) and d > date.today() else _TWO_YEARS


def build_product(parsed: dict):
    """Build a product dataclass from a parsed dict, filling any missing field
    with a sane default so the supported product can be priced for the demo."""
    pt = parsed.get("product_type")
    common = dict(
        underlier=_f(parsed.get("underlier"), "AAPL"),
        notional=_f(parsed.get("notional"), 1000.0),
        maturity=_future(parsed.get("maturity")),
        issuer=_f(parsed.get("issuer"), "Unknown Issuer"),
        issuer_rating=_f(parsed.get("issuer_rating"), "A"),
        offer_price=_f(parsed.get("offer_price"), 1.0),
    )
    if pt == "autocallable":
        return Autocallable(
            **common,
            coupon_rate=_f(parsed.get("coupon_rate"), 0.10),
            coupon_barrier=_f(parsed.get("coupon_barrier"), 0.70),
            call_barrier=_f(parsed.get("call_barrier"), 1.00),
            knock_in_barrier=_f(parsed.get("knock_in_barrier"), 0.60),
            observation_freq=_f(parsed.get("observation_freq"), "quarterly"),
        )
    if pt == "reverse_convertible":
        return ReverseConvertible(
            **common,
            coupon_rate=_f(parsed.get("coupon_rate"), 0.10),
            barrier=_f(parsed.get("barrier"), 0.70),
            barrier_type=_f(parsed.get("barrier_type"), "european"),
        )
    if pt == "principal_protected":
        return PrincipalProtected(
            **common,
            participation=_f(parsed.get("participation"), 1.00),
            cap=_f(parsed.get("cap"), 0.30),
            floor=_f(parsed.get("floor"), 1.00),
        )
    if pt == "barrier_note":
        return BarrierNote(
            **common,
            fixed_return=_f(parsed.get("fixed_return"), 0.15),
            barrier=_f(parsed.get("barrier"), 0.80),
            barrier_type=_f(parsed.get("barrier_type"), "european"),
        )
    if pt == "buffered_note":
        return BufferedNote(
            **common,
            upside_leverage=_f(parsed.get("upside_leverage"), 1.5),
            cap=_f(parsed.get("cap"), 0.25),
            buffer=_f(parsed.get("buffer"), 0.10),
        )
    raise ValueError(f"unknown product_type: {pt!r}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--key", default=os.environ.get("PRISM_TEST_ANTHROPIC_KEY"))
    ap.add_argument("--dir", default="test_pdfs")
    args = ap.parse_args()

    pdfs = sorted(glob.glob(os.path.join(args.dir, "*.pdf")))
    if not pdfs:
        print(f"No PDFs found in {args.dir}/")
        return 1

    print(f"Found {len(pdfs)} PDF(s) in {args.dir}/\n")

    if not args.key:
        print("No API key provided — listing fixtures only (no live parse).")
        print("Run with --key sk-ant-... (or PRISM_TEST_ANTHROPIC_KEY=...) to "
              "parse + price each PDF.\n")
        for p in pdfs:
            print(f"  • {os.path.basename(p)}  ({os.path.getsize(p) // 1024} KB)")
        return 0

    supported = refused = errored = 0
    for p in pdfs:
        name = os.path.basename(p)
        with open(p, "rb") as fh:
            data = fh.read()
        try:
            parsed = parse_term_sheet(data, args.key)
        except UnsupportedProductError as exc:
            refused += 1
            print(f"REFUSED   {name}")
            for r in exc.reasons:
                print(f"            - {r}")
            continue
        except PdfParseError as exc:
            errored += 1
            print(f"ERROR     {name}: {exc}")
            continue

        try:
            product = build_product(parsed)
            result = price_product(product, seed=42, **DEMO_MARKET)
        except Exception as exc:  # noqa: BLE001 - report, don't crash the run
            errored += 1
            print(f"ERROR     {name}: parsed but pricing failed: {exc}")
            continue

        supported += 1
        pt = parsed.get("product_type")
        und = parsed.get("underlier")
        inferred = parsed.get("inferred_fields") or []
        print(f"SUPPORTED {name}")
        print(f"            type={pt}  underlier={und}  "
              f"fair_value={result.fair_value:,.0f}  "
              f"margin={result.margin_pct:.2f}%  P(loss)={result.prob_loss:.0%}")
        if inferred:
            print(f"            inferred (verify): {', '.join(inferred)}")

    print(f"\nSummary: {supported} supported/priced, {refused} refused, "
          f"{errored} error, {len(pdfs)} total.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
