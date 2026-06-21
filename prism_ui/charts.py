"""Plotly figure builders for the Prism UI.

All figures are driven directly by `DecompositionResult` fields documented in
BACKEND_NOTES.md §3:
  - decomposition_bar  <- bond_floor, option_value, embedded_margin, offer
  - payoff_diagram     <- payoff_curve  [(underlier_pct, return_pct), ...]
  - return_histogram   <- return_distribution  [per-path total return fractions]
"""
from __future__ import annotations

import plotly.graph_objects as go

# Consistent palette.
_BOND_COLOR = "#2E86AB"     # blue
_OPTION_COLOR = "#A23B72"   # magenta
_MARGIN_COLOR = "#F18F01"   # amber
_EQUITY_COLOR = "#9aa0a6"   # grey
_NOTE_COLOR = "#2E86AB"     # blue


def decomposition_bar(result, notional: float) -> go.Figure:
    """Stacked bar: bond floor + option value + margin == offer price.

    Margin can be negative (offer below fair value); shown as a downward segment
    so the visual still reconciles to the offer price.
    """
    bond = result.bond_floor
    option = result.option_value
    margin = result.embedded_margin

    fig = go.Figure()
    fig.add_bar(
        name="Bond floor", x=["Offer price"], y=[bond],
        marker_color=_BOND_COLOR,
        hovertemplate="Bond floor: $%{y:,.0f}<extra></extra>",
    )
    fig.add_bar(
        name="Option value", x=["Offer price"], y=[option],
        marker_color=_OPTION_COLOR,
        hovertemplate="Option value: $%{y:,.0f}<extra></extra>",
    )
    fig.add_bar(
        name="Embedded margin", x=["Offer price"], y=[margin],
        marker_color=_MARGIN_COLOR,
        hovertemplate="Embedded margin: $%{y:,.0f}<extra></extra>",
    )

    # Reference line at the offer price.
    offer = result.offer_price_dollars
    fig.add_hline(
        y=offer, line_dash="dash", line_color="#444",
        annotation_text=f"Offer  ${offer:,.0f}",
        annotation_position="top left",
    )

    fig.update_layout(
        barmode="relative",
        title="Decomposition — what makes up the offer price",
        yaxis_title="Value ($)",
        legend_title_text="Component",
        height=420,
        margin=dict(l=60, r=20, t=60, b=40),
    )
    return fig


def payoff_diagram(result) -> go.Figure:
    """Investor total return vs underlier performance, with equity overlay.

    X: underlier performance (%), Y: total return to investor (%).
    Annotates breakeven (Y=0 crossing) where present.
    """
    curve = result.payoff_curve
    xs = [float(x) for x, _ in curve]
    ys = [float(y) for _, y in curve]

    fig = go.Figure()
    # Linear equity return overlay (1:1 with the underlier).
    fig.add_scatter(
        x=xs, y=xs, mode="lines", name="Equity (1:1)",
        line=dict(color=_EQUITY_COLOR, dash="dot", width=1.5),
        hovertemplate="Underlier %{x:.0f}%<br>Equity %{y:.1f}%<extra></extra>",
    )
    # Structured-product payoff.
    fig.add_scatter(
        x=xs, y=ys, mode="lines", name="This note",
        line=dict(color=_NOTE_COLOR, width=2.5),
        hovertemplate="Underlier %{x:.0f}%<br>Note return %{y:.1f}%<extra></extra>",
    )

    # Zero reference lines.
    fig.add_hline(y=0, line_color="#ccc", line_width=1)
    fig.add_vline(x=0, line_color="#ccc", line_width=1)

    # Breakeven annotation: first sign change in the note return.
    for i in range(1, len(ys)):
        if ys[i - 1] is None or ys[i] is None:
            continue
        if (ys[i - 1] < 0 <= ys[i]) or (ys[i - 1] > 0 >= ys[i]):
            # Linear interpolation for a cleaner breakeven x.
            x0, x1 = xs[i - 1], xs[i]
            y0, y1 = ys[i - 1], ys[i]
            be = x0 if y1 == y0 else x0 + (0 - y0) * (x1 - x0) / (y1 - y0)
            # Annotate at the BOTTOM of the plot so the breakeven label can't
            # collide with the title or the (now bottom-anchored) legend, which
            # previously crowded the top of the chart (2026-06-20 UX — polish #3).
            fig.add_vline(
                x=be, line_dash="dash", line_color="#c0392b",
                annotation_text=f"Breakeven {be:.0f}%",
                annotation_position="bottom",
            )
            break

    fig.update_layout(
        title="Payoff at maturity",
        xaxis_title="Underlier performance (%)",
        yaxis_title="Total return to investor (%)",
        height=480,
        hovermode="x unified",
        # Legend moved BELOW the plot (was top, y=1.02) so it no longer overlaps
        # the title / breakeven annotation. Extra bottom margin keeps it clear of
        # the x-axis title (2026-06-20 UX — polish #3).
        legend=dict(orientation="h", yanchor="top", y=-0.22,
                    xanchor="center", x=0.5),
        margin=dict(l=60, r=20, t=60, b=90),
    )
    return fig


def return_histogram(result) -> go.Figure:
    """Histogram of per-path total returns (fraction -> percent)."""
    returns_pct = [float(r) * 100.0 for r in result.return_distribution]

    fig = go.Figure()
    fig.add_histogram(
        x=returns_pct, nbinsx=60, marker_color=_OPTION_COLOR,
        opacity=0.85,
        hovertemplate="Return %{x:.0f}%<br>Paths %{y}<extra></extra>",
    )
    fig.add_vline(x=0, line_dash="dash", line_color="#c0392b",
                  annotation_text="0% (breakeven)", annotation_position="top")

    if returns_pct:
        mean_ret = sum(returns_pct) / len(returns_pct)
        fig.add_vline(x=mean_ret, line_dash="dot", line_color="#2E86AB",
                      annotation_text=f"Mean {mean_ret:.1f}%",
                      annotation_position="top right")

    fig.update_layout(
        title="Return distribution (Monte Carlo paths)",
        xaxis_title="Total return (%)",
        yaxis_title="Number of paths",
        height=400,
        margin=dict(l=60, r=20, t=60, b=50),
        bargap=0.02,
    )
    return fig
