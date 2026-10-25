"""docs/07 §7.3 — period result in nominal R and compounding of the size reductions."""

import pytest

from app.domain.risk import CircuitBreakerParams, circuit_breaker, period_pnl_r, size_multiplier


def test_period_result_counts_a_closed_position_once():
    # start: +200 € latent on a line; during the week it is sold for +150 € realised; the latent goes to 0
    assert period_pnl_r(1150, 0, 1000, 200, 300) == pytest.approx((150 - 200) / 300)


def test_period_result_rejects_a_zero_r():
    with pytest.raises(ValueError):
        period_pnl_r(0, 0, 0, 0, 0)


def test_size_reductions_compound_and_never_increase():
    assert size_multiplier({}) == 1.0
    assert size_multiplier({"orange": 0.5, "portail": 0.5}) == 0.25
    assert size_multiplier({"erreur": 1.7}) == 1.0  # a factor above 1 is clipped: a reduction only


def test_breaker_trips_on_the_week_and_resumes_at_half_size():
    p = CircuitBreakerParams(3, 6, 0.5)
    assert circuit_breaker(-3.2, 0, -3.2, 0, p).tripped
    st = circuit_breaker(0.4, 0, -3.5, 0, p, previous_week_tripped=True)
    assert not st.tripped and st.size_multiplier == 0.5
