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


def test_rolling_windows_catch_what_calendar_weeks_hide():
    from app.domain.risk import RollingParams, rolling_breaker

    p = RollingParams(3, 6, 8, 10, 0.5)  # the logic, on the thresholds of the first version
    # −2.9 R on Friday then −2.9 R on Monday: two calendar weeks under −3 R, but −5.8 R over 5 sessions
    totals = [0, 0, 0, 0, -870.0]
    tripped, _, why = rolling_breaker(totals, -1740.0, 300, p)
    assert tripped and "5 séances" in why
    # a slow give-back: +10 R in 6 sessions, then −0.57 R a session — no window beyond its limit, −8 R from the peak
    rise = [500.0 * k for k in range(1, 7)]
    fall = [3000.0 - 170 * k for k in range(1, 15)]
    tripped, mult, why = rolling_breaker([0.0, *rise, *fall], 3000.0 - 170 * 15 + 150, 300, p)
    assert not tripped and mult == 0.5 and "plus haut" in why
    assert rolling_breaker([], 0.0, 300, p) == (False, 1.0, "")


def test_market_reductions_take_the_strongest_others_compound():
    from app.domain.risk import combine_size_factors

    assert combine_size_factors({"orange": 0.5, "portail": 0.5}, {}) == 0.5
    assert combine_size_factors({"orange": 0.5}, {"coupe-circuit": 0.5}) == 0.25
    assert combine_size_factors({}, {}) == 1.0


def test_gap_risk_reduces_the_size_only_when_a_usual_gap_exceeds_the_stop_distance():
    from app.domain.risk import gap_risk_multiplier

    closes = [10.0] * 200
    calm = [10.0] * 200  # no gap: no reduction
    assert gap_risk_multiplier(closes, calm, 10.0, 9.4)[0] == 1.0
    jumpy = [9.0 if k % 10 == 0 else 10.0 for k in range(200)]  # a −10 % gap one day in ten
    m, q = gap_risk_multiplier(closes, jumpy, 10.0, 9.6)
    assert q > 0.09 and abs(m - 0.4 / (q * 10.0)) < 1e-9 and m < 1
    assert gap_risk_multiplier(closes[:50], jumpy[:50], 10.0, 9.6) == (1.0, None)  # too short: no claim
