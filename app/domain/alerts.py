"""Alert routing rules (docs/09 §9.4, CLAUDE.md conventions). Pure decisions; persistence outside.

- P2/P3: deduplicated by (kind, isin, account, day).
- P1: grouped **by incident** — first alert immediately, reminder every ``p1_repeat_minutes``
  while the incident lasts; distinct incidents are never merged.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta


@dataclass(frozen=True)
class AlertDecision:
    send: bool
    reason: str
    repeat_count: int = 0


def decide_p1(last_sent_at: datetime | None, repeat_count: int, now: datetime, repeat_minutes: int) -> AlertDecision:
    if last_sent_at is None:
        return AlertDecision(True, "première alerte de l'incident", 0)
    if now - last_sent_at >= timedelta(minutes=repeat_minutes):
        return AlertDecision(True, f"rappel #{repeat_count + 1} (incident toujours ouvert)", repeat_count + 1)
    return AlertDecision(False, "incident déjà signalé, rappel pas encore dû", repeat_count)


def decide_dedup(already_sent_today: bool, kind: str, isin: str | None, day: date) -> AlertDecision:
    if already_sent_today:
        return AlertDecision(False, f"déjà envoyé ({kind}, {isin}, {day.isoformat()})")
    return AlertDecision(True, "nouveau")


def dedup_key(kind: str, isin: str | None, account_id: int | None, day: date) -> tuple[str, str, str, str]:
    return (kind, isin or "", str(account_id or ""), day.isoformat())


def allowed_in_mode(level: str, mode: str, intraday: bool) -> bool:
    """docs/02 §2.3: P1 always; P2 only in ``disponible`` (deferred otherwise); nothing in absent
    except P1; P3 grouped."""
    if level == "P1":
        return True
    if mode == "absent":
        return False
    if mode == "reunion":
        return level in ("P3", "P4") or (level == "P2" and not intraday)
    return True
