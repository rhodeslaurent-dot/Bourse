"""docs/23 §23.7 — media opinions (podcasts, radio, press): values cited with their stance, the
« media pressure » caution (a cited buy followed by a jump tends to reverse), and the record measured
by the tool for each source. Media opinions never trigger a proposal on their own."""

from __future__ import annotations

import logging
import re
import statistics
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import MediaMention, NewsItem, PriceEod
from app.domain.timeutil import PARIS
from app.llm.gateway import LlmUnavailable
from app.llm.schemas import MediaExtraction

log = logging.getLogger("bourse.media")


@dataclass(frozen=True)
class MediaParams:
    reversal_window_days: int = 20
    jump_flag_pct: float = 0.03
    track_horizon_sessions: int = 20
    track_min_samples: int = 20


def media_params(config: Any) -> MediaParams:
    m = getattr(config.params, "media", None) or {}
    return MediaParams(
        int(m.get("reversal_window_days", 20)),
        float(m.get("jump_flag_pct", 0.03)),
        int(m.get("track_horizon_sessions", 20)),
        int(m.get("track_min_samples", 20)),
    )


def _aware(ts: datetime) -> datetime:
    return ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts


def close_before(s: Session, isin: str, day: date) -> PriceEod | None:
    return s.scalar(select(PriceEod).where(PriceEod.isin == isin, PriceEod.day < day).order_by(PriceEod.day.desc()))


_LEGAL = {"sa", "se", "nv", "ag", "plc", "spa", "sca", "ab", "asa", "oyj", "as", "inc", "corp", "co", "ltd"}
_LEGAL2 = {"s a", "s e", "n v", "a s", "s p a"}  # « S.A. », « N.V. », « S.p.A. » once punctuation is removed


def norm_name(name: str) -> str:
    """« Maurel & Prom S.A. » → « maurel et prom »: accents, punctuation and legal forms removed."""
    import unicodedata

    txt = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower().replace("&", " et ")
    words = re.sub(r"[^a-z0-9]+", " ", txt).split()
    while len(words) > 1:
        if len(words) > 3 and " ".join(words[-3:]) in _LEGAL2:
            words = words[:-3]
        elif len(words) > 2 and " ".join(words[-2:]) in _LEGAL2:
            words = words[:-2]
        elif words[-1] in _LEGAL:
            words = words[:-1]
        else:
            break
    return " ".join(words)


def name_matcher(aliases: dict[str, str]) -> dict[str, str]:
    """Normalised name → ISIN; a name shared by two ISINs is dropped (never matched by guess)."""
    out: dict[str, str] = {}
    clash: set[str] = set()
    for alias, isin in aliases.items():
        n = norm_name(alias)
        if not n:
            continue
        if n in out and out[n] != isin:
            clash.add(n)
        out.setdefault(n, isin)
    return {k: v for k, v in out.items() if k not in clash}


_MANAGER = re.compile(
    r"^[A-ZÉÈÀÂÎÔÛÇ][a-zéèêëàâîïôöûüç'’\-]+(?:[ -][A-ZÉÈ][a-zéèêëàâîïôöûüç'’\-]+)* [A-ZÉÈÀÂÎÔÛÇ][A-ZÉÈÀÂÎÔÛÇ'’\- ]+$"
)
_SECTION = {"achat": "achat", "achats": "achat", "renforcement": "achat", "vente": "vente", "ventes": "vente",
            "allegement": "vente", "allègement": "vente"}  # fmt: skip


def parse_manager_writeup(text: str) -> list[dict[str, Any]]:
    """Rules for a write-up organised by manager (« Julie JOURDAN » / « Achat : » / one value per line /
    « Vente : »…), e.g. bourse-portefeuille-conseil.fr: names, stance and manager copied as written, no LLM.
    Reading stops at « Retrouvez » (links to other portfolios); prose lines are never taken as values."""
    out: list[dict[str, Any]] = []
    speaker: str | None = None
    stance: str | None = None
    for raw in (text or "").splitlines():
        ln = raw.strip()
        if not ln:
            continue
        key = ln.rstrip(" :").strip().lower()
        if key.startswith("retrouvez"):
            break
        if key in _SECTION:
            stance = _SECTION[key]
            continue
        if _MANAGER.match(ln):
            speaker, stance = ln, None
            continue
        if stance and speaker and len(ln) <= 60 and not ln.endswith((".", "!", "?")):
            label = "Achat" if stance == "achat" else "Vente"
            out.append({"name": ln, "stance": stance, "speaker": speaker, "snippet": f"{speaker} — {label} : {ln}"})
        elif ln.endswith((".", "!", "?")):
            stance = None  # a sentence closes the list
    return out


_TITLE_DATE = re.compile(r"\b(\d{1,2})/(\d{1,3})/(\d{4})\b")


def broadcast_day(title: str, published: datetime) -> date | None:
    """Date of the show written in the title (« 16/10/2026 – C'est Votre Argent », typos such as « 16/010 »
    included) when it is within the 10 days before publication; else None (the publication date is used)."""
    m = _TITLE_DATE.search(title or "")
    if not m:
        return None
    try:
        d = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None
    return d if published.date() - timedelta(days=10) <= d <= published.date() else None


def close_on_or_before(s: Session, isin: str, day: date) -> PriceEod | None:
    return s.scalar(select(PriceEod).where(PriceEod.isin == isin, PriceEod.day <= day).order_by(PriceEod.day.desc()))


def _fetch_article(url: str, feed: dict[str, Any]) -> str | None:
    """Full text of the article a media feed points to; only the feed's own site is allowed (no redirect
    elsewhere). None when the page is reserved, refused or unreadable."""
    from urllib.parse import urlparse

    from app.data.providers.linked_page import fetch_article

    host = urlparse(str(feed.get("url") or url)).hostname or ""
    page, _ = fetch_article(url, [host.removeprefix("www.")])
    if page.status != "ok" or not page.text:
        log.info("article média %s non lu : %s %s", url, page.status, page.detail or "")
        return None
    return page.text


def extract_mentions(
    s: Session,
    rows: list[NewsItem],
    gateway: Any,
    aliases: dict[str, str],
    feed: dict[str, Any] | None = None,
    fetch: Any | None = None,
) -> int:
    """Values cited by new media items (LLM extraction, then checks): the name must be written in the text
    and known to the referential (else kept without ISIN, never matched by guess). Each item is processed
    once (``classification["media"]``)."""
    n = 0
    for row in rows:
        cls = dict(row.classification or {})
        if "media" in cls:
            continue
        text = f"{row.title}\n\n{row.body or ''}"
        if (feed or {}).get("follow_link") and row.url:  # the feed only carries an excerpt (« […] »)
            page = (fetch or _fetch_article)(row.url, feed or {})
            if page is None:
                continue  # page unreadable this time: retried at the next cycle, never extracted from the excerpt
            text = f"{row.title}\n\n{page}"
        values: list[dict[str, Any]] = []
        rejected: list[str] = []
        if (feed or {}).get("parser") == "gerants":
            values = parse_manager_writeup(text)  # fixed layout read by rules (docs/23 §23.7)
        if not values and (gateway is None or not getattr(gateway, "enabled", True)):
            continue  # no LLM: kept unread and retried — never marked « analysé » without having been read
        if not values:
            try:
                out, _ = gateway.parse(
                    s, "media_extract", "media_extract", text[:12000], MediaExtraction, max_tokens=3000
                )
                low = text.lower()
                for v in out.values:
                    if v.name.lower() not in low:
                        log.info("média %s : « %s » absent du texte, rejeté", row.source, v.name)
                        rejected.append(v.name[:60])
                        continue
                    values.append(v.model_dump())
            except LlmUnavailable as exc:
                log.warning("extraction média indisponible (%s) : épisode gardé sans valeurs", exc)
                continue  # retried at the next cycle
        published = _aware(row.published_at or row.fetched_at)
        shown = broadcast_day(row.title, published)  # the show's date, not the write-up's (docs/23 §23.7.4)
        day = shown or published.date()
        if shown is not None:
            published = datetime.combine(shown, time(18, 0), tzinfo=PARIS).astimezone(UTC)
        after_close = bool((feed or {}).get("broadcast_after_close"))
        names = name_matcher(aliases)
        for v in values:
            isin = aliases.get(v["name"].lower()) or names.get(norm_name(v["name"]))
            ref = (close_on_or_before if after_close else close_before)(s, isin, day) if isin else None
            exists = s.scalar(
                select(MediaMention.id).where(
                    MediaMention.source == row.source,
                    MediaMention.item_guid == row.guid,
                    MediaMention.name == v["name"],
                )
            )
            if exists:
                continue
            s.add(
                MediaMention(
                    source=row.source, item_guid=row.guid[:255], published_at=published, isin=isin,
                    name=v["name"][:128], stance=v["stance"], speaker=(v.get("speaker") or None),
                    snippet=(v.get("snippet") or "")[:300], price_ref=ref.close if ref else None,
                    price_ref_day=ref.day if ref else None, received_at=datetime.now(UTC),
                )
            )  # fmt: skip
            n += 1
        cls["media"] = [{k: v[k] for k in ("name", "stance", "speaker")} for v in values]
        if rejected:  # media-check --feed shows them: a name the model wrote differently from the text
            cls["media_rejetes"] = rejected[:10]
        row.classification = cls
    s.flush()
    return n


def recent(s: Session, isin: str, now: datetime, days: int) -> list[MediaMention]:
    return list(
        s.scalars(
            select(MediaMention)
            .where(MediaMention.isin == isin, MediaMention.published_at >= now - timedelta(days=days))
            .order_by(MediaMention.published_at.desc())
        )
    )


def pressure(mentions: list[MediaMention], price: float, jump_pct: float) -> MediaMention | None:
    """The latest cited buy whose price has since risen by ≥ ``jump_pct`` (docs/23 §23.7.3: the jump that
    follows a media buy tends to reverse within ~20 sessions — Barber-Loeffler, Engelberg, Kerl-Walter, Lidén)."""
    for m in mentions:
        if m.stance == "achat" and m.price_ref and price / m.price_ref - 1 >= jump_pct:
            return m
    return None


@dataclass
class SourceRecord:
    source: str
    stance: str
    n: int
    mean: float | None
    median: float | None
    hit_rate: float | None
    sufficient: bool

    @property
    def favourable(self) -> bool:
        return bool(self.sufficient and self.mean is not None and self.mean > 0 and (self.hit_rate or 0) >= 0.5)


def track_record(s: Session, horizon: int = 20, min_samples: int = 20) -> list[SourceRecord]:
    """Per source and stance: return from the close before the broadcast to the close ``horizon`` sessions
    later (stored sessions of the value). A citation without both prices is not counted."""
    returns: dict[tuple[str, str], list[float]] = {}
    for m in s.scalars(select(MediaMention).where(MediaMention.isin.is_not(None), MediaMention.price_ref.is_not(None))):
        assert m.isin is not None and m.price_ref and m.price_ref_day
        later = list(
            s.scalars(
                select(PriceEod.close)
                .where(PriceEod.isin == m.isin, PriceEod.day > m.price_ref_day)
                .order_by(PriceEod.day)
                .limit(horizon)
            )
        )
        if len(later) < horizon:
            continue
        returns.setdefault((m.source, m.stance), []).append(later[-1] / m.price_ref - 1)
    out = []
    for (src, stance), rs in sorted(returns.items()):
        ok = len(rs) >= min_samples
        out.append(
            SourceRecord(
                src, stance, len(rs), statistics.fmean(rs), statistics.median(rs),
                sum(r > 0 for r in rs) / len(rs), ok,
            )
        )  # fmt: skip
    return out


def favourable_sources(s: Session, p: MediaParams) -> set[str]:
    return {
        r.source
        for r in track_record(s, p.track_horizon_sessions, p.track_min_samples)
        if r.stance == "achat" and r.favourable
    }
