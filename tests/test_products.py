"""PRD §15.5.7 + §15.7: end-to-end product pricing and canonical acceptance.

All tests inject explicit market overrides, so they are deterministic and
network-free (no live endpoints are hit).
"""
import pytest

from prism import decompose, price_product
from prism.models import DecompositionResult


def _assert_fully_populated(res: DecompositionResult):
    for fld in (
        "bond_floor", "option_value", "fair_value", "offer_price_dollars",
        "embedded_margin", "margin_pct", "greeks", "prob_loss",
        "return_distribution", "payoff_curve",
    ):
        assert getattr(res, fld) is not None, f"field {fld} is None"
    assert {"delta", "vega", "rho"} <= set(res.greeks)
    assert len(res.payoff_curve) > 0
    assert len(res.return_distribution) > 0
    assert 0.0 < res.prob_loss < 1.0
    # Internal identities (PRD §6.3).
    assert res.fair_value == pytest.approx(res.bond_floor + res.option_value, abs=1e-6)
    assert res.embedded_margin == pytest.approx(
        res.offer_price_dollars - res.fair_value, abs=1e-6
    )


def test_price_autocallable_fully_populated(canonical_autocallable, offline_market, seed):
    res = price_product(canonical_autocallable, n_paths=50_000, seed=seed, **offline_market)
    assert isinstance(res, DecompositionResult)
    _assert_fully_populated(res)


def test_price_reverse_convertible_fully_populated(sample_reverse_convertible, seed):
    res = price_product(
        sample_reverse_convertible, n_paths=50_000, seed=seed,
        spot=100.0, risk_free=0.05, div_yield=0.0, credit_spread=0.015, flat_vol=0.20,
    )
    assert isinstance(res, DecompositionResult)
    _assert_fully_populated(res)


def test_decompose_is_alias(canonical_autocallable, offline_market, seed):
    res = decompose(canonical_autocallable, n_paths=20_000, seed=seed, **offline_market)
    assert isinstance(res, DecompositionResult)


def test_seed_determinism(canonical_autocallable, offline_market):
    r1 = price_product(canonical_autocallable, n_paths=30_000, seed=999, **offline_market)
    r2 = price_product(canonical_autocallable, n_paths=30_000, seed=999, **offline_market)
    assert r1.fair_value == pytest.approx(r2.fair_value, abs=1e-9)
    assert r1.prob_loss == pytest.approx(r2.prob_loss, abs=1e-12)


# ---------------------------------------------------------------------------
# PRD §15.7(B) — STRUCTURAL / end-to-end check on the 18-month note.
# Per the amended §15.7, the 18-month note is exercised ONLY for structural and
# internal-consistency checks; the value bands (bond/option/margin) are NOT
# asserted on it (a ~92% bond floor and a possibly-negative margin are correct
# at this short tenor — see §15.7 and BACKEND_NOTES.md §7).
# ---------------------------------------------------------------------------

def test_canonical_18m_structural_fully_populated(canonical_autocallable, offline_market, seed):
    """§15.7(B): DecompositionResult fully populated and internally consistent."""
    res = price_product(canonical_autocallable, n_paths=50_000, seed=seed, **offline_market)
    _assert_fully_populated(res)  # covers identities, greeks, payoff_curve, prob_loss


def test_canonical_18m_prob_loss_in_open_interval(canonical_autocallable, offline_market, seed):
    """§15.7(B): prob_loss in (0, 1)."""
    res = price_product(canonical_autocallable, n_paths=50_000, seed=seed, **offline_market)
    assert 0.0 < res.prob_loss < 1.0


def test_canonical_18m_decomposition_identity(canonical_autocallable, offline_market, seed):
    """§15.7(B): bond_floor + option_value == fair_value; margin == offer - fair."""
    res = price_product(canonical_autocallable, n_paths=50_000, seed=seed, **offline_market)
    assert res.fair_value == pytest.approx(res.bond_floor + res.option_value, abs=1e-6)
    assert res.embedded_margin == pytest.approx(
        res.offer_price_dollars - res.fair_value, abs=1e-6
    )
    # margin SIGN is informative only on the 18m note — explicitly NOT asserted.


# ---------------------------------------------------------------------------
# PRD §15.7(A) — VALUE-BAND acceptance on the 5-year note (primary).
# These are the bands the amended PRD requires to hold. Inputs are the
# documented deterministic 5y fixture (live-data run is DEFERRED off-network).
# ---------------------------------------------------------------------------

def test_canonical_5y_fair_value_below_offer(canonical_autocallable_5y, offline_market_5y, seed):
    """§15.7(A): fair_value < offer_price (embedded margin must be positive)."""
    res = price_product(canonical_autocallable_5y, n_paths=50_000, seed=seed, **offline_market_5y)
    assert res.fair_value < res.offer_price_dollars, (
        f"fair_value {res.fair_value:.1f} must be < offer {res.offer_price_dollars:.1f} "
        f"(margin_pct={res.margin_pct:.2f}%)"
    )


def test_canonical_5y_margin_band(canonical_autocallable_5y, offline_market_5y, seed):
    """§15.7(A): embedded margin_pct in the 2-8% band (academic literature)."""
    res = price_product(canonical_autocallable_5y, n_paths=50_000, seed=seed, **offline_market_5y)
    assert 2.0 <= res.margin_pct <= 8.0, f"margin_pct={res.margin_pct:.2f}% out of 2-8% band"


def test_canonical_5y_bond_floor_band(canonical_autocallable_5y, offline_market_5y, seed):
    """§15.7(A): bond_floor ~ 60-70% of notional."""
    res = price_product(canonical_autocallable_5y, n_paths=50_000, seed=seed, **offline_market_5y)
    pct = res.bond_floor / canonical_autocallable_5y.notional
    assert 0.60 <= pct <= 0.70, f"bond_floor {pct:.3f} of notional out of 60-70% band"


def test_canonical_5y_option_value_band(canonical_autocallable_5y, offline_market_5y, seed):
    """§15.7(A): option_value ~ 30-35% of notional."""
    res = price_product(canonical_autocallable_5y, n_paths=50_000, seed=seed, **offline_market_5y)
    pct = res.option_value / canonical_autocallable_5y.notional
    assert 0.30 <= pct <= 0.35, f"option_value {pct:.3f} of notional out of 30-35% band"


def test_canonical_5y_prob_loss_in_open_interval(canonical_autocallable_5y, offline_market_5y, seed):
    """§15.7(A): 0 < prob_loss < 1."""
    res = price_product(canonical_autocallable_5y, n_paths=50_000, seed=seed, **offline_market_5y)
    assert 0.0 < res.prob_loss < 1.0


def test_canonical_5y_greeks_and_payoff_populated(canonical_autocallable_5y, offline_market_5y, seed):
    """§15.7(A): Greeks populated, payoff_curve non-empty."""
    res = price_product(canonical_autocallable_5y, n_paths=50_000, seed=seed, **offline_market_5y)
    assert {"delta", "vega", "rho"} <= set(res.greeks)
    assert all(res.greeks[g] is not None for g in ("delta", "vega", "rho"))
    assert len(res.payoff_curve) > 0
