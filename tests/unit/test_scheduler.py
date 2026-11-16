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


def test_crontab_days_are_read_the_crontab_way():
    """16/11/2026: APScheduler reads 0 as Monday — « 1-5 » ran Tuesday to Saturday, « 0 » (Sunday) on Monday."""
    from app.scheduler.registry import crontab_days

    assert crontab_days("1-5") == "mon,tue,wed,thu,fri"
    assert crontab_days("0") == crontab_days("7") == "sun"
    assert crontab_days("6") == "sat"
    assert crontab_days("1") == "mon"
    assert crontab_days("*") == "*"
    assert crontab_days("1,3,5") == "mon,wed,fri"
    assert crontab_days("1-7") == "mon,tue,wed,thu,fri,sat,sun"
    assert crontab_days("*/2") == "tue,thu,sat,sun"  # 0, 2, 4, 6
    assert crontab_days("mon-fri") == "mon,tue,wed,thu,fri"


def test_every_scheduled_job_fires_on_the_days_its_crontab_says(config):
    from datetime import datetime, timedelta

    from app.domain.timeutil import PARIS

    jobs = {(j.name, j.variant): j for j in build_schedule(config.params)}
    monday = datetime(2026, 11, 16, 0, 0, tzinfo=PARIS)

    def days(name: str) -> set[str]:
        t, out, d = jobs[(name, "normal")].trigger, set(), monday
        while d < monday + timedelta(days=7):
            d = t.get_next_fire_time(None, d)
            if d is None or d >= monday + timedelta(days=7):
                break
            out.add(d.strftime("%a"))
            d = d + timedelta(seconds=1)
        return out

    assert days("eod_backfill_check") == {"Mon", "Tue", "Wed", "Thu", "Fri"}
    assert days("daily_regime") == {"Mon", "Tue", "Wed", "Thu", "Fri"}
    assert days("backtest_weekly") == {"Sun"}
    assert days("portfolio_review") == {"Sat"}
    assert days("universe_refresh") == {"Mon"}
    assert days("insider_archive") == {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}
    assert days("imap_poll") == {"Mon", "Tue", "Wed", "Thu", "Fri"}  # window job: ISO days, already right
