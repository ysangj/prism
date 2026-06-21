"""Audit-ready PDF valuation report export (PRD 8.5).

Builds a purpose-laid-out PDF from a priced analysis so users get a clean,
readable, downloadable report instead of dumping the whole Streamlit page via
the browser Print dialog.

Public API
----------
    from prism import build_report_pdf, report_filename
    pdf_bytes = build_report_pdf(product, result, meta=meta)
    name = report_filename(product, meta)

``build_report_pdf`` returns raw ``bytes`` (built into a ``BytesIO``) so the UI
can hand them straight to ``st.download_button``.

No new dependencies
-------------------
The whole report is rendered with ``reportlab`` (already a dependency). Charts
are drawn with ``reportlab.graphics`` (``Drawing`` + bar/line charts) — there is
**no** Plotly / kaleido / matplotlib here (those are not installed). When a chart
proves awkward for a given dataset we degrade gracefully to a table.

Determinism / secrets
---------------------
The report never calls ``datetime.now()`` when ``meta["generated_at"]`` is
supplied (so tests can pin the timestamp). No API key or secret is ever read or
embedded — the report only renders the market/diagnostic fields already present
on the ``DecompositionResult``.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from io import BytesIO

from reportlab.graphics.shapes import Drawing, Line, String
from reportlab.graphics.charts.barcharts import HorizontalBarChart
from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .models import (
    Autocallable,
    BarrierNote,
    BufferedNote,
    PrincipalProtected,
    ReverseConvertible,
    tenor_years,
)

__all__ = ["build_report_pdf", "report_filename"]

# ---------------------------------------------------------------------------
# Brand palette (matches the app's violet -> magenta lockup; kept professional)
# ---------------------------------------------------------------------------
_VIOLET = HexColor("#7c3aed")
_MAGENTA = HexColor("#c026d3")
_INK = HexColor("#1f2433")
_MUTED = HexColor("#5b6072")
_LIGHT = HexColor("#f3f0fb")  # pale violet row tint
_RULE = HexColor("#d9d2ee")
_GREEN = HexColor("#15803d")
_RED = HexColor("#b91c1c")

# Product-type human labels (mirrors prism_ui.config.PRODUCT_LABELS so the report
# reads the same as the app, but kept here so report.py has no UI dependency).
_PRODUCT_LABELS = {
    Autocallable: "Autocallable (Phoenix)",
    ReverseConvertible: "Reverse Convertible",
    PrincipalProtected: "Principal-Protected Note (PPN)",
    BarrierNote: "Barrier Note (Digital)",
    BufferedNote: "Buffered Note (Accelerated)",
}

_PRODUCT_SLUG = {
    Autocallable: "autocallable",
    ReverseConvertible: "reverse_convertible",
    PrincipalProtected: "principal_protected",
    BarrierNote: "barrier_note",
    BufferedNote: "buffered_note",
}

_DISCLAIMER = (
    "Educational / research estimate — not investment advice. Values are "
    "model estimates derived from public, delayed, or demo market data and may "
    "differ materially from dealer marks or realized outcomes. Prism is not a "
    "broker-dealer, investment adviser, or pricing service."
)


# ---------------------------------------------------------------------------
# Formatting helpers (mirror prism_ui.formatting; duplicated so report.py has no
# UI dependency, per the engine/UI ownership split).
# ---------------------------------------------------------------------------
def _fmt_currency(value) -> str:
    """Whole-dollar currency, e.g. 63747.09 -> '$63,747'. 'n/a' if missing."""
    try:
        return f"${float(value):,.0f}"
    except (TypeError, ValueError):
        return "n/a"


def _fmt_signed_currency(value) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "n/a"
    sign = "-" if v < 0 else ""
    return f"{sign}${abs(v):,.0f}"


def _fmt_pct(value, dp: int = 2) -> str:
    """Percent points -> 'x.xx%'. 'n/a' if missing."""
    try:
        return f"{float(value):.{dp}f}%"
    except (TypeError, ValueError):
        return "n/a"


def _fmt_frac_pct(value, dp: int = 1) -> str:
    """A fraction (0.70) rendered as a percent ('70.0%'). 'n/a' if missing."""
    try:
        return f"{float(value) * 100:.{dp}f}%"
    except (TypeError, ValueError):
        return "n/a"


def _fmt_mult(value) -> str:
    try:
        return f"{float(value):.2f}×"
    except (TypeError, ValueError):
        return "n/a"


def _pct_of(dollar_value, notional) -> str:
    """A $ amount as a percent of notional, e.g. '63.7% of notional'."""
    try:
        if not notional:
            return "n/a"
        return f"{float(dollar_value) / float(notional) * 100:.1f}%"
    except (TypeError, ValueError):
        return "n/a"


def _safe(value, fmt=str, default="n/a"):
    """Apply ``fmt`` to ``value`` unless it is None; degrade to ``default``."""
    if value is None:
        return default
    try:
        return fmt(value)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Filename helper
# ---------------------------------------------------------------------------
def _sanitize(token: str) -> str:
    """Lower-case, strip to [a-z0-9_-]; collapse runs; never empty."""
    token = re.sub(r"[^A-Za-z0-9]+", "_", str(token)).strip("_").lower()
    return token or "na"


def _generated_dt(meta: dict | None) -> datetime:
    """Resolve the report timestamp.

    Prefer ``meta['generated_at']`` (ISO string) so tests can pin it; otherwise
    stamp the current UTC time. Never raises on a malformed value.
    """
    if meta:
        raw = meta.get("generated_at")
        if isinstance(raw, datetime):
            return raw
        if isinstance(raw, str) and raw.strip():
            try:
                return datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                pass
    return datetime.now(timezone.utc)


def report_filename(product, meta: dict | None = None) -> str:
    """Default download filename, e.g.
    ``prism_report_aapl_autocallable_20260620.pdf``.

    Sanitized (no spaces, no secrets); date taken from ``meta['generated_at']``
    when present, else today (UTC).
    """
    underlier = _sanitize(getattr(product, "underlier", "underlier"))
    slug = _PRODUCT_SLUG.get(type(product), "product")
    stamp = _generated_dt(meta).strftime("%Y%m%d")
    return f"prism_report_{underlier}_{slug}_{stamp}.pdf"


# ---------------------------------------------------------------------------
# Paragraph styles
# ---------------------------------------------------------------------------
def _styles() -> dict:
    base = getSampleStyleSheet()
    s = {}
    s["title"] = ParagraphStyle(
        "PrismTitle", parent=base["Title"], fontName="Helvetica-Bold",
        fontSize=18, textColor=_VIOLET, spaceAfter=2, leading=22,
    )
    s["subtitle"] = ParagraphStyle(
        "PrismSubtitle", parent=base["Normal"], fontName="Helvetica",
        fontSize=11, textColor=_INK, spaceAfter=2, leading=14,
    )
    s["meta"] = ParagraphStyle(
        "PrismMeta", parent=base["Normal"], fontName="Helvetica",
        fontSize=8.5, textColor=_MUTED, leading=12,
    )
    s["tag"] = ParagraphStyle(
        "PrismTag", parent=base["Normal"], fontName="Helvetica-Bold",
        fontSize=8.5, textColor=_MAGENTA, leading=12, spaceBefore=4,
        spaceAfter=2,
    )
    s["h2"] = ParagraphStyle(
        "PrismH2", parent=base["Heading2"], fontName="Helvetica-Bold",
        fontSize=12, textColor=_VIOLET, spaceBefore=14, spaceAfter=6,
        leading=15,
    )
    s["body"] = ParagraphStyle(
        "PrismBody", parent=base["Normal"], fontName="Helvetica",
        fontSize=9, textColor=_INK, leading=13, alignment=TA_LEFT,
        spaceAfter=4,
    )
    s["verdict"] = ParagraphStyle(
        "PrismVerdict", parent=base["Normal"], fontName="Helvetica-Bold",
        fontSize=10.5, textColor=_INK, leading=14, spaceBefore=4,
        spaceAfter=4,
    )
    s["caption"] = ParagraphStyle(
        "PrismCaption", parent=base["Normal"], fontName="Helvetica-Oblique",
        fontSize=8, textColor=_MUTED, leading=11, spaceBefore=2, spaceAfter=8,
    )
    s["disclaimer"] = ParagraphStyle(
        "PrismDisclaimer", parent=base["Normal"], fontName="Helvetica",
        fontSize=8, textColor=_MUTED, leading=11, spaceAfter=4,
    )
    s["cell"] = ParagraphStyle(
        "PrismCell", parent=base["Normal"], fontName="Helvetica", fontSize=9,
        textColor=_INK, leading=12,
    )
    return s


# ---------------------------------------------------------------------------
# Table builder
# ---------------------------------------------------------------------------
def _kv_table(rows, styles, col_widths=None, value_align="RIGHT"):
    """A two-column key/value table with the house style (zebra rows)."""
    data = [
        [Paragraph(str(k), styles["cell"]), Paragraph(str(v), styles["cell"])]
        for k, v in rows
    ]
    if col_widths is None:
        col_widths = [2.7 * inch, 3.7 * inch]
    tbl = Table(data, colWidths=col_widths, hAlign="LEFT")
    ts = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (1, 0), (1, -1), value_align),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, _RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]
    for i in range(len(data)):
        if i % 2 == 0:
            ts.append(("BACKGROUND", (0, i), (-1, i), _LIGHT))
    tbl.setStyle(TableStyle(ts))
    return tbl


# ---------------------------------------------------------------------------
# Product summary rows (adapts per type)
# ---------------------------------------------------------------------------
def _common_summary_rows(product):
    try:
        tenor = tenor_years(product.maturity)
        tenor_str = f"{tenor:.2f} years"
    except Exception:  # noqa: BLE001 (degrade gracefully on a bad date)
        tenor_str = "n/a"
    return [
        ("Underlier", _safe(getattr(product, "underlier", None))),
        ("Notional", _fmt_currency(getattr(product, "notional", None))),
        ("Issuer", _safe(getattr(product, "issuer", None))),
        ("Issuer rating", _safe(getattr(product, "issuer_rating", None))),
        ("Maturity", _safe(getattr(product, "maturity", None), fmt=lambda d: d.strftime("%Y-%m-%d"))),
        ("Tenor", tenor_str),
        ("Offer price", f"{_fmt_frac_pct(getattr(product, 'offer_price', None), dp=2)} of par"),
    ]


def _type_summary_rows(product):
    """Type-specific term-sheet rows in human units."""
    if isinstance(product, Autocallable):
        return [
            ("Coupon rate (p.a.)", _fmt_frac_pct(product.coupon_rate, dp=2)),
            ("Coupon barrier", _fmt_frac_pct(product.coupon_barrier)),
            ("Call barrier", _fmt_frac_pct(product.call_barrier)),
            ("Knock-in barrier", _fmt_frac_pct(product.knock_in_barrier)),
            ("Observation frequency", _safe(product.observation_freq, fmt=lambda x: str(x).title())),
        ]
    if isinstance(product, ReverseConvertible):
        return [
            ("Coupon rate (p.a.)", _fmt_frac_pct(product.coupon_rate, dp=2)),
            ("Barrier", _fmt_frac_pct(product.barrier)),
            ("Barrier type", _safe(product.barrier_type, fmt=lambda x: str(x).title())),
        ]
    if isinstance(product, PrincipalProtected):
        cap = product.cap
        cap_str = "Uncapped" if not cap or cap <= 0 else _fmt_frac_pct(cap)
        return [
            ("Participation rate", _fmt_frac_pct(product.participation)),
            ("Cap", cap_str),
            ("Floor / protection", _fmt_frac_pct(product.floor)),
        ]
    if isinstance(product, BarrierNote):
        return [
            ("Fixed (digital) return", _fmt_frac_pct(product.fixed_return)),
            ("Barrier", _fmt_frac_pct(product.barrier)),
            ("Barrier type", _safe(product.barrier_type, fmt=lambda x: str(x).title())),
        ]
    if isinstance(product, BufferedNote):
        cap = product.cap
        cap_str = "Uncapped" if not cap or cap <= 0 else _fmt_frac_pct(cap)
        return [
            ("Upside leverage", _fmt_mult(product.upside_leverage)),
            ("Cap", cap_str),
            ("Downside buffer", _fmt_frac_pct(product.buffer)),
        ]
    return [("Parameters", "n/a")]


# ---------------------------------------------------------------------------
# Charts (reportlab.graphics)
# ---------------------------------------------------------------------------
def _decomposition_chart(result, notional):
    """Horizontal stacked bar: bond floor + option value + margin vs offer price.

    Returns a Drawing, or None to signal the caller should fall back to a table
    (defensive: any geometry problem degrades, never crashes the report).
    """
    try:
        bf = float(result.bond_floor)
        ov = float(result.option_value)
        margin = float(result.embedded_margin)
        offer = float(result.offer_price_dollars)
    except (TypeError, ValueError):
        return None

    # Stacked bar of the fair-value build-up; segments can be negative (a
    # negative embedded margin = offer below fair value), which a stacked bar
    # cannot render cleanly, so fall back to the table in that case.
    if margin < 0 or bf < 0 or ov < 0:
        return None

    d = Drawing(440, 150)
    chart = HorizontalBarChart()
    chart.x = 95
    chart.y = 30
    chart.width = 300
    chart.height = 95
    # One category ("Build-up") with three stacked series.
    chart.data = [[bf], [ov], [margin]]
    chart.categoryAxis.categoryNames = ["Fair-value\nbuild-up"]
    chart.categoryAxis.labels.fontSize = 8
    chart.categoryAxis.style = "stacked"
    chart.valueAxis.valueMin = 0
    top = max(offer, bf + ov + margin)
    chart.valueAxis.valueMax = top * 1.05
    chart.valueAxis.labels.fontSize = 7
    chart.valueAxis.labelTextFormat = lambda v: f"${v/1000:,.0f}k"
    chart.bars[0].fillColor = _VIOLET
    chart.bars[1].fillColor = _MAGENTA
    chart.bars[2].fillColor = HexColor("#f59e0b")
    chart.barWidth = 26
    d.add(chart)

    # Simple legend.
    legend = [
        ("Bond floor", _VIOLET),
        ("Option value", _MAGENTA),
        ("Embedded margin", HexColor("#f59e0b")),
    ]
    ly = 130
    for label, col in legend:
        d.add(Line(95, ly, 109, ly, strokeColor=col, strokeWidth=6))
        d.add(String(114, ly - 3, label, fontSize=7.5, fillColor=_INK))
        ly -= 12
    d.hAlign = "LEFT"
    return d


def _payoff_chart(result):
    """Line plot of the payoff curve (underlier move % vs investor return %).

    Returns a Drawing, or None to fall back to a sampled table.
    """
    curve = getattr(result, "payoff_curve", None)
    if not curve:
        return None
    try:
        pts = [(float(x), float(y)) for (x, y) in curve]
    except (TypeError, ValueError):
        return None
    if len(pts) < 2:
        return None

    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    if ymin == ymax:  # flat line -> pad so the axis renders
        ymin -= 1.0
        ymax += 1.0
    pad = (ymax - ymin) * 0.08

    d = Drawing(440, 200)
    plot = LinePlot()
    plot.x = 45
    plot.y = 30
    plot.width = 360
    plot.height = 145
    plot.data = [pts]
    plot.lines[0].strokeColor = _VIOLET
    plot.lines[0].strokeWidth = 1.6
    plot.xValueAxis.valueMin = xmin
    plot.xValueAxis.valueMax = xmax
    plot.xValueAxis.valueSteps = [v for v in (-50, -25, 0, 25, 50) if xmin <= v <= xmax]
    plot.xValueAxis.labels.fontSize = 7
    plot.xValueAxis.labelTextFormat = lambda v: f"{v:+.0f}%"
    plot.yValueAxis.valueMin = ymin - pad
    plot.yValueAxis.valueMax = ymax + pad
    plot.yValueAxis.labels.fontSize = 7
    plot.yValueAxis.labelTextFormat = lambda v: f"{v:+.0f}%"
    d.add(plot)

    # Axis captions.
    d.add(String(225, 6, "Underlier move at maturity (%)", fontSize=7.5,
                 fillColor=_MUTED, textAnchor="middle"))
    d.hAlign = "LEFT"
    return d


def _payoff_table(result, styles):
    """Sampled fallback table when the payoff chart can't be drawn."""
    curve = getattr(result, "payoff_curve", None) or []
    if not curve:
        return Paragraph("Payoff curve unavailable.", styles["body"])
    # Sample ~9 evenly spaced points.
    n = len(curve)
    idx = sorted(set(round(i * (n - 1) / 8) for i in range(9)))
    rows = [("Underlier move", "Investor return")]
    for i in idx:
        try:
            x, y = curve[i]
            rows.append((f"{float(x):+.0f}%", f"{float(y):+.2f}%"))
        except (TypeError, ValueError):
            continue
    return _kv_table(rows, styles, col_widths=[3.2 * inch, 3.2 * inch])


def _breakeven_caption(result, styles):
    """Caption naming the first breakeven crossing if easily derivable."""
    curve = getattr(result, "payoff_curve", None) or []
    be = None
    try:
        for (x0, y0), (x1, y1) in zip(curve, curve[1:]):
            y0, y1 = float(y0), float(y1)
            if (y0 <= 0 <= y1) or (y0 >= 0 >= y1):
                x0, x1 = float(x0), float(x1)
                if y1 != y0:
                    be = x0 + (0 - y0) * (x1 - x0) / (y1 - y0)
                else:
                    be = x0
                break
    except (TypeError, ValueError):
        be = None
    if be is None:
        txt = ("Investor total return at maturity vs. the underlier's move. "
               "No zero-return breakeven within the −50%..+50% window.")
    else:
        txt = (f"Investor total return at maturity vs. the underlier's move. "
               f"Approximate breakeven near an underlier move of {be:+.1f}%.")
    return Paragraph(txt, styles["caption"])


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------
def _verdict_line(result, notional):
    """Plain one-line verdict on the embedded margin."""
    try:
        pct = abs(float(result.margin_pct))
        margin = float(result.embedded_margin)
    except (TypeError, ValueError):
        return "Fair-value comparison unavailable."
    if margin > 0:
        return (f"Priced about {pct:.1f}% of notional ABOVE estimated fair "
                f"value (an embedded issuer margin of {_fmt_currency(margin)}).")
    if margin < 0:
        return (f"Priced about {pct:.1f}% of notional BELOW estimated fair "
                f"value ({_fmt_signed_currency(margin)}); the offer looks "
                f"cheap to model fair value at this tenor.")
    return "Priced at estimated fair value (no embedded margin)."


def _greek_rows(result):
    g = getattr(result, "greeks", None) or {}
    return [
        ("Delta", _safe(g.get("delta"), fmt=lambda v: f"{float(v):,.2f}"),
         "Sensitivity of fair value to a 1% move in the underlier."),
        ("Vega", _safe(g.get("vega"), fmt=lambda v: f"{float(v):,.2f}"),
         "Sensitivity to a 1 volatility-point change in implied volatility."),
        ("Rho", _safe(g.get("rho"), fmt=lambda v: f"{float(v):,.2f}"),
         "Sensitivity to a 1bp parallel shift in the yield curve."),
    ]


def _mean(seq):
    try:
        vals = [float(v) for v in seq]
        return sum(vals) / len(vals) if vals else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def build_report_pdf(product, result, *, meta: dict | None = None) -> bytes:
    """Render a priced analysis into an audit-ready PDF and return raw bytes.

    Parameters
    ----------
    product : one of the five product dataclasses (``Autocallable``,
        ``ReverseConvertible``, ``PrincipalProtected``, ``BarrierNote``,
        ``BufferedNote``).
    result : a :class:`prism.models.DecompositionResult`.
    meta : optional presentation context the UI may pass. Recognized keys:
        ``demo_mode`` (bool), ``generated_at`` (ISO timestamp string or
        ``datetime``), ``n_paths`` (int), ``data_source`` (str, e.g.
        ``"Demo (offline)"`` / ``"Live (yfinance + FRED)"``). Any key may be
        omitted; the report degrades gracefully. No secret/key is ever read.

    Returns
    -------
    bytes : a complete PDF document (``%PDF-`` .. ``%%EOF``).
    """
    meta = meta or {}
    styles = _styles()
    notional = getattr(product, "notional", None)
    label = _PRODUCT_LABELS.get(type(product), type(product).__name__)
    generated = _generated_dt(meta)
    gen_str = generated.strftime("%Y-%m-%d %H:%M %Z").strip() or generated.strftime("%Y-%m-%d")

    # Resolve the data-source label: explicit meta wins, else infer from demo_mode.
    data_source = meta.get("data_source")
    if not data_source:
        if meta.get("demo_mode") is True:
            data_source = "Demo (offline)"
        elif meta.get("demo_mode") is False:
            data_source = "Live (yfinance + FRED)"
        else:
            data_source = "n/a"
    n_paths = meta.get("n_paths")

    story = []

    # ---- 1. Header / title block ----
    story.append(Paragraph("Prism — Structured Product Valuation Report",
                           styles["title"]))
    story.append(Paragraph(
        f"{label} on {_safe(getattr(product, 'underlier', None))}",
        styles["subtitle"]))
    story.append(Paragraph(f"Generated {gen_str} &nbsp;|&nbsp; Data source: "
                           f"{data_source}", styles["meta"]))
    story.append(Paragraph(_DISCLAIMER, styles["tag"]))
    story.append(HRFlowable(width="100%", thickness=1.4, color=_VIOLET,
                            spaceBefore=4, spaceAfter=2))

    # ---- 2. Product summary ----
    story.append(Paragraph("Product summary", styles["h2"]))
    summary_rows = _common_summary_rows(product) + _type_summary_rows(product)
    story.append(_kv_table(summary_rows, styles))

    # ---- 3. Fair-value decomposition ----
    story.append(Paragraph("Fair-value decomposition", styles["h2"]))
    decomp_rows = [
        ("Bond floor",
         f"{_fmt_currency(result.bond_floor)}  ({_pct_of(result.bond_floor, notional)} of notional)"),
        ("Embedded option value",
         f"{_fmt_currency(result.option_value)}  ({_pct_of(result.option_value, notional)} of notional)"),
        ("Estimated fair value",
         f"{_fmt_currency(result.fair_value)}  ({_pct_of(result.fair_value, notional)} of notional)"),
        ("Offer price",
         f"{_fmt_currency(result.offer_price_dollars)}  ({_pct_of(result.offer_price_dollars, notional)} of notional)"),
        ("Embedded margin",
         f"{_fmt_signed_currency(result.embedded_margin)}  ({_fmt_pct(result.margin_pct)} of notional)"),
    ]
    story.append(_kv_table(decomp_rows, styles))
    story.append(Spacer(1, 6))
    story.append(Paragraph(_verdict_line(result, notional), styles["verdict"]))

    chart = _decomposition_chart(result, notional)
    if chart is not None:
        story.append(chart)
        story.append(Paragraph(
            "Stacked build-up of the fair value (bond floor + option value) plus "
            "the embedded margin, shown against the offer price.",
            styles["caption"]))
    else:
        story.append(Paragraph(
            "Chart omitted (a negative margin or component cannot be stacked); "
            "see the table above.", styles["caption"]))

    # ---- 4. Payoff at maturity ----
    story.append(Paragraph("Payoff at maturity", styles["h2"]))
    payoff = _payoff_chart(result)
    if payoff is not None:
        story.append(payoff)
        story.append(_breakeven_caption(result, styles))
    else:
        story.append(_payoff_table(result, styles))
        story.append(Paragraph("Sampled payoff points (chart unavailable).",
                               styles["caption"]))

    # ---- 5. Risk metrics ----
    story.append(Paragraph("Risk metrics", styles["h2"]))
    mean_ret = _mean(getattr(result, "return_distribution", None) or [])
    max_loss = None
    try:
        dist = [float(v) for v in (result.return_distribution or [])]
        max_loss = min(dist) if dist else None
    except (TypeError, ValueError):
        max_loss = None
    risk_rows = [
        ("Probability of principal loss",
         _safe(result.prob_loss, fmt=lambda v: f"{float(v) * 100:.1f}%")),
        ("Expected return (mean of simulated paths)",
         _safe(mean_ret, fmt=lambda v: f"{v * 100:+.2f}%")),
        ("Worst-case return (min of simulated paths)",
         _safe(max_loss, fmt=lambda v: f"{v * 100:+.2f}%")),
    ]
    story.append(_kv_table(risk_rows, styles))
    story.append(Spacer(1, 4))
    # Greeks with plain definitions.
    greek_data = [[Paragraph("Greek", styles["cell"]),
                   Paragraph("Value", styles["cell"]),
                   Paragraph("What it measures", styles["cell"])]]
    for name, val, defn in _greek_rows(result):
        greek_data.append([Paragraph(name, styles["cell"]),
                           Paragraph(val, styles["cell"]),
                           Paragraph(defn, styles["cell"])])
    gt = Table(greek_data, colWidths=[0.9 * inch, 1.0 * inch, 4.5 * inch],
               hAlign="LEFT")
    gt.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, 0), _VIOLET),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, _RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(gt)

    # ---- 6. Market data snapshot ----
    story.append(Paragraph("Market data snapshot", styles["h2"]))
    mkt_rows = [
        ("Spot (underlier)", _safe(result.spot, fmt=lambda v: f"${float(v):,.2f}")),
        ("Risk-free rate", _safe(result.risk_free, fmt=lambda v: f"{float(v) * 100:.2f}%")),
        ("Issuer credit spread", _safe(result.credit_spread, fmt=lambda v: f"{float(v) * 100:.2f}%")),
        ("Dividend yield", _safe(result.div_yield, fmt=lambda v: f"{float(v) * 100:.2f}%")),
        ("ATM volatility used", _safe(result.atm_vol, fmt=lambda v: f"{float(v) * 100:.1f}%")),
        ("Data source", data_source),
    ]
    if n_paths is not None:
        mkt_rows.append(("Monte Carlo paths", _safe(n_paths, fmt=lambda v: f"{int(v):,}")))
    story.append(_kv_table(mkt_rows, styles))

    # Caveats: low-confidence flags + engine notes.
    caveats = []
    if getattr(result, "low_confidence_vol", False):
        caveats.append("Volatility estimate flagged LOW CONFIDENCE (sparse "
                       "options chain).")
    if getattr(result, "low_confidence_curve", False):
        caveats.append("Risk-free curve from a static fallback (no FRED key) — "
                       "rates LOW CONFIDENCE.")
    for note in (getattr(result, "notes", None) or []):
        caveats.append(str(note))
    if caveats:
        story.append(Spacer(1, 4))
        story.append(Paragraph("Caveats", styles["verdict"]))
        for c in caveats:
            story.append(Paragraph(f"• {c}", styles["disclaimer"]))

    # ---- 7. Methodology & disclaimer ----
    story.append(Paragraph("Methodology &amp; disclaimer", styles["h2"]))
    n_paths_txt = f"{int(n_paths):,}" if n_paths is not None else "~100,000"
    methodology = (
        f"Bond floor: the issuer's promise to repay protected principal (and any "
        f"fixed cash flows), discounted at the risk-free rate plus the issuer's "
        f"credit spread. Embedded option: priced by Monte Carlo simulation of "
        f"the underlier under Geometric Brownian Motion ({n_paths_txt} paths), "
        f"averaging discounted payoffs across paths. Greeks (delta, vega, rho) "
        f"are computed by bump-and-reprice with common random numbers. Fair value "
        f"= bond floor + embedded option value; embedded margin = offer price "
        f"− fair value."
    )
    story.append(Paragraph(methodology, styles["body"]))
    story.append(Paragraph(
        "Data sources: equity spot / options / dividends via yfinance "
        "(Yahoo Finance, 15–20 min delayed); U.S. Treasury curve via the "
        "Federal Reserve (FRED). Demo mode uses fixed offline inputs for "
        "deterministic, network-free pricing.", styles["body"]))
    story.append(Paragraph(f"Report generated: {gen_str}.", styles["body"]))
    story.append(HRFlowable(width="100%", thickness=0.6, color=_RULE,
                            spaceBefore=4, spaceAfter=4))
    story.append(Paragraph(_DISCLAIMER, styles["disclaimer"]))

    # ---- Build ----
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        topMargin=0.7 * inch, bottomMargin=0.7 * inch,
        title="Prism Structured Product Valuation Report",
        author="Prism", subject="Structured product fair-value decomposition",
    )

    def _footer(canvas, doc_):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(_MUTED)
        canvas.drawString(0.75 * inch, 0.4 * inch,
                          "Prism — educational / research estimate, not investment advice.")
        canvas.drawRightString(letter[0] - 0.75 * inch, 0.4 * inch,
                               f"Page {doc_.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()
