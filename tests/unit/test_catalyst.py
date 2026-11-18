"""18/11/2026 — the market's reaction to a results publication, from stored closes only."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from app.domain.catalyst import DayBar, results_reaction, welcomed

CLOSE = time(17, 30)


def _bars() -> list[DayBar]:
    out, d = [], date(2026, 10, 19)
    while d <= date(2026, 11, 18):
        if d.weekday() < 5:
            out.append(DayBar(d, 100.0, 1000.0))
        d += timedelta(days=1)
    out[-1] = DayBar(date(2026, 11, 18), 106.0, 3000.0)  # the day after an evening publication
    out[-2] = DayBar(date(2026, 11, 17), 100.0, 1000.0)
    return out


def test_an_evening_publication_counts_for_the_next_session():
    r = results_reaction(datetime(2026, 11, 17, 17, 45), CLOSE, _bars())
    assert r is not None and r.ref_day == date(2026, 11, 17) and r.day == date(2026, 11, 18)
    assert abs(r.move - 0.06) < 1e-9 and abs(r.rel_volume - 3.0) < 1e-9
    assert welcomed(r, 0.03, 1.5) and not welcomed(r, 0.08, 1.5) and not welcomed(r, 0.03, 4.0)


def test_a_morning_publication_counts_for_its_own_session():
    r = results_reaction(datetime(2026, 11, 18, 7, 0), CLOSE, _bars())
    assert r is not None and r.ref_day == date(2026, 11, 17) and r.day == date(2026, 11, 18)


def test_no_reaction_while_the_next_session_is_not_closed():
    assert results_reaction(datetime(2026, 11, 18, 18, 0), CLOSE, _bars()) is None  # after the last stored close
    assert not welcomed(None, 0.03, 1.5)
