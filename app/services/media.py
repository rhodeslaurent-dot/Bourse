"""docs/23 §23.7 — media opinions (podcasts, radio, press): values cited with their stance, the
« media pressure » caution (a cited buy followed by a jump tends to reverse), and the record measured
by the tool for each source. Media opinions never trigger a proposal on their own."""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import MediaMention, NewsItem, PriceEod
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


def extract_mentions(s: Session, rows: list[NewsItem], gateway: Any, aliases: dict[str, str]) -> int:
    """Values cited by new media items (LLM extraction, then checks): the name must be written in the text
    and known to the referential (else kept without ISIN, never matched by guess). Each item is processed
    once (``classification["media"]``)."""
    n = 0
    for row in rows:
        cls = dict(row.classification or {})
        if "media" in cls:
            continue
        text = f"{row.title}\n\n{row.body or ''}"
        values: list[dict[str, Any]] = []
        if gateway is not None and getattr(gateway, "enabled", True):
            try:
                out, _ = gateway.parse(s, "media_extract", "media_extract", text[:6000], MediaExtraction)
                low = text.lower()
                for v in out.values:
                    if v.name.lower() not in low:
                        log.info("média %s : « %s » absent du texte, rejeté", row.source, v.name)
                        continue
                    values.append(v.model_dump())
            except LlmUnavailable as exc:
                log.warning("extraction média indisponible (%s) : épisode gardé sans valeurs", exc)
                continue  # retried at the next cycle
        published = _aware(row.published_at or row.fetched_at)
        for v in values:
            isin = aliases.get(v["name"].lower())
            ref = close_before(s, isin, published.date()) if isin else None
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
