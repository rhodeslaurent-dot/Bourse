"""Notification abstraction: ``notify(event, level)`` → Telegram and/or e-mail (docs/09 §9.4).

Phase 0 ships the transport and the level → channel routing. Deduplication by
(instrument, account, kind, day) and P1 incident grouping are wired in phase 1 with alerts.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

Level = Literal["P1", "P2", "P3", "P4"]

log = logging.getLogger("bourse.notify")


@dataclass
class Button:
    label: str
    data: str  # callback_data, ≤ 64 bytes


@dataclass
class Event:
    level: Level
    kind: str
    title: str
    lines: list[str] = field(default_factory=list)
    buttons: list[Button] = field(default_factory=list)
    isin: str | None = None
    incident_id: str | None = None

    def telegram_text(self) -> str:
        body = [f"[{self.level}] {self.title}", *self.lines[:11]]
        return "\n".join(body)  # ≤ 12 lines (CLAUDE.md conventions)

    def message_hash(self) -> str:
        return hashlib.sha256((self.kind + self.title + "\n".join(self.lines)).encode()).hexdigest()


CHANNELS_BY_LEVEL: dict[str, tuple[str, ...]] = {
    "P1": ("telegram", "email"),
    "P2": ("telegram",),
    "P3": ("telegram",),
    "P4": ("email",),
}


class Notifier:
    def __init__(self, telegram_send=None, email_send=None) -> None:  # type: ignore[no-untyped-def]
        self._tg = telegram_send
        self._mail = email_send
        self.sent: list[tuple[str, Event, datetime]] = []

    def notify(self, event: Event) -> list[str]:
        used: list[str] = []
        for ch in CHANNELS_BY_LEVEL[event.level]:
            try:
                if ch == "telegram" and self._tg:
                    self._tg(event)
                    used.append(ch)
                elif ch == "email" and self._mail:
                    self._mail(event)
                    used.append(ch)
            except Exception as exc:  # noqa: BLE001
                log.warning("notification %s failed on %s: %s", event.kind, ch, exc)
        for ch in used:
            self.sent.append((ch, event, datetime.now(UTC)))
        return used
