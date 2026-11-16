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


def test_the_contrast_inside_a_source_is_free_of_the_drift_of_its_population():
    """16/11/2026 — buys −0.6 % and sales (signed) +2.5 % both carry the drift of the companies that file: the
    contrast « up − down » on raw excess removes it."""
    from app.domain.event_stats import contrast

    drift = -0.01
    up = group_stats([drift + x for x in (0.01, 0.02, 0.0, 0.03)])  # buys: raw = drift + effect
    down = group_stats([-(drift + x) for x in (-0.02, -0.03, -0.01, -0.04)])  # sales, signed (× −1)
    c = contrast([up], [down])
    assert c.n_up == 4 and c.n_down == 4
    assert c.diff == pytest.approx(0.015 - (-0.025))  # the drift cancels
    assert c.t_stat is not None and c.t_stat > 3
    assert contrast([up], []).diff is None
    pooled = contrast([up, up], [down])
    assert pooled.n_up == 8 and pooled.diff == pytest.approx(c.diff)
