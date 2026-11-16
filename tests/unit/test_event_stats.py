"""Lot S3 — the pure statistics of what followed a family of events."""

from __future__ import annotations

import pytest

from app.domain.event_stats import broken, group_stats, signed, total_return, venue_median


def test_signed_reads_a_sell_the_other_way_round():
    assert signed(-0.02, -1) == pytest.approx(0.02)
    assert signed(-0.02, 1) == pytest.approx(-0.02)
    assert signed(0.03, None) == pytest.approx(0.03)
    assert signed(0.03, 0) == pytest.approx(0.03)


def test_group_stats_counts_mean_hit_rate_and_t():
    g = group_stats([0.01, 0.02, -0.01, 0.02])
    assert g.n == 4 and g.mean == pytest.approx(0.01) and g.hit_rate == pytest.approx(0.75)
    assert g.t_stat is not None and g.t_stat > 0
    assert group_stats([]).mean is None and group_stats([]).n == 0
    one = group_stats([0.05])
    assert one.n == 1 and one.t_stat is None  # one case says nothing about dispersion


def test_venue_median_needs_enough_values():
    assert venue_median([0.01] * 9) is None
    assert venue_median([0.0] * 5 + [0.1] * 6) == pytest.approx(0.1)
    assert venue_median([0.01, 0.03], min_n=2) == pytest.approx(0.02)


def test_a_window_crossing_a_split_or_a_bad_tick_is_broken():
    assert not broken([100, 101, 102], [1.0, 1.0, 1.0])
    assert broken([100, 50, 51], [0.5, 1.0, 1.0])  # 2-for-1 split: the adjustment factor doubles
    assert broken([100, 1, 1], [None, None, None])  # a ×100 unit change the factor does not show
    assert broken([100, 0, 1], [None, None, None])
    assert not broken([100, 102], [None, None])


def test_a_dividend_inside_the_window_is_not_read_as_a_loss():
    # 100 → 97 on the ex-dividend date of a 3 dividend: the stored factor adj/close goes from 0.97 to 1
    assert total_return(100.0, 0.97, 97.0, 1.0) == pytest.approx(0.0)
    assert total_return(100.0, 0.97, 99.0, 1.0) == pytest.approx(99 / 97 - 1)
    # no factor at one end: the raw return, never a guess
    assert total_return(100.0, None, 97.0, 1.0) == pytest.approx(-0.03)
    assert total_return(100.0, 0.97, 97.0, None) == pytest.approx(-0.03)
