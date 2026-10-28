"""docs/33 §33.3 — prospective shadow tracking of registration n° 2 on the European venues: the frozen cells
evaluated each morning on the last official session, stored once, never the placebo."""

from __future__ import annotations

from datetime import date

from app.db.models import PriceEod, ShadowSignal
from app.db.session import db_session
from app.services.shadow import SHADOW_CELLS, record_shadow_signals
from tests.integration.test_research import research_db  # noqa: F401 — the synthetic European base


def test_shadow_signals_are_recorded_once_per_session(research_db, config, calendars, monkeypatch):  # noqa: F811
    # the synthetic base ends in 2018, a year the venue calendars do not cover: its month end is given here
    monkeypatch.setattr("app.services.shadow._is_month_end", lambda *a: True)
    with db_session() as s:
        last = s.query(PriceEod.day).order_by(PriceEod.day.desc()).first()[0]
        first = record_shadow_signals(s, config, calendars, last)
        assert last == date(2018, 11, 30) and first["C2"] > 0  # Friday 30/11: the last session of the month
        rows = s.query(ShadowSignal).all()
        assert set(first) == set(SHADOW_CELLS) and sum(first.values()) == len(rows)
        assert all(r.day == last and r.plan_id == "2026-10-28_reg2" and r.venue_group in ("P0", "P1") for r in rows)
        assert not any(r.cell.startswith("X1") for r in rows)  # the placebo is never tracked
        again = record_shadow_signals(s, config, calendars, last)
        assert sum(again.values()) == 0 and s.query(ShadowSignal).count() == len(rows)  # a session once
        late = record_shadow_signals(s, config, calendars, date(last.year + 1, 1, 15))
        assert sum(late.values()) == 0  # last session too old: nothing, never guessed


def test_month_end_comes_from_the_venue_calendar(config, calendars):
    from app.services.shadow import _is_month_end

    assert _is_month_end(config, calendars, "P0", date(2026, 10, 30))  # Friday: the last Paris session of October
    assert not _is_month_end(config, calendars, "P0", date(2026, 10, 29))
    assert not _is_month_end(config, calendars, "P0", date(2018, 11, 30))  # calendar year unknown: never guessed


def test_shadow_tracks_registration_3_confirmatory_cells(research_db, config, calendars):  # noqa: F811
    """docs/34 §34.5: the same tracking for registration n° 3's C1 / C2 (plan path as a parameter)."""
    from app.services.shadow import PLAN_REG3, shadow_cells

    assert shadow_cells(PLAN_REG3) == ("C1", "C2")
    with db_session() as s:
        last = s.query(PriceEod.day).order_by(PriceEod.day.desc()).first()[0]
        out = record_shadow_signals(s, config, calendars, last, plan=PLAN_REG3)
        assert set(out) == {"C1", "C2"}
        rows = s.query(ShadowSignal).all()
        assert all(r.plan_id == "2026-10-28_reg3" for r in rows) and sum(out.values()) == len(rows)
