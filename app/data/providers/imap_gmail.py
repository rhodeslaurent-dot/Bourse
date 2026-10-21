"""IMAP (Gmail) newsletter ingestion (docs/04 §4.5): dedicated mailbox, labels/senders per source,
receipt time recorded (latency measurement), full body kept private. Parsing is pure and tested
on anonymised ``.eml`` fixtures; the IMAP client is a thin wrapper."""

from __future__ import annotations

import email
import hashlib
import imaplib
import os
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parsedate_to_datetime

from app.data.providers.rss import strip_html

ISIN_RE = re.compile(r"\b([A-Z]{2}[A-Z0-9]{9}\d)\b")
LEVEL_RE = re.compile(
    r"(?P<kind>objectif|cible|stop|support|r[ée]sistance|achat|entr[ée]e|invalidation|seuil)\s*(?:de|à|a|:|=)?\s*(?P<value>\d{1,5}(?:[.,]\d{1,3})?)\s*(?:€|EUR|euros)?",
    re.I,
)


@dataclass(frozen=True)
class NewsletterMail:
    source: str
    message_id: str
    subject: str
    sender: str
    sent_at: datetime | None
    received_at: datetime
    body_text: str
    dedup_hash: str


@dataclass
class ExtractedValue:
    isin: str | None
    name: str | None
    direction: str  # buy | sell | watch | neutral
    levels: dict[str, float] = field(default_factory=dict)
    snippet: str = ""


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:  # noqa: BLE001
        return value


def message_text(msg: Message) -> str:
    """Prefer text/plain; fall back to HTML stripped."""
    plain, html = [], []
    for part in msg.walk():
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html"):
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        charset = part.get_content_charset() or "utf-8"
        text = payload.decode(charset, errors="replace")
        (plain if ctype == "text/plain" else html).append(text)
    if plain:
        return "\n".join(plain)
    return strip_html("\n".join(html))


def source_for(sender: str, senders: dict[str, list[str]]) -> str | None:
    s = sender.lower()
    for source, patterns in senders.items():
        if any(p.lower() in s for p in patterns):
            return source
    return None


def parse_eml(raw: bytes, senders: dict[str, list[str]], received_at: datetime | None = None) -> NewsletterMail | None:
    msg = email.message_from_bytes(raw)
    sender = _decode(msg.get("From"))
    source = source_for(sender, senders)
    if source is None:
        return None
    sent = None
    if msg.get("Date"):
        try:
            sent = parsedate_to_datetime(msg["Date"])
            sent = sent if sent.tzinfo else sent.replace(tzinfo=UTC)
        except (TypeError, ValueError):
            sent = None
    received = received_at or sent or datetime.now(UTC)
    mid = _decode(msg.get("Message-ID")) or hashlib.sha256(raw).hexdigest()
    return NewsletterMail(
        source,
        mid,
        _decode(msg.get("Subject")),
        sender,
        sent,
        received,
        message_text(msg),
        hashlib.sha256(f"{source}|{mid}".encode()).hexdigest(),
    )


BUY_WORDS = ("achat", "acheter", "renforcer", "buy", "haussier", "opportunité", "recommandation d'achat")
SELL_WORDS = ("vente", "vendre", "alléger", "sell", "baissier", "sortir")
WATCH_WORDS = ("surveiller", "watch", "à suivre", "sous surveillance")


def direction_of(text: str) -> str:
    t = text.lower()
    if any(w in t for w in SELL_WORDS):
        return "sell"
    if any(w in t for w in BUY_WORDS):
        return "buy"
    if any(w in t for w in WATCH_WORDS):
        return "watch"
    return "neutral"


def extract_values(body: str, aliases: dict[str, str] | None = None) -> list[ExtractedValue]:
    """Rule-based extraction: ISINs and known names (aliases: lowercase alias → ISIN) with the
    direction and price levels found in the surrounding paragraph. The LLM refines afterwards."""
    aliases = aliases or {}
    out: list[ExtractedValue] = []
    seen: set[str] = set()
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n|\r\n\r\n", body) if p.strip()]
    for para in paragraphs:
        found: list[tuple[str | None, str | None]] = [(m, None) for m in ISIN_RE.findall(para)]
        low = para.lower()
        for alias, isin in aliases.items():
            if re.search(rf"\b{re.escape(alias)}\b", low):
                found.append((isin, alias))
        levels: dict[str, float] = {}
        for m in LEVEL_RE.finditer(para):
            kind = m.group("kind").lower().replace("é", "e")
            key = {"cible": "objectif", "entree": "achat", "seuil": "stop"}.get(kind, kind)
            try:
                levels.setdefault(key, float(m.group("value").replace(",", ".")))
            except ValueError:
                continue
        for isin, name in found:
            key = isin or name or ""
            if key in seen:
                continue
            seen.add(key)
            out.append(ExtractedValue(isin, name, direction_of(para), levels, para[:240]))
    return out


def search_criteria(senders: dict[str, list[str]], since: date) -> str:
    """IMAP SEARCH for unread mails of known newsletter senders only (prefix ``OR`` chain), so a
    shared mailbox is never scanned wholesale: other unread mails are not even fetched."""
    patterns = sorted({p.lstrip("@").replace('"', "") for ps in senders.values() for p in ps if p.strip("@ ")})
    base = f"UNSEEN SINCE {since.strftime('%d-%b-%Y')}"
    if not patterns:
        return base
    froms = " ".join(f'FROM "{p}"' for p in patterns)
    return f"{base} {'OR ' * (len(patterns) - 1)}{froms}"


class GmailImap:
    def __init__(self, host: str, user: str | None = None, password: str | None = None) -> None:
        self.host = host
        self.user = user or os.environ.get("IMAP_USER", "")
        self.password = password or os.environ.get("IMAP_PASSWORD", "")

    def fetch_unseen(
        self, senders: dict[str, list[str]], mailbox: str = "INBOX", limit: int = 50, since_days: int = 3
    ) -> list[NewsletterMail]:
        """Unread newsletters of known senders. Mails are read with ``BODY.PEEK`` (state unchanged);
        only the ones recognised as newsletters are then marked as read, so a mailbox shared with
        other uses keeps its other unread mails."""
        if not (self.user and self.password):
            raise RuntimeError("IMAP_USER / IMAP_PASSWORD absents")
        out: list[NewsletterMail] = []
        since = (datetime.now(UTC) - timedelta(days=since_days)).date()
        with imaplib.IMAP4_SSL(self.host) as conn:
            conn.login(self.user, self.password)
            status, _ = conn.select(f'"{mailbox}"' if " " in mailbox else mailbox)
            if status != "OK":
                raise RuntimeError(f"dossier IMAP introuvable : {mailbox} (libellé Gmail à créer ?)")
            _, data = conn.search(None, search_criteria(senders, since))
            ids = data[0].split()[-limit:]
            for uid in ids:
                _, parts = conn.fetch(uid, "(INTERNALDATE BODY.PEEK[])")
                raw = b""
                internal = None
                for part in parts:
                    if isinstance(part, tuple):
                        raw = part[1]
                        m = re.search(rb'INTERNALDATE "([^"]+)"', part[0])
                        if m:
                            internal = imaplib.Internaldate2tuple(part[0])
                # Internaldate2tuple returns a *local* time tuple → epoch → UTC (M14); without it, poll time.
                received = datetime.fromtimestamp(time.mktime(internal), tz=UTC) if internal else datetime.now(UTC)
                mail = parse_eml(raw, senders, received)
                if mail:
                    out.append(mail)
                    conn.store(uid, "+FLAGS", "\\Seen")
        return out


LIST_RE = re.compile(rb'\((?P<flags>[^)]*)\) (?:"[^"]*"|NIL) (?P<name>.+)$')


def all_mail_folder(list_lines: list[bytes]) -> str | None:
    """Gmail's « All mail » folder, found by its ``\\All`` attribute (its name is localised)."""
    for line in list_lines:
        m = LIST_RE.match(line or b"")
        if m and b"\\All" in m.group("flags"):
            return m.group("name").decode("utf-8", errors="replace")
    return None


def history_criteria(gmail_query: str, since: date | None) -> str:
    q = gmail_query.replace("\\", "").replace('"', "")
    return f'X-GM-RAW "{q}"' + (f" SINCE {since.strftime('%d-%b-%Y')}" if since else "")


@dataclass
class HistoryHeader:
    sender: str
    subject: str
    received_at: datetime | None


class GmailHistory:
    """Read-only access to a mailbox for a one-off history import (docs/19 §19.2): the « All mail »
    folder is opened with EXAMINE and every fetch uses BODY.PEEK, so no flag ever changes. The
    password is passed in by the caller and never stored."""

    def __init__(self, host: str, user: str, password: str) -> None:
        self.host, self.user, self.password = host, user, password
        self.conn: imaplib.IMAP4_SSL | None = None
        self.folder = "INBOX"

    def __enter__(self) -> GmailHistory:
        self.conn = imaplib.IMAP4_SSL(self.host)
        self.conn.login(self.user, self.password)
        _, lines = self.conn.list()
        self.folder = all_mail_folder([x for x in lines if isinstance(x, bytes)]) or "INBOX"
        status, _ = self.conn.select(self.folder, readonly=True)
        if status != "OK":
            raise RuntimeError(f"dossier IMAP illisible : {self.folder}")
        return self

    def __exit__(self, *exc: object) -> None:
        if self.conn is not None:
            try:
                self.conn.logout()
            except Exception:  # noqa: BLE001
                pass

    def search(self, gmail_query: str, since: date | None = None) -> list[bytes]:
        assert self.conn is not None
        _, data = self.conn.search(None, history_criteria(gmail_query, since))
        return data[0].split() if data and data[0] else []

    @staticmethod
    def _internal(parts: list) -> datetime | None:  # type: ignore[type-arg]
        for part in parts:
            if isinstance(part, tuple) and b"INTERNALDATE" in part[0]:
                t = imaplib.Internaldate2tuple(part[0])
                if t:
                    return datetime.fromtimestamp(time.mktime(t), tz=UTC)
        return None

    def header(self, uid: bytes) -> HistoryHeader:
        assert self.conn is not None
        _, parts = self.conn.fetch(uid, "(INTERNALDATE BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
        raw = next((p[1] for p in parts if isinstance(p, tuple)), b"")
        msg = email.message_from_bytes(raw)
        return HistoryHeader(_decode(msg.get("From")), _decode(msg.get("Subject")), self._internal(parts))

    def mail(self, uid: bytes, source: str) -> NewsletterMail | None:
        """Full letter, attributed to ``source`` (the search query selected it)."""
        assert self.conn is not None
        _, parts = self.conn.fetch(uid, "(INTERNALDATE BODY.PEEK[])")
        raw = next((p[1] for p in parts if isinstance(p, tuple)), b"")
        return parse_eml(raw, {source: [""]}, self._internal(parts)) if raw else None
