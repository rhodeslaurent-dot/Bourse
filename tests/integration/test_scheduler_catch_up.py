"""16/11/2026 — runs due while the application was down are caught up at start, in the order they were due.

Three restarts on the morning of 16/11/2026 lost the closes (06:45), the regime (07:30) and the radar (06:40):
APScheduler computes the next run from the start, whatever the misfire grace says."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.db.models import JobRun
from app.db.session import db_session
from app.domain.timeutil import PARIS
from app.scheduler.registry import build_schedule
from app.scheduler.service import last_fire, missed_runs, schedule_catch_up


def _paris(h: int, m: int, day: int = 16) -> datetime:
    return datetime(2026, 11, day, h, m, tzinfo=PARIS)


def test_the_morning_chain_missed_by_a_restart_is_due_in_its_order(config):
    jobs = build_schedule(config.params)
    now = _paris(10, 30)  # a restart at 10:30, Monday
    due = missed_runs(jobs, now, {})
    names = [n for _, n, _ in due]
    assert names.index("eod_backfill_check") < names.index("opportunity_scan")
    assert "daily_regime" not in names  # 07:30 + 60 min of grace: too late at 10:30, it waits for tomorrow
    assert "news_scan" not in names and "imap_poll" not in names  # minutes apart: the next run comes soon
    eod = next(d for d, n, _ in due if n == "eod_backfill_check")
    assert eod == _paris(6, 45)
    # restart at 07:35: the regime is still inside its grace, after the closes; the slow SEC read (06:20) waits
    early = [n for _, n, _ in missed_runs(jobs, _paris(7, 35), {})]
    assert early.index("eod_backfill_check") < early.index("daily_regime") < early.index("us_filings")


def test_a_job_started_since_its_due_time_is_not_run_again(config):
    jobs = build_schedule(config.params)
    now = _paris(10, 30)
    ran = {"eod_backfill_check": _paris(6, 45) + timedelta(seconds=2)}
    assert "eod_backfill_check" not in [n for _, n, _ in missed_runs(jobs, now, ran)]
    before = {"eod_backfill_check": _paris(6, 45, day=13)}  # last run on Friday
    assert "eod_backfill_check" in [n for _, n, _ in missed_runs(jobs, now, before)]


def test_last_fire_inside_a_window():
    from app.scheduler.registry import _cron

    t = _cron("40 7,18 * * 1-5", PARIS)
    assert last_fire(t, _paris(6, 0), _paris(19, 0)) == _paris(18, 40)
    assert last_fire(t, _paris(8, 0), _paris(18, 0)) is None


def test_the_catch_up_is_queued_as_one_job_with_the_due_dates(config, db_engine):
    with db_session() as s:
        s.add(JobRun(job="sources_daily", started_at=_paris(22, 40, day=15).astimezone(UTC), status="ok"))

    class Sched:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def add_job(self, fn, **kw) -> None:
            self.calls.append({"fn": fn, **kw})

    sched = Sched()
    names = schedule_catch_up(sched, config, now=_paris(7, 0).astimezone(UTC))  # type: ignore[arg-type]
    assert "eod_backfill_check" in names and "sources_daily" not in names  # 22:40 yesterday: done
    (call,) = sched.calls
    assert call["id"] == "catch_up_at_start" and call["trigger"] == "date"
    runs = call["args"][0]
    eod = next(r for r in runs if r[0] == "eod_backfill_check")
    assert eod[2] == _paris(6, 45).astimezone(UTC).isoformat() and eod[3] == "2026-11-16"
    assert not [r for r in runs if r[0] == "consensus_daily"]  # Friday 22:20 + 10 h of grace: out on Monday 07:00


def test_a_failing_catch_up_never_stops_the_start(config, monkeypatch):
    import app.scheduler.service as svc

    def boom(names):
        raise RuntimeError("base indisponible")

    monkeypatch.setattr(svc, "_last_started", boom)
    assert schedule_catch_up(object(), config) == []  # type: ignore[arg-type]


def test_a_run_cut_by_the_restart_is_closed_and_caught_up(config, db_engine):
    """16/11/2026 — a rebuild at 19:40 cut the short positions job: its « running » row must not count as a run."""
    from sqlalchemy import select

    from app.scheduler.service import INTERRUPTED

    with db_session() as s:
        s.add(JobRun(job="short_positions_refresh", started_at=_paris(19, 40).astimezone(UTC), status="running"))
        s.add(JobRun(job="letters_review", started_at=_paris(19, 15).astimezone(UTC), status="ok"))

    class Sched:
        calls: list[dict] = []

        def add_job(self, fn, **kw) -> None:
            self.calls.append(kw)

    names = schedule_catch_up(Sched(), config, now=_paris(19, 42).astimezone(UTC))  # type: ignore[arg-type]
    assert "short_positions_refresh" in names and "letters_review" not in names
    with db_session() as s:
        row = s.scalar(select(JobRun).where(JobRun.job == "short_positions_refresh"))
        assert row.status == "error" and row.error == INTERRUPTED and row.ended_at is not None
