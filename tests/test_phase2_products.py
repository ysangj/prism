"""Phase 2: end-to-end pricing + structural properties for ALL FIVE product types.

PRD §4 (5 product structures), §8.1 (per-type params), §15.7 (acceptance).
BACKEND_NOTES §9 (Phase 2 product contract).

All tests are deterministic and network-free (explicit market overrides + seed).
They assert the *structural* properties of each payoff (PRD §4 / BACKEND_NOTES §9),
not fragile exact numbers.
"""
from __future__ import annotations

import datetime as dt

import pytest

from prism import price_product
from prism.models import (
    Autocallable,
    BarrierNote,
    BufferedNote,
    DecompositionResult,
    PrincipalProtected,
    ReverseConvertible,
)

SEED = 12345


def _maturity(years: float) -> dt.date:
    return dt.date.today() + dt.timedelta(days=int(round(years * 365.25)))


# Shared deterministic offline market (BACKEND_NOTES §3 / UI demo profile).
OFFLINE = dict(spot=200.0, risk_free=0.045, div_yield=0.005,
               credit_spread=0.009, flat_vol=0.28)


def _shared(**kw):
    base = dict(underlier="AAPL", notional=100_000, issuer="JPMorgan Chase",
                issuer_rating="A", offer_price=1.0)
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# Product fixtures (one per type)
# ---------------------------------------------------------------------------
@pytest.fixture
def autocallable():
    return Autocallable(maturity=_maturity(5.0), coupon_rate=0.095,
                        coupon_barrier=0.70, call_barrier=1.00,
                        knock_in_barrier=0.60, observation_freq="quarterly",
                        **_shared())


@pytest.fixture
def reverse_convertible():
    return ReverseConvertible(maturity=_maturity(2.0), coupon_rate=0.09,
                              barrier=0.70, barrier_type="european", **_shared())


@pytest.fixture
def ppn():
    return PrincipalProtected(maturity=_maturity(3.0), participation=1.0,
                              cap=0.30, floor=1.0, **_shared())


@pytest.fixture
def barrier_note_eu():
    return BarrierNote(maturity=_maturity(3.0), fixed_return=0.20, barrier=0.80,
                       barrier_type="european", **_shared())


@pytest.fixture
def barrier_note_am():
    return BarrierNote(maturity=_maturity(3.0), fixed_return=0.20, barrier=0.80,
                       barrier_type="american", **_shared())


@pytest.fixture
def buffered_note():
    return BufferedNote(maturity=_maturity(5.0), upside_leverage=1.5, cap=0.25,
                        buffer=0.10, **_shared())


# ---------------------------------------------------------------------------
# Full-population check applied to EVERY product type (PRD §15.2 / §15.7).
# ---------------------------------------------------------------------------
def _assert_fully_populated(res: DecompositionResult, *, prob_loss_open=True):
    assert isinstance(res, DecompositionResult)
    for fld in ("bond_floor", "option_value", "fair_value",
                "offer_price_dollars", "embedded_margin", "margin_pct",
                "greeks", "prob_loss", "return_distribution", "payoff_curve"):
        assert getattr(res, fld) is not None, f"{fld} is None"
    # Greeks complete and numeric.
    assert {"delta", "vega", "rho"} <= set(res.greeks)
    for g in ("delta", "vega", "rho"):
        assert res.greeks[g] is not None
        assert isinstance(res.greeks[g], (int, float))
    # Curves non-empty.
    assert len(res.payoff_curve) > 0
    assert len(res.return_distribution) > 0
    # prob_loss in [0,1].
    assert 0.0 <= res.prob_loss <= 1.0
    if prob_loss_open:
        assert 0.0 < res.prob_loss < 1.0
    # Decomposition identities (PRD §6.3).
    assert res.fair_value == pytest.approx(res.bond_floor + res.option_value, abs=1e-6)
    assert res.embedded_margin == pytest.approx(
        res.offer_price_dollars - res.fair_value, abs=1e-6)
    # Diagnostics populated.
    for fld in ("spot", "risk_free", "credit_spread", "div_yield", "atm_vol"):
        assert getattr(res, fld) is not None, f"diagnostic {fld} is None"


def _price(product, n_paths=50_000):
    return price_product(product, n_paths=n_paths, seed=SEED, **OFFLINE)


def _payoff_at(res, underlier_pct, tol=1.0):
    """Return the payoff-curve return_pct nearest underlier_pct (within tol)."""
    best = min(res.payoff_curve, key=lambda p: abs(p[0] - underlier_pct))
    assert abs(best[0] - underlier_pct) <= tol, (
        f"no payoff point near {underlier_pct}% (closest {best[0]}%)")
    return best[1]


# === 1. Autocallable =======================================================
def test_autocallable_fully_populated(autocallable):
    _assert_fully_populated(_price(autocallable))


# === 2. Reverse Convertible ================================================
def test_reverse_convertible_fully_populated(reverse_convertible):
    _assert_fully_populated(_price(reverse_convertible))


# === 3. Principal Protected (PPN) ==========================================
def test_ppn_fully_populated(ppn):
    # Fully protected -> prob_loss ~ 0, so open-interval check is relaxed.
    res = _price(ppn)
    _assert_fully_populated(res, prob_loss_open=False)


def test_ppn_prob_loss_near_zero(ppn):
    """PRD §4 / BACKEND_NOTES §9.1: full principal protection -> prob_loss ~ 0."""
    res = _price(ppn)
    assert res.prob_loss <= 0.01, f"PPN prob_loss={res.prob_loss} should be ~0"


def test_ppn_bond_floor_dominant(ppn):
    """BACKEND_NOTES §9.1: protected floor makes the bond floor dominant."""
    res = _price(ppn)
    assert res.bond_floor > res.option_value, (
        f"bond_floor {res.bond_floor:.0f} should dominate option_value "
        f"{res.option_value:.0f} for a fully-protected PPN")


def test_ppn_no_downside_loss(ppn):
    """A −30% underlier move must not produce investor loss (floor=100%)."""
    res = _price(ppn)
    down = _payoff_at(res, -30.0)
    assert down >= -0.001, f"PPN should protect principal; got {down}% at −30%"


def test_ppn_upside_capped(ppn):
    """Upside capped at +30% (cap=0.30) even for a large up move."""
    res = _price(ppn)
    up = _payoff_at(res, 50.0)
    assert up <= 30.0 + 1e-6, f"PPN upside {up}% exceeds the 30% cap"


# === 4. Barrier Note (Digital) =============================================
def test_barrier_note_fully_populated(barrier_note_eu):
    _assert_fully_populated(_price(barrier_note_eu))


def test_barrier_digital_pays_fixed_above_barrier(barrier_note_eu):
    """PRD §4 / BACKEND_NOTES §9.2: fixed digital return above the barrier."""
    res = _price(barrier_note_eu)
    # At 0% underlier move the terminal level (100%) is >= 80% barrier -> +20%.
    at_par = _payoff_at(res, 0.0)
    assert at_par == pytest.approx(20.0, abs=0.5), (
        f"digital payout at 0% move should be +20%, got {at_par}%")


def test_barrier_principal_at_risk_below_barrier(barrier_note_eu):
    """Below the barrier principal tracks the underlier (loss)."""
    res = _price(barrier_note_eu)
    down = _payoff_at(res, -40.0)
    assert down == pytest.approx(-40.0, abs=1.0), (
        f"below-barrier payoff should track underlier (~−40%), got {down}%")


def test_barrier_american_prob_loss_ge_european(barrier_note_eu, barrier_note_am):
    """BACKEND_NOTES §9.2: American (path-monitored) breaches >= European."""
    res_eu = _price(barrier_note_eu)
    res_am = _price(barrier_note_am)
    assert res_am.prob_loss >= res_eu.prob_loss - 1e-9, (
        f"American prob_loss {res_am.prob_loss} should be >= European "
        f"{res_eu.prob_loss}")


# === 5. Buffered Note (Accelerated) ========================================
def test_buffered_note_fully_populated(buffered_note):
    _assert_fully_populated(_price(buffered_note))


def test_buffered_upside_capped(buffered_note):
    """PRD §4 / BACKEND_NOTES §9.3: leveraged upside capped at the cap."""
    res = _price(buffered_note)
    up = _payoff_at(res, 50.0)
    assert up == pytest.approx(25.0, abs=0.5), (
        f"buffered upside should be capped at +25%, got {up}%")


def test_buffered_leverages_upside(buffered_note):
    """A small +10% move with 1.5x leverage -> +15% (still under the 25% cap)."""
    res = _price(buffered_note)
    up = _payoff_at(res, 10.0)
    assert up == pytest.approx(15.0, abs=0.5), (
        f"1.5x leverage on +10% should give +15%, got {up}%")


def test_buffered_absorbs_loss_within_buffer(buffered_note):
    """A −8% move is inside the 10% buffer -> 0 investor loss."""
    res = _price(buffered_note)
    inside = _payoff_at(res, -8.0)
    assert inside == pytest.approx(0.0, abs=0.5), (
        f"−8% move within the 10% buffer should be 0 loss, got {inside}%")


def test_buffered_loss_beyond_buffer(buffered_note):
    """A −20% move (beyond the 10% buffer) -> −10% investor loss."""
    res = _price(buffered_note)
    beyond = _payoff_at(res, -20.0)
    assert beyond == pytest.approx(-10.0, abs=0.5), (
        f"−20% move with a 10% buffer should give −10%, got {beyond}%")


# === Cross-type: determinism + return_distribution in [0,1] ===============
@pytest.mark.parametrize("fixture_name", [
    "autocallable", "reverse_convertible", "ppn", "barrier_note_eu",
    "buffered_note",
])
def test_all_types_seed_deterministic(request, fixture_name):
    product = request.getfixturevalue(fixture_name)
    r1 = price_product(product, n_paths=20_000, seed=42, **OFFLINE)
    r2 = price_product(product, n_paths=20_000, seed=42, **OFFLINE)
    assert r1.fair_value == pytest.approx(r2.fair_value, abs=1e-9)
    assert r1.prob_loss == pytest.approx(r2.prob_loss, abs=1e-12)


@pytest.mark.parametrize("fixture_name", [
    "autocallable", "reverse_convertible", "ppn", "barrier_note_eu",
    "buffered_note",
])
def test_all_types_prob_loss_bounded(request, fixture_name):
    product = request.getfixturevalue(fixture_name)
    res = price_product(product, n_paths=20_000, seed=SEED, **OFFLINE)
    assert 0.0 <= res.prob_loss <= 1.0


def test_unsupported_type_raises():
    """BACKEND_NOTES §3: any non-supported product type raises TypeError."""
    class NotAProduct:
        pass
    with pytest.raises(TypeError):
        price_product(NotAProduct(), n_paths=1000, seed=SEED, **OFFLINE)
