"""Display formatting helpers (PRD §15.6: currency -> whole $, percents 1-2 dp).

All rounding happens here at the display layer; engine values stay unrounded.
"""
from __future__ import annotations


def fmt_currency(value: float) -> str:
    """Whole-dollar currency, e.g. 63747.09 -> '$63,747'."""
    try:
        return f"${value:,.0f}"
    except (TypeError, ValueError):
        return "—"


def fmt_signed_currency(value: float) -> str:
    """Whole-dollar currency with explicit sign, e.g. -2881 -> '-$2,881'."""
    try:
        sign = "-" if value < 0 else ""
        return f"{sign}${abs(value):,.0f}"
    except (TypeError, ValueError):
        return "—"


def fmt_pct(value: float, dp: int = 2) -> str:
    """Percentage already expressed in percent points, e.g. 2.88 -> '2.88%'."""
    try:
        return f"{value:.{dp}f}%"
    except (TypeError, ValueError):
        return "—"


def pct_of_notional(dollar_value: float, notional: float) -> float:
    """Return a $ amount as a percent of notional (percent points)."""
    if not notional:
        return 0.0
    return dollar_value / notional * 100.0
