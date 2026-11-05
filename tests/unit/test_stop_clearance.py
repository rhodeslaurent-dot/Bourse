"""User decision of 05/11/2026 (analysis of the cards: Inwido, « l'erreur Airbus ») — a stop in the test path of a
support (inside it, or above it by less than the margin) goes under that support, repeated down a cluster."""

from __future__ import annotations

import pytest

from app.domain.stops import Zone, clear_of_supports

MM50, PIVOTS = Zone(184.5, 184.5), Zone(182.2, 184.1)


def test_a_stop_just_above_the_mm50_goes_under_the_whole_support_cluster():
    stop, span = clear_of_supports(184.65, 195.0, [MM50, PIVOTS], 0.005, 0.5 * 4.3)
    assert stop == pytest.approx(182.2 * 0.995) and span == Zone(182.2, 184.5)


def test_a_stop_far_from_any_support_or_a_support_above_the_price_is_kept():
    assert clear_of_supports(170.0, 195.0, [MM50, PIVOTS], 0.005, 2.15) == (170.0, None)
    assert clear_of_supports(184.65, 180.0, [MM50, PIVOTS], 0.005, 2.15) == (184.65, None)  # not under the price
    assert clear_of_supports(184.65, 195.0, [], 0.005, 2.15) == (184.65, None)


def test_without_margin_only_a_stop_inside_a_zone_moves():
    assert clear_of_supports(184.65, 195.0, [MM50, PIVOTS], 0.005, 0.0) == (184.65, None)
    stop, span = clear_of_supports(183.0, 195.0, [PIVOTS], 0.005, 0.0)
    assert stop == pytest.approx(182.2 * 0.995) and span == PIVOTS
