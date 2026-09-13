"""A0.3: per-venue calendars (24/12: Xetra closed, Paris half-day), SRD calendar."""

from datetime import UTC, date, datetime

import pytest

from app.domain.calendar import DayStatus, SrdCalendar


def test_christmas_eve_xetra_closed_paris_half_day(calendars):
    assert calendars.get("XETR").session(date(2026, 12, 24)).status == DayStatus.CLOSED
    paris = calendars.get("XPAR").session(date(2026, 12, 24))
    assert paris.status == DayStatus.HALF_DAY
    assert paris.close_time.strftime("%H:%M") == "14:05"
    assert calendars.get("XETR").session(date(2026, 12, 31)).status == DayStatus.CLOSED
    assert calendars.get("XPAR").is_half_day(date(2026, 12, 31))


def test_euronext_holidays_2026(calendars):
    xpar = calendars.get("XPAR")
    for d in [date(2026, 1, 1), date(2026, 4, 3), date(2026, 4, 6), date(2026, 5, 1), date(2026, 12, 25)]:
        assert not xpar.is_open(d), d
    assert xpar.is_open(date(2026, 9, 14))  # Monday
    assert not xpar.is_open(date(2026, 9, 12))  # Saturday


def test_xetra_may_1_2027_saturday_and_dec_24_closed(calendars):
    xetr = calendars.get("XETR")
    assert not xetr.is_open(date(2027, 5, 1))
    assert not xetr.is_open(date(2027, 12, 24))
    assert calendars.get("XPAR").is_half_day(date(2027, 12, 24))


def test_unknown_year_refused(calendars):
    with pytest.raises(ValueError):
        calendars.get("XPAR").session(date(2031, 1, 5))


def test_is_open_at_uses_venue_timezone(calendars):
    xpar = calendars.get("XPAR")
    assert xpar.is_open_at(datetime(2026, 7, 15, 7, 30, tzinfo=UTC))  # 09:30 Paris summer
    assert not xpar.is_open_at(datetime(2026, 7, 15, 6, 30, tzinfo=UTC))  # 08:30 Paris
    assert not xpar.is_open_at(datetime(2026, 12, 24, 13, 30, tzinfo=UTC))  # 14:30 Paris on half-day


def test_previous_next_session_over_easter(calendars):
    xpar = calendars.get("XPAR")
    assert xpar.previous_session(date(2026, 4, 7)) == date(2026, 4, 2)
    assert xpar.next_session(date(2026, 4, 2)) == date(2026, 4, 7)


def test_any_p0_open_and_open_mics(calendars):
    assert calendars.any_p0_open(date(2026, 12, 24))
    assert calendars.open_mics(date(2026, 12, 24), ["XPAR", "XETR"]) == ["XPAR"]
    assert calendars.open_mics(date(2026, 12, 25), ["XPAR", "XETR"]) == []


def test_srd_calendar_from_params(config, calendars):
    srd = SrdCalendar.from_params(config.params.srd.calendar_2026, config.params.srd.calendar_2027_provisional)
    nxt = srd.next_liquidation(date(2026, 9, 14))
    assert nxt.liquidation_date == date(2026, 9, 25) and nxt.settlement_date == date(2026, 9, 30) and nxt.confirmed
    assert srd.sessions_until_liquidation(date(2026, 9, 22), calendars.get("XPAR")) == 3
    assert not srd.next_liquidation(date(2027, 1, 2)).confirmed


def test_eodhd_code_to_mic(calendars):
    assert calendars.mic_for_eodhd("XETRA") == "XETR" and calendars.mic_for_eodhd("PA") == "XPAR"
