"""PRD §15.5 checkpoint 5 / §6.2: MC European call within 1% of Black-Scholes."""
import math

import numpy as np
import pytest

from prism.pricing.black_scholes import bs_call
from prism.pricing.monte_carlo import (
    european_call_mc,
    simulate_paths,
    step_indices_for_times,
)


def test_mc_european_call_matches_bs_within_1pct(seed):
    mc = european_call_mc(100, 100, 0.05, 0.20, 1.0, n_paths=400_000, seed=seed)
    bs = bs_call(100, 100, 0.05, 0.20, 1.0)
    rel = abs(mc - bs) / bs
    assert rel < 0.01, f"MC {mc} vs BS {bs}, rel={rel}"


def test_simulate_paths_shape_and_seed_determinism(seed):
    p1 = simulate_paths(100, None, 0.05, 0.0, 1.0, n_paths=1000, n_steps=4, seed=seed, vol=0.20)
    p2 = simulate_paths(100, None, 0.05, 0.0, 1.0, n_paths=1000, n_steps=4, seed=seed, vol=0.20)
    assert p1.shape == (1000, 5)          # n_steps+1 columns, spot prepended
    assert np.allclose(p1[:, 0], 100.0)   # column 0 is the initial spot
    assert np.allclose(p1, p2)            # deterministic under fixed seed


def test_mc_risk_neutral_drift(seed):
    # E[S_T] == S0 * e^{(r-q)T} under the risk-neutral measure.
    paths = simulate_paths(100, None, 0.05, 0.0, 1.0, n_paths=400_000, n_steps=1, seed=seed, vol=0.20)
    assert paths[:, -1].mean() == pytest.approx(100 * math.exp(0.05), rel=0.01)


def test_step_indices_for_times():
    times = np.array([0.25, 0.5, 1.0])
    idx = step_indices_for_times(times, 1.0, 12)
    assert min(idx) >= 1 and max(idx) <= 12
    assert idx[-1] == 12               # final observation snaps to maturity
    assert list(idx) == [3, 6, 12]
