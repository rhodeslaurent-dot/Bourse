"""A0.3: market_days_only gating per venue; half-day variants; schedule built from params."""

from datetime import date

from app.config.schema import JobSpec
from app.scheduler.gating import should_run
from app.scheduler.registry import build_schedule


def test_xetra_job_skips_24_12_while_paris_runs(calendars):
    spec = JobSpec(cron="50 17 * * 1-5", market_days_only=True)
    assert not should_run("eod_pipeline", spec, date(2026, 12, 24), calendars, mics=["XETR"]).run
    d = should_run("eod_pipeline", spec, date(2026, 12, 24), calendars, mics=["XPAR"])
    assert d.run and d.open_mics == ("XPAR",)


def test_instrument_job_on_mixed_venues_runs_only_open_ones(calendars):
    spec = JobSpec(cron="45 6 * * 1-5", market_days_only=True)
    d = should_run("eod_backfill_check", spec, date(2026, 12, 24), calendars, mics=["XPAR", "XETR"])
    assert d.run and d.open_mics == ("XPAR",) and "XETR" in d.reason


def test_job_without_instrument_runs_if_any_p0_open(calendars):
    spec = JobSpec(cron="45 8 * * 1-5", market_days_only=True)
    assert should_run("brief_premarket", spec, date(2026, 12, 24), calendars).run
    assert not should_run("brief_premarket", spec, date(2026, 12, 25), calendars).run
    assert not should_run("brief_premarket", spec, date(2026, 9, 13), calendars).run  # Sunday


def test_system_job_runs_every_day(calendars):
    spec = JobSpec(cron="0 23 * * *", tz="Indian/Reunion", market_days_only=False)
    assert should_run("backup", spec, date(2026, 12, 25), calendars).run


def test_disabled_job_never_runs(calendars):
    spec = JobSpec(cron="5 9 * * 1-5", enabled=False)
    assert not should_run("open_scan", spec, date(2026, 9, 14), calendars).run


def test_half_day_variants(calendars):
    spec = JobSpec(cron="50 17 * * 1-5", half_day="14:20", market_days_only=True)
    assert not should_run("eod_pipeline", spec, date(2026, 12, 24), calendars, variant="normal").run
    assert should_run("eod_pipeline", spec, date(2026, 12, 24), calendars, variant="half_day").run
    assert should_run("eod_pipeline", spec, date(2026, 12, 23), calendars, variant="normal").run
    assert not should_run("eod_pipeline", spec, date(2026, 12, 23), calendars, variant="half_day").run


def test_build_schedule_only_implemented_jobs(config):
    jobs = build_schedule(config.params)
    names = {(j.name, j.variant) for j in jobs}
    assert ("backup", "normal") in names
    assert not any(n == "open_scan" for n, _ in names)
    all_jobs = build_schedule(config.params, only_implemented=False)
    assert ("eod_pipeline", "half_day") in {(j.name, j.variant) for j in all_jobs}
    eod = next(j for j in all_jobs if j.name == "eod_pipeline" and j.variant == "normal")
    assert eod.misfire_grace_seconds == 240 * 60
