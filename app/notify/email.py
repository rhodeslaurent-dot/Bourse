"""SMTP (Gmail) sender. Secrets from the environment only (CLAUDE.md rule 6)."""

from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage

from app.notify.base import Event


def smtp_settings() -> dict[str, str] | None:
    user = os.environ.get("SMTP_USER")
    pwd = os.environ.get("SMTP_PASSWORD")
    to = os.environ.get("EMAIL_TO")
    if not (user and pwd and to):
        return None
    return {
        "host": os.environ.get("SMTP_HOST", "smtp.gmail.com"),
        "port": os.environ.get("SMTP_PORT", "587"),
        "user": user,
        "password": pwd,
        "to": to,
        "from": os.environ.get("EMAIL_FROM", user),
    }


def send_email(subject: str, text: str, html: str | None = None) -> None:
    cfg = smtp_settings()
    if cfg is None:
        raise RuntimeError("SMTP non configuré (SMTP_USER / SMTP_PASSWORD / EMAIL_TO)")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg["from"]
    msg["To"] = cfg["to"]
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    with smtplib.SMTP(cfg["host"], int(cfg["port"]), timeout=30) as smtp:
        smtp.starttls()
        smtp.login(cfg["user"], cfg["password"])
        smtp.send_message(msg)


def send_event_email(event: Event) -> None:
    send_email(f"[BOURSE-PILOT {event.level}] {event.title}", "\n".join(event.lines))
