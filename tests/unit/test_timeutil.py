"""A0.3: Paris ↔ Réunion (summer and winter), naive datetimes refused."""

from datetime import UTC, date, datetime

import pytest
from apscheduler.triggers.cron import CronTrigger

from app.domain.timeutil import (
    PARIS,
    REUNION,
    ensure_utc,
    format_dual,
    is_paris_summer_time,
    market_open_reunion,
    paris_to_reunion_time,
)


def test_market_open_summer_and_winter():
    assert market_open_reunion(date(2026, 7, 15)).strftime("%H:%M") == "11:00"
    assert market_open_reunion(date(2026, 12, 15)).strftime("%H:%M") == "12:00"
    assert is_paris_summer_time(date(2026, 7, 15)) and not is_paris_summer_time(date(2026, 12, 15))


def test_dst_transition_days_2026():
    # DST starts 29/03/2026 and ends 25/10/2026 in Europe/Paris.
    assert paris_to_reunion_time(date(2026, 3, 28), "09:00").hour == 12
    assert paris_to_reunion_time(date(2026, 3, 30), "09:00").hour == 11
    assert paris_to_reunion_time(date(2026, 10, 24), "09:00").hour == 11
    assert paris_to_reunion_time(date(2026, 10, 26), "09:00").hour == 12


def test_cron_0905_paris_fires_at_expected_reunion_time():
    trig = CronTrigger.from_crontab("5 9 * * 1-5", timezone=PARIS)
    summer = trig.get_next_fire_time(None, datetime(2026, 7, 14, 12, 0, tzinfo=UTC))
    winter = trig.get_next_fire_time(None, datetime(2026, 12, 14, 12, 0, tzinfo=UTC))
    assert summer.astimezone(REUNION).strftime("%Y-%m-%d %H:%M") == "2026-07-15 11:05"
    assert winter.astimezone(REUNION).strftime("%Y-%m-%d %H:%M") == "2026-12-15 12:05"


def test_naive_datetime_refused():
    with pytest.raises(ValueError):
        ensure_utc(datetime(2026, 1, 1, 9, 0))


def test_format_dual():
    dt = datetime(2026, 7, 15, 7, 5, tzinfo=UTC)
    assert format_dual(dt) == "11:05 Réunion (09:05 Paris)"
