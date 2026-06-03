"""Per-product-type form configuration + unit conversion.

The form shows inputs in *human* units (percentages as 70, coupon as 9.5,
leverage as 1.5x). This module converts those to the *fraction* convention the
`prism` dataclasses expect (0.70, 0.095, 1.5) before construction.

Field spec tuple format (per per-type field):
    (key, label, kind, default_human, help)
where `kind` is one of:
    "pct"   -> shown as percent points (e.g. 70), stored as fraction (0.70)
    "rate"  -> shown as percent points (e.g. 9.5), stored as fraction (0.095)
    "mult"  -> shown as a multiple (e.g. 1.5), stored as-is (1.5)
    "freq"  -> observation frequency selectbox
    "btype" -> barrier type selectbox (european/american)
    "cap"   -> like pct but 0 means "uncapped"

Defaults are PRD §15.7 canonical-style values so the form is immediately usable.
"""
from __future__ import annotations

from prism import (
    Autocallable,
    BarrierNote,
    BufferedNote,
    PrincipalProtected,
    ReverseConvertible,
)

OBSERVATION_FREQS = ["monthly", "quarterly", "semiannual", "annual"]
BARRIER_TYPES = ["european", "american"]

# Maps the pdf_parser / selector product_type string -> dataclass.
PRODUCT_CLASSES = {
    "autocallable": Autocallable,
    "reverse_convertible": ReverseConvertible,
    "principal_protected": PrincipalProtected,
    "barrier_note": BarrierNote,
    "buffered_note": BufferedNote,
}

# Human-friendly display labels for the product-type selector.
PRODUCT_LABELS = {
    "autocallable": "Autocallable (Phoenix)",
    "reverse_convertible": "Reverse Convertible",
    "principal_protected": "Principal-Protected Note (PPN)",
    "barrier_note": "Barrier Note (Digital)",
    "buffered_note": "Buffered Note (Accelerated)",
}

# Per-type field specs. (key, label, kind, default_human, help)
TYPE_FIELDS: dict[str, list[tuple]] = {
    "autocallable": [
        ("coupon_rate", "Coupon rate (% p.a.)", "rate", 9.5,
         "Annualized contingent coupon."),
        ("coupon_barrier", "Coupon barrier (%)", "pct", 70.0,
         "Level (% of initial) above which a coupon is paid."),
        ("call_barrier", "Call barrier (%)", "pct", 100.0,
         "Level at/above which the note auto-calls (early redemption)."),
        ("knock_in_barrier", "Knock-in barrier (%)", "pct", 60.0,
         "Final level below which principal is at risk."),
        ("observation_freq", "Observation frequency", "freq", "quarterly",
         "How often the autocall/coupon is observed."),
    ],
    "reverse_convertible": [
        ("coupon_rate", "Coupon rate (% p.a.)", "rate", 9.5,
         "Annualized fixed coupon."),
        ("barrier", "Barrier (%)", "pct", 70.0,
         "Level below which principal converts to shares."),
        ("barrier_type", "Barrier type", "btype", "european",
         "European = tested at maturity; American = path-monitored."),
    ],
    "principal_protected": [
        ("participation", "Participation rate (%)", "pct", 100.0,
         "Fraction of positive underlier performance captured."),
        ("cap", "Cap (%)", "cap", 30.0,
         "Max upside as % of notional. Enter 0 for uncapped."),
        ("floor", "Floor / protection (%)", "pct", 100.0,
         "Protected principal fraction at maturity (100% = full protection)."),
    ],
    "barrier_note": [
        ("fixed_return", "Fixed (digital) return (%)", "pct", 20.0,
         "Digital payout if the barrier condition holds."),
        ("barrier", "Barrier (%)", "pct", 80.0,
         "Level of the initial price defining the barrier."),
        ("barrier_type", "Barrier type", "btype", "european",
         "European = terminal level only; American = path-monitored."),
    ],
    "buffered_note": [
        ("upside_leverage", "Upside leverage (x)", "mult", 1.5,
         "Upside participation multiple (1.5 = 150%)."),
        ("cap", "Cap (%)", "cap", 25.0,
         "Max upside as % of notional. Enter 0 for uncapped."),
        ("buffer", "Downside buffer (%)", "pct", 10.0,
         "Loss absorbed before principal is at risk (first 10%)."),
    ],
}

# Per-type fields whose units the pdf_parser returns as *fractions* and which we
# must therefore display back as percent points (multiply by 100) when
# auto-populating the form. "mult" / "freq" / "btype" are passed through as-is.
_PARSED_KIND = {
    key: kind
    for fields in TYPE_FIELDS.values()
    for (key, _label, kind, _default, _help) in fields
}


def human_to_fraction(key: str, kind: str, human_value):
    """Convert one human-unit input to the engine's fraction/value convention."""
    if kind in ("pct", "rate"):
        return float(human_value) / 100.0
    if kind == "cap":
        # 0 or negative -> uncapped (engine treats <=0 as uncapped).
        return float(human_value) / 100.0
    if kind == "mult":
        return float(human_value)
    if kind in ("freq", "btype"):
        return human_value
    return human_value


def fraction_to_human(kind: str, fraction_value):
    """Inverse of human_to_fraction, for displaying parsed/loaded values."""
    if fraction_value is None:
        return None
    if kind in ("pct", "rate", "cap"):
        return float(fraction_value) * 100.0
    if kind == "mult":
        return float(fraction_value)
    return fraction_value


def parsed_value_to_human(key: str, fraction_value):
    """Map a pdf_parser per-type field (fraction) back to human units for the form."""
    kind = _PARSED_KIND.get(key)
    if kind is None:
        return fraction_value
    return fraction_to_human(kind, fraction_value)


def build_product(product_type: str, shared: dict, per_type_human: dict):
    """Construct the right dataclass from shared params + human-unit per-type params.

    `shared` keys: underlier, notional, maturity (date), issuer, issuer_rating,
                   offer_price (fraction).
    `per_type_human` keys: the per-type field keys with HUMAN-unit values.
    """
    cls = PRODUCT_CLASSES[product_type]
    kwargs = dict(shared)
    for (key, _label, kind, _default, _help) in TYPE_FIELDS[product_type]:
        kwargs[key] = human_to_fraction(key, kind, per_type_human[key])
    return cls(**kwargs)
