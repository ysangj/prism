"""PRD §15.5 checkpoint 1 + §6.2: Black-Scholes, put-call parity, digital, bond floor."""
import math

import pytest

from prism.bond_floor import price_bond_floor
from prism.pricing.black_scholes import bs_call, bs_put, bs_digital


def test_bs_call_textbook_value():
    # PRD §15.5.1: bs_call(100,100,0.05,0.20,1.0) ~= 10.45
    assert bs_call(100, 100, 0.05, 0.20, 1.0) == pytest.approx(10.4506, abs=1e-3)


def test_bs_call_put_high_precision():
    assert bs_call(100, 100, 0.05, 0.20, 1.0) == pytest.approx(10.450584, abs=1e-4)
    assert bs_put(100, 100, 0.05, 0.20, 1.0) == pytest.approx(5.573526, abs=1e-4)


def test_put_call_parity():
    # PRD §6.2: C - P == S e^{-qT} - K e^{-rT}
    S, K, r, sigma, T, q = 100, 95, 0.03, 0.25, 2.0, 0.01
    lhs = bs_call(S, K, r, sigma, T, div_yield=q) - bs_put(S, K, r, sigma, T, div_yield=q)
    rhs = S * math.exp(-q * T) - K * math.exp(-r * T)
    assert lhs == pytest.approx(rhs, abs=1e-9)


def test_bs_digital_bounds_and_monotonicity():
    # Cash-or-nothing digital call: 0 < value < payout*e^{-rT}, rises with spot.
    S, K, r, sigma, T = 100, 100, 0.05, 0.20, 1.0
    cap = math.exp(-r * T)
    v = bs_digital(S, K, r, sigma, T, payoff=1.0)
    assert 0.0 < v < cap
    deep_itm = bs_digital(300, K, r, sigma, T, payoff=1.0)
    deep_otm = bs_digital(10, K, r, sigma, T, payoff=1.0)
    assert deep_otm < v < deep_itm
    assert deep_itm == pytest.approx(cap, abs=2e-3)


def test_bs_digital_put_complement():
    S, K, r, sigma, T = 100, 100, 0.05, 0.20, 1.0
    call = bs_digital(S, K, r, sigma, T, option_type="call")
    put = bs_digital(S, K, r, sigma, T, option_type="put")
    # digital call + digital put == discounted unit payoff
    assert call + put == pytest.approx(math.exp(-r * T), abs=1e-9)


def test_bond_floor_closed_form():
    # PRD §15.5.3: price_bond_floor(100000, 1.0, 0.05, 0.01) ~= 94,176
    val = price_bond_floor(100_000, 1.0, 0.05, 0.01)
    assert val == pytest.approx(94176.45, abs=1.0)
    assert val == pytest.approx(100_000 * math.exp(-0.06), abs=1e-6)
