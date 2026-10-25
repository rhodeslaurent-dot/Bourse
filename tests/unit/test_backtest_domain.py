"""docs/27 — conservative trade simulation and robust statistics (pure)."""

import pytest

from app.domain.backtest import simulate_trade, summarize
from app.domain.indicators import Bar


def B(o, h, lo, c):  # noqa: N802
    return Bar(o, h, lo, c, 1000)


def test_limit_fills_only_when_traded_through_and_target_is_reached():
    bars = [B(10.5, 10.6, 10.0, 10.2), B(10.2, 10.3, 9.9, 10.1), B(10.1, 11.2, 10.0, 11.0)]
    # touch of 10.0 on day 0 is not a fill (needs low < limit); day 1 trades through → fill at 10.0
    r = simulate_trade(bars, limit=10.0, stop=9.0, target=11.0, slippage_pct=0.0, entry_sessions=3, max_hold=10)
    assert r is not None and (r.entry_index, r.entry, r.reason, r.exit) == (1, 10.0, "target", 11.0)
    assert r.gross_r == pytest.approx(1.0) and r.net_r(0.01) == pytest.approx(0.9)


def test_gap_open_below_limit_fills_at_open_and_stop_first_when_both_in_one_session():
    bars = [B(9.8, 9.9, 9.7, 9.8), B(9.8, 11.5, 8.5, 10.0)]
    r = simulate_trade(bars, 10.0, 9.0, 11.0, 0.01, 3, 10)
    assert r.entry == 9.8 and r.reason == "stop" and r.exit == pytest.approx(9.0 * 0.99) and r.exit_index == 1


def test_gap_through_stop_exits_at_open_and_invalidated_setup_never_enters():
    bars = [B(9.9, 10.0, 9.8, 9.9), B(8.0, 8.2, 7.9, 8.1)]
    r = simulate_trade(bars, 10.0, 9.0, 11.0, 0.0, 3, 10)
    assert r.reason == "gap_stop" and r.exit == 8.0 and r.mae_r < -1.5
    assert simulate_trade([B(8.5, 8.6, 8.4, 8.5)], 10.0, 9.0, 11.0, 0.0, 3, 10) is None  # opens below the stop


def test_time_stop_and_unknown_outcome_is_not_counted():
    bars = [B(9.9, 10.0, 9.8, 9.9)] + [B(10.0, 10.2, 9.9, 10.1)] * 5
    r = simulate_trade(bars, 10.0, 9.0, 12.0, 0.0, 3, 3)
    assert r.reason == "time" and r.exit_index == 2 and r.exit == 10.1
    assert simulate_trade(bars, 10.0, 9.0, 12.0, 0.0, 3, 30) is None  # history ends before the time stop


def test_summary_is_reproducible_and_flags_only_strong_evidence():
    s = summarize([1.0, -1.0, 2.0, -1.0, 0.5] * 40, [5] * 200)
    assert s.n == 200 and s.hit_rate == pytest.approx(0.6) and s.profit_factor == pytest.approx(3.5 / 2)
    assert s.ci_low < s.mean_r < s.ci_high and s.significant is (s.t_stat >= 3)
    assert summarize([1.0, -1.0, 2.0, -1.0, 0.5] * 40).as_dict() == s.as_dict() | {"avg_hold": None}
    assert summarize([]).n == 0 and summarize([]).significant is False
