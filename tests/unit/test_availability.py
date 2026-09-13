"""docs/02 §2.3: schedule + overrides, unconfirmed market_open slot ignored, /mode parsing."""

from datetime import UTC, datetime, timedelta

from app.config.schema import ScheduleSlot
from app.domain.availability import (
    ModeOverride,
    parse_duration,
    parse_mode_command,
    resolve_mode,
    slot_window_utc,
    unconfirmed_slots,
)


def _sched(config):
    return config.params.availability.schedule


def test_default_schedule_evening_slot_is_disponible(config):
    # Tuesday 2026-09-15 20:00 Réunion = 16:00 UTC
    r = resolve_mode(datetime(2026, 9, 15, 16, 0, tzinfo=UTC), _sched(config), "reunion")
    assert r.mode == "disponible" and r.source == "schedule"


def test_default_mode_outside_slots(config):
    # Tuesday 15:00 Réunion = 11:00 UTC → default (reunion)
    r = resolve_mode(datetime(2026, 9, 15, 11, 0, tzinfo=UTC), _sched(config), "reunion")
    assert r.mode == "reunion" and r.source == "schedule_default"


def test_unconfirmed_market_open_slot_is_ignored(config):
    sched = _sched(config)
    assert len(unconfirmed_slots(sched)) == 1
    # 09:20 Paris summer on a Tuesday = 07:20 UTC → inside the market_open slot if confirmed
    now = datetime(2026, 9, 15, 7, 20, tzinfo=UTC)
    assert resolve_mode(now, sched, "reunion").mode == "reunion"
    confirmed = [s.model_copy(update={"confirmed": True}) for s in sched]
    assert resolve_mode(now, confirmed, "reunion").mode == "disponible"


def test_market_open_slot_follows_dst():
    slot = ScheduleSlot(days="1-5", ref="market_open", offset_min=[0, 45], mode="disponible", confirmed=True)
    from datetime import date

    s, e = slot_window_utc(slot, date(2026, 7, 15))
    assert s == datetime(2026, 7, 15, 7, 0, tzinfo=UTC) and e == datetime(2026, 7, 15, 7, 45, tzinfo=UTC)
    s, _ = slot_window_utc(slot, date(2026, 12, 15))
    assert s == datetime(2026, 12, 15, 8, 0, tzinfo=UTC)


def test_weekend_slot(config):
    r = resolve_mode(datetime(2026, 9, 12, 6, 0, tzinfo=UTC), _sched(config), "reunion")  # Saturday 10:00 Réunion
    assert r.mode == "disponible"


def test_override_wins_until_expiry(config):
    now = datetime(2026, 9, 15, 15, 30, tzinfo=UTC)  # Tuesday 19:30 Réunion
    ov = ModeOverride("absent", now - timedelta(minutes=5), now + timedelta(hours=1), "telegram")
    assert resolve_mode(now, _sched(config), "reunion", ov).mode == "absent"
    later = now + timedelta(hours=1, minutes=30)  # 21:00 Réunion, override expired → evening slot
    assert resolve_mode(later, _sched(config), "reunion", ov).mode == "disponible"


def test_parse_mode_command():
    c = parse_mode_command("/mode reunion 2h")
    assert c.mode == "reunion" and c.duration == timedelta(hours=2)
    assert parse_mode_command("/mode absent").duration is None
    assert parse_mode_command("/mode réunion 30min").mode == "reunion"
    assert parse_mode_command("/mode vacances") is None
    assert parse_mode_command("/mode absent bientot") is None
    assert parse_duration("1j") == timedelta(days=1)
