"""PRD §15.5 checkpoint 6 / §6.4: Greeks via bump-and-reprice.

The PRD acceptance criterion is phrased around a call ("delta of a call is
between 0 and 1 and increases as spot rises"). We assert that against the
``compute_greeks`` engine driven by a Black-Scholes repricing closure, then
confirm the end-to-end product greeks dict is complete and non-degenerate.
"""
from prism.pricing.black_scholes import bs_call
from prism.risk import compute_greeks


def _bs_reprice(strike=100, vol=0.20, T=1.0):
    def reprice(inp):
        return bs_call(inp["spot"], strike, inp["risk_free"], vol + inp.get("vol_shift", 0.0), T)

    return reprice


def test_call_delta_in_unit_interval():
    # compute_greeks reports delta per +1% spot move; normalise by 1% of spot
    # to recover dV/dS, which must lie in (0,1) for a call.
    spot = 100.0
    g = compute_greeks({"spot": spot, "risk_free": 0.05, "vol_shift": 0.0}, _bs_reprice())
    dv_ds = g["delta"] / (spot * 0.01)
    assert 0.0 < dv_ds < 1.0


def test_call_delta_increases_with_spot():
    reprice = _bs_reprice()
    norm_deltas = []
    for s in (70, 85, 100, 115, 130):
        g = compute_greeks({"spot": float(s), "risk_free": 0.05, "vol_shift": 0.0}, reprice)
        norm_deltas.append(g["delta"] / (s * 0.01))
    for d in norm_deltas:
        assert 0.0 < d < 1.0
    assert all(b > a for a, b in zip(norm_deltas, norm_deltas[1:]))


def test_compute_greeks_returns_expected_keys():
    g = compute_greeks({"spot": 100.0, "risk_free": 0.05, "vol_shift": 0.0}, _bs_reprice())
    assert set(g.keys()) == {"delta", "vega", "rho"}


def test_product_greeks_complete_and_non_degenerate(canonical_autocallable, offline_market, seed):
    """PRD §6.4: product greeks (delta/vega/rho) must be present and meaningful —
    the note value depends on the underlier, so they cannot all be zero."""
    from prism import price_product

    res = price_product(canonical_autocallable, n_paths=50_000, seed=seed, **offline_market)
    assert {"delta", "vega", "rho"} <= set(res.greeks)
    nonzero = {k: v for k, v in res.greeks.items() if abs(v) > 1e-9}
    assert nonzero, f"all greeks zero: {res.greeks}"
    assert abs(res.greeks["delta"]) > 1e-6
