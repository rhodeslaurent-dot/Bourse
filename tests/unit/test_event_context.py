"""17/11/2026 — the context of past events completed point in time: VIX and the index's trend, read before the day."""

from __future__ import annotations

from datetime import date

from app.domain.event_stats import trend_flags
from app.services.event_context import ContextSeries, Series, completed


def test_trend_flags_need_a_full_window_and_compare_to_the_mean():
    assert trend_flags([1.0, 2.0, 3.0], window=3) == [None, None, True]
    assert trend_flags([3.0, 2.0, 1.0, 5.0], window=3) == [None, None, False, True]


def test_the_context_is_completed_with_what_was_known_before_the_day_and_never_overwritten():
    days = [date(2026, 3, 2), date(2026, 3, 3), date(2026, 3, 4)]
    series = ContextSeries(
        Series(days, [18.0, 25.0, 40.0]),
        {"XPAR": Series(days, [None, True, False]), "EU": Series(days, [False, False, False])},
    )
    # an event of 04/03: the VIX and the trend of 03/03, never those of the day itself
    out = completed({"regime": None, "vix": None}, "XPAR", date(2026, 3, 4), series)
    assert out == {"regime": None, "vix": 25.0, "indice_mm50": "au-dessus", "indice_de": "XPAR"}
    # a venue without index follows Europe; a stored VIX is kept
    out = completed({"vix": 12.0}, "XWAR", date(2026, 3, 4), series)
    assert out == {"vix": 12.0, "indice_mm50": "en dessous", "indice_de": "EU"}
    # nothing to add: None (the row is left as it is)
    assert completed({"vix": 12.0, "indice_mm50": "au-dessus"}, "XPAR", date(2026, 3, 4), series) is None
    # a VIX older than 7 days is not a context
    assert completed({}, "XPAR", date(2026, 3, 20), series).get("vix") is None
