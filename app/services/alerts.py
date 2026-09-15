"""Alert service: persists ``alerts`` rows, applies dedup (P2/P3) and incident grouping (P1),
respects the availability mode, then hands the event to the notifier."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Alert
from app.domain.alerts import allowed_in_mode, decide_p1
from app.notify.base import Event, Notifier

log = logging.getLogger("bourse.alerts")


class AlertService:
    def __init__(self, notifier: Notifier, p1_repeat_minutes: int) -> None:
        self.notifier = notifier
        self.p1_repeat_minutes = p1_repeat_minutes

    def emit(
        self,
        s: Session,
        event: Event,
        mode: str,
        intraday: bool = True,
        now: datetime | None = None,
        account_id: int | None = None,
        position_id: int | None = None,
    ) -> bool:
        now = now or datetime.now(UTC)
        day: date = now.date()
        if not allowed_in_mode(event.level, mode, intraday):
            log.info("alerte %s différée (mode %s)", event.kind, mode)
            self._store(s, event, now, day, account_id, position_id, sent=False, incident=event.incident_id)
            return False
        if event.level == "P1":
            assert event.incident_id, "une P1 porte toujours un incident_id"
            existing = s.scalar(
                select(Alert)
                .where(Alert.incident_id == event.incident_id, Alert.sent_at.is_not(None))
                .order_by(Alert.id.desc())
            )
            last_sent = (
                existing.sent_at.replace(tzinfo=UTC)
                if existing and existing.sent_at and existing.sent_at.tzinfo is None
                else (existing.sent_at if existing else None)
            )
            decision = decide_p1(last_sent, existing.repeat_count if existing else 0, now, self.p1_repeat_minutes)
            if not decision.send:
                return False
            channels = self.notifier.notify(event)
            row = self._store(
                s,
                event,
                now,
                day,
                account_id,
                position_id,
                sent=bool(channels),
                incident=event.incident_id,
                channels=channels,
            )
            row.repeat_count = decision.repeat_count
            return bool(channels)
        dup = s.scalar(
            select(Alert).where(
                Alert.kind == event.kind,
                Alert.isin == event.isin,
                Alert.account_id == account_id,
                Alert.day == day,
                Alert.sent_at.is_not(None),
            )
        )
        if dup is not None:
            return False
        channels = self.notifier.notify(event)
        self._store(s, event, now, day, account_id, position_id, sent=bool(channels), incident=None, channels=channels)
        return bool(channels)

    def close_incident(self, s: Session, incident_id: str, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        n = 0
        for row in s.scalars(select(Alert).where(Alert.incident_id == incident_id, Alert.seen_at.is_(None))):
            row.seen_at = now
            n += 1
        return n

    @staticmethod
    def _store(
        s: Session,
        event: Event,
        now: datetime,
        day: date,
        account_id: int | None,
        position_id: int | None,
        sent: bool,
        incident: str | None,
        channels: list[str] | None = None,
    ) -> Alert:
        row = Alert(
            ts=now,
            level=event.level,
            kind=event.kind,
            isin=event.isin,
            account_id=account_id,
            position_id=position_id,
            incident_id=incident,
            channel=",".join(channels or []) or "none",
            message_hash=event.message_hash(),
            day=day,
            sent_at=now if sent else None,
            payload={"title": event.title, "lines": event.lines},
        )
        s.add(row)
        s.flush()
        return row
