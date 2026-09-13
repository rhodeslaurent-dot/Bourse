"""Small repository helpers used by the scheduler, the bot and the /sante page."""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.db.models import (
    AvailabilityRow,
    DataFreshness,
    JobRun,
    MarketCalendarRow,
    ParamsVersion,
    SrdCalendarRow,
    TelegramEvent,
)
from app.domain.availability import ModeOverride
from app.domain.calendar import MarketCalendars, SrdCalendar


def record_params_version(s: Session, version: str, sha256: str, yaml_text: str, effective_from: date | None) -> None:
    if s.scalar(select(ParamsVersion).where(ParamsVersion.sha256 == sha256)) is None:
        s.add(ParamsVersion(version=version, sha256=sha256, yaml_text=yaml_text, effective_from=effective_from))


def seed_market_calendar(s: Session, cals: MarketCalendars, years: list[int]) -> int:
    n = 0
    for mic, venue in cals.venues.items():
        for year in years:
            if year not in venue.known_years:
                continue
            d = date(year, 1, 1)
            while d.year == year:
                sess = venue.session(d)
                existing = s.scalar(
                    select(MarketCalendarRow).where(MarketCalendarRow.mic == mic, MarketCalendarRow.day == d)
                )
                if existing is None:
                    s.add(
                        MarketCalendarRow(
                            mic=mic,
                            day=d,
                            status=sess.status.value,
                            open_time=sess.open_time,
                            close_time=sess.close_time,
                            source=venue.source,
                        )
                    )
                    n += 1
                d = date.fromordinal(d.toordinal() + 1)
    return n


def seed_srd_calendar(s: Session, cal: SrdCalendar, source: str) -> int:
    n = 0
    for c in cal.cycles:
        if s.scalar(select(SrdCalendarRow).where(SrdCalendarRow.liquidation_date == c.liquidation_date)) is None:
            s.add(
                SrdCalendarRow(
                    liquidation_date=c.liquidation_date,
                    settlement_date=c.settlement_date,
                    confirmed=c.confirmed,
                    source=source,
                )
            )
            n += 1
    return n


def current_override(s: Session, now: datetime | None = None) -> ModeOverride | None:
    row = s.scalar(select(AvailabilityRow).order_by(desc(AvailabilityRow.id)).limit(1))
    if row is None:
        return None
    now = now or datetime.now(UTC)
    until = row.until.replace(tzinfo=UTC) if row.until and row.until.tzinfo is None else row.until
    since = row.ts.replace(tzinfo=UTC) if row.ts and row.ts.tzinfo is None else row.ts
    if until is not None and until <= now:
        return None
    return ModeOverride(mode=row.mode, since=since, until=until, source=row.source)  # type: ignore[arg-type]


def set_override(s: Session, mode: str, until: datetime | None, source: str) -> AvailabilityRow:
    row = AvailabilityRow(mode=mode, until=until, source=source, ts=datetime.now(UTC))
    s.add(row)
    s.flush()
    return row


def last_runs(s: Session, limit_per_job: int = 1) -> dict[str, JobRun]:
    out: dict[str, JobRun] = {}
    for run in s.scalars(select(JobRun).order_by(desc(JobRun.id)).limit(500)):
        if run.job not in out:
            out[run.job] = run
    return out


def freshness(s: Session) -> list[DataFreshness]:
    return list(s.scalars(select(DataFreshness).order_by(DataFreshness.source)))


def log_telegram_event(
    s: Session, user_id: int, authorised: bool, kind: str, text: str | None, callback_data: str | None, handled: bool
) -> None:
    s.add(
        TelegramEvent(
            telegram_user_id=user_id,
            authorised=authorised,
            kind=kind,
            text=text,
            callback_data=callback_data,
            handled=handled,
        )
    )
