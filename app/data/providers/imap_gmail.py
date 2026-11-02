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
from typing import Any

from app.data.providers.rss import strip_html
from app.domain.letter_rules import merge_levels, read_levels, sentences
from app.domain.names import find_mentions

ISIN_RE = re.compile(r"\b([A-Z]{2}[A-Z0-9]{9}\d)\b")
# paragraphs naming no value that still belong to the fiche of the value named just before (lot R3, docs/38 §38.6)
CONTINUATION_PARAGRAPHS = 3
# a paragraph about the market (an index, a rate, a commodity) ends the fiche: its levels are never the value's
MARKET_RE = re.compile(
    r"\b(?:cac\s*40|sbf\s*120|euro\s*stoxx|stoxx\s*600|dax|ftse|s&p\s*500|nasdaq|dow\s*jones|nikkei|bund|oat|brent"
    r"|wti|bitcoin|eur\s*/\s*usd|euro\s*/\s*dollar)\b",
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
    html: str = ""  # raw HTML part (links such as « Lire la suite », docs/19 §19.6)
    linked: dict[str, Any] | None = None  # page behind the link once fetched (status, url, time)


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


def message_html(msg: Message) -> str:
    out = []
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            payload = part.get_payload(decode=True)
            if payload is not None:
                out.append(payload.decode(part.get_content_charset() or "utf-8", errors="replace"))
    return "\n".join(out)


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


def _domain(sender: str) -> str:
    m = re.search(r"@([A-Za-z0-9.\-]+)", sender)
    return m.group(1).lower().strip(".") if m else ""


def _matches(pattern: str, sender: str) -> bool:
    """« @capital.fr » matches the domain and its subdomains (news@info.capital.fr — 26/10/2026: the Momentum
    Capital letter was never recognised because it is sent from a subdomain); « @prismamedia » matches any
    domain label starting with it; any other pattern is a plain substring of the sender."""
    p = pattern.lower().strip()
    if not p.startswith("@"):
        return p in sender.lower()
    q, dom = p[1:].strip("."), _domain(sender)
    if not q or not dom:
        return False
    if "." in q:  # a full domain: itself or a subdomain, never capital.fr.evil.com
        return dom == q or dom.endswith("." + q)
    return dom.startswith(q + ".") or ("." + q + ".") in dom  # a name without TLD (« @prismamedia »)


def source_for(sender: str, senders: dict[str, list[str]]) -> str | None:
    for source, patterns in senders.items():
        if any(_matches(p, sender) for p in patterns):
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
        message_html(msg),
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


def _mentions(text: str, aliases: dict[str, str]) -> list[tuple[str | None, str | None]]:
    """(ISIN, name key) of the values written in ``text``: ISINs, then known names (never tickers); one per value."""
    found: list[tuple[str | None, str | None]] = [(m, None) for m in ISIN_RE.findall(text)]
    found += [(isin, key) for isin, key in find_mentions(text, aliases)]
    out: list[tuple[str | None, str | None]] = []
    keys: set[str] = set()
    for isin, name in found:
        k = isin or name or ""
        if k and k not in keys:
            keys.add(k)
            out.append((isin, name))
    return out


def _sentence_levels(para: str, aliases: dict[str, str]) -> tuple[dict[str, dict[str, float]], str | None]:
    """A paragraph naming several values: each sentence's levels go to the value it names, or to the last value
    named before it in the paragraph; a sentence naming several values gives its levels to none (never shared).
    Returns the levels by value key and the value the paragraph ends on (None when ambiguous)."""
    by: dict[str, dict[str, float]] = {}
    owner: str | None = None
    for sent in sentences(para):
        named = _mentions(sent, aliases)
        if len(named) == 1:
            owner = named[0][0] or named[0][1]
        elif len(named) > 1:
            owner = None
            continue
        lv = read_levels(sent)
        if owner and lv:
            by[owner] = merge_levels(by.get(owner, {}), lv)
    return by, owner


def extract_values(body: str, aliases: dict[str, str] | None = None) -> list[ExtractedValue]:
    """Rule-based extraction: ISINs and known names (aliases: normalized name → ISIN, ``news.alias_index``) with the
    direction of the paragraph and the price levels written for each value (``app.domain.letter_rules``: a cue such
    as « objectif », « vise », « clôture au-dessus de », « invalidation » gives its kind to the numbers after it).

    - A paragraph naming one value gives it its levels; one naming several shares them out sentence by sentence.
    - A fiche often names its value in a title and writes its levels in the next paragraphs: up to
      ``CONTINUATION_PARAGRAPHS`` paragraphs naming no value still give their levels to the value named just before
      (an index's levels caught this way are dropped at the reception by ``letters.level_price_band``).
    - A value named again later is completed by the new levels; its first direction is kept unless it was neutral
      (a fiche's title, then the paragraph that recommends).
    - A paragraph about the market (CAC 40, Bund, Brent…) ends the fiche.
    The LLM refines afterwards; every level it adds must be written in the letter (``newsletters._number_in_text``)."""
    aliases = aliases or {}
    out: list[ExtractedValue] = []
    by_key: dict[str, ExtractedValue] = {}
    owner: ExtractedValue | None = None  # the value whose fiche the next unnamed paragraphs continue
    gap = 0
    keys_of: dict[str, list[str]] | None = None  # ISIN → its names, built at the first paragraph naming several
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n|\r\n\r\n", body) if p.strip()]
    for para in paragraphs:
        found = _mentions(para, aliases)
        if not found:
            gap += 1
            if MARKET_RE.search(para):
                owner = None
            elif owner is not None and gap <= CONTINUATION_PARAGRAPHS:
                lv = read_levels(para)
                if lv:
                    owner.levels = merge_levels(owner.levels, lv)
            continue
        gap = 0
        if len(found) == 1:
            k = found[0][0] or found[0][1] or ""
            shares, last = {k: read_levels(para)}, k
        else:  # sentence by sentence, among the names of this paragraph's values only (a referential has 10⁴ names)
            if keys_of is None:
                keys_of = {}
                for name_key, isin_of in aliases.items():
                    keys_of.setdefault(isin_of, []).append(name_key)
            sub = {k: i for i, _ in found if i for k in keys_of.get(i, [])}
            shares, last = _sentence_levels(para, sub)
        direction = direction_of(para)
        for isin, name in found:
            key = isin or name or ""
            v = by_key.get(key)
            if v is None:
                v = ExtractedValue(isin, name, direction, {}, para[:240])
                by_key[key] = v
                out.append(v)
            elif v.direction == "neutral" and direction != "neutral":
                v.direction, v.snippet = direction, para[:240]  # a title first, the recommendation after
            if shares.get(key):
                v.levels = merge_levels(v.levels, shares[key])
        owner = by_key.get(last) if last else None
    return out


def search_criteria(senders: dict[str, list[str]], since: date, include_read: bool = False) -> str:
    """IMAP SEARCH for the mails of known newsletter senders only (prefix ``OR`` chain), so a shared mailbox
    is never scanned wholesale. ``include_read``: also the letters already opened in Gmail (a letter read on
    the phone before the poll was never ingested); the ingestion deduplicates them."""
    patterns = sorted({p.lstrip("@").replace('"', "") for ps in senders.values() for p in ps if p.strip("@ ")})
    base = ("" if include_read else "UNSEEN ") + f"SINCE {since.strftime('%d-%b-%Y')}"
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
        self,
        senders: dict[str, list[str]],
        mailbox: str = "INBOX",
        limit: int = 50,
        since_days: int = 3,
        include_read: bool = False,
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
            _, data = conn.search(None, search_criteria(senders, since, include_read))
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
