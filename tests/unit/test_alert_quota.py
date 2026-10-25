"""docs/02 §2.3 — daily P2 quota in « disponible »: beyond it the alert is stored, not pushed; P1 never capped."""

from datetime import UTC, datetime

from app.db.models import Alert
from app.db.session import db_session
from app.notify.base import Event
from app.services.alerts import AlertService


class _Notifier:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def notify(self, event):
        self.sent.append(event.kind)
        return ["telegram"]


def test_p2_quota_then_stored_not_sent_and_p1_unaffected(db_engine):
    n = _Notifier()
    svc = AlertService(n, 30, {"disponible": 2})
    now = datetime(2026, 10, 26, 9, 0, tzinfo=UTC)
    with db_session() as s:
        res = [
            svc.emit(s, Event("P2", f"kind{k}", f"t{k}", [], isin=f"FR{k:010d}"), "disponible", now=now)
            for k in range(3)
        ]
        assert res == [True, True, False] and len(n.sent) == 2
        assert s.query(Alert).filter(Alert.sent_at.is_(None)).count() == 1
        assert svc.emit(s, Event("P1", "stop", "t", [], isin="FR0000000009", incident_id="i1"), "disponible", now=now)
        # another mode: no quota from this parameter
        assert svc.emit(s, Event("P2", "kindx", "t", [], isin="FR0000000010"), "disponible_sans_quota", now=now)
