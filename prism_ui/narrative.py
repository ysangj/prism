"""Plain-language narrative helpers for the Prism UI (UX feedback 2026-06-15).

Turns a `DecompositionResult` into lay-terms findings:

  - `margin_verdict`      -> the hero headline + plain explanation + sentiment
  - `decomposition_takeaway` -> bond / option / margin shares of the offer price
  - `payoff_takeaway`     -> where the investor makes/loses money (qualitative)
  - `histogram_takeaway`  -> P(loss) / typical / worst, reusing the metric numbers

All money/percent rounding goes through the existing `formatting` helpers so the
verdict, the metric cards, and the chart takeaways stay numerically consistent.
Sentiment is framed from the INVESTOR's perspective: a positive embedded margin
(paying above fair value) is BAD for the buyer.
"""
from __future__ import annotations

from .formatting import fmt_currency, fmt_pct

# Below this absolute margin-%-of-notional the offer is "roughly fair value".
_NEAR_ZERO_PCT = 0.10


def margin_verdict(result) -> tuple[str, str, str]:
    """Return (headline, explanation, sentiment) for the hero verdict.

    sentiment is one of: "warn" (overpriced — bad for buyer), "good" (at/below
    fair value), "neutral" (roughly fair). The caller maps sentiment to a
    subtle colored callout — NOT to ambiguous up/down arrows.
    """
    margin = result.embedded_margin
    margin_pct = result.margin_pct
    abs_pct = abs(margin_pct)

    if abs_pct < _NEAR_ZERO_PCT:
        headline = "Priced roughly at fair value"
        explanation = (
            "The offer price is within a rounding margin of what the note's "
            "components are independently worth — no meaningful embedded fee "
            "detected for these inputs.")
        return headline, explanation, "neutral"

    if margin > 0:
        headline = f"Priced {fmt_pct(abs_pct)} above fair value"
        explanation = (
            f"You'd pay about {fmt_currency(margin)} more than the note's "
            "components are independently worth — that gap is the issuer's "
            "embedded fee, and it works against you as the buyer.")
        return headline, explanation, "warn"

    headline = f"Priced {fmt_pct(abs_pct)} below fair value"
    explanation = (
        f"The offer price is about {fmt_currency(abs(margin))} below the "
        "independently estimated value of the note's components — there is no "
        "positive embedded fee for these inputs.")
    return headline, explanation, "good"


def decomposition_takeaway(result) -> str:
    """One-line breakdown of the offer price into bond / option / margin shares."""
    offer = result.offer_price_dollars
    bond = result.bond_floor
    option = result.option_value
    margin = result.embedded_margin

    if not offer:
        return ("The offer price could not be split into components for these "
                "inputs.")

    bond_pct = bond / offer * 100.0
    option_pct = option / offer * 100.0
    margin_pct = margin / offer * 100.0

    if margin >= 0:
        margin_clause = (
            f"and about {fmt_pct(margin_pct, dp=0)} is the issuer's margin")
    else:
        margin_clause = (
            f"and the price sits about {fmt_pct(abs(margin_pct), dp=0)} below "
            "the value of those components (no positive margin)")
    return (
        f"Of your {fmt_currency(offer)} offer price, about "
        f"{fmt_pct(bond_pct, dp=0)} is the bond, "
        f"{fmt_pct(option_pct, dp=0)} is the option, {margin_clause}.")


def _downside_threshold(result) -> float | None:
    """Largest underlier drop (in % points, positive number) at which the note's
    return is still >= 0, i.e. roughly where principal loss begins on the
    downside. Derived from the payoff_curve; None if not determinable.
    """
    curve = result.payoff_curve or []
    # pairs of (underlier_pct, return_pct); look at the downside (x <= 0).
    downside = sorted((float(x), float(y)) for x, y in curve if float(x) <= 0)
    if not downside:
        return None
    # Walk from 0% downward; find the last x where return is still >= 0.
    last_ok = None
    for x, y in sorted(downside, key=lambda p: -p[0]):  # 0 -> most negative
        if y >= 0:
            last_ok = x
        else:
            break
    if last_ok is None:
        return None
    return abs(last_ok)


def payoff_takeaway(result, meta: dict | None = None) -> str:
    """Qualitative statement of the payoff shape from the investor's view."""
    underlier = (meta or {}).get("underlier") or "the underlier"
    thr = _downside_threshold(result)

    if thr is None or thr <= 0:
        downside = (f"You can start losing principal as soon as {underlier} "
                    "finishes below its starting level at maturity.")
    elif thr >= 99:
        downside = (f"Your principal is largely protected even if {underlier} "
                    "falls sharply by maturity.")
    else:
        downside = (f"You start losing principal if {underlier} finishes more "
                    f"than about {fmt_pct(thr, dp=0)} below its starting level "
                    "at maturity.")

    # Upside character from the top of the curve.
    curve = result.payoff_curve or []
    ys = [float(y) for _, y in curve]
    upside = ""
    if ys:
        top = max(ys)
        # Compare the note's best return to the best equity return on the grid.
        xs = [float(x) for x, _ in curve]
        equity_top = max(xs) if xs else top
        if top + 1e-9 < equity_top:
            upside = (" On the upside your gains are capped below a direct "
                      "position in the underlier.")
        else:
            upside = " On the upside you participate as the underlier rises."
    return downside + upside


def histogram_takeaway(result, mean_return_pct: float,
                       max_loss_pct: float) -> str:
    """P(loss) / typical / worst, reusing the exact numbers in the metric cards."""
    n = len(result.return_distribution or [])
    p_loss = fmt_pct(result.prob_loss * 100.0, dp=1)
    typical = fmt_pct(mean_return_pct, dp=1)
    worst = fmt_pct(max_loss_pct, dp=1)
    n_txt = f"{n:,}" if n else "many"
    return (
        f"Across {n_txt} simulated scenarios, you lose money about {p_loss} of "
        f"the time; the typical outcome is around {typical}, and the worst "
        f"simulated case is {worst}.")
