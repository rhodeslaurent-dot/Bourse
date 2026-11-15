"""Lot S2 (step 1 of the user's plan, 15/11/2026) — one journal of market events built from every source.

Each stored item about a value — a letter's recommendation, a manager's pick on a podcast, a classified news item, a
manager's dealing, an official filing, a change of the analysts' average target, an earnings surprise, a radar label,
a burst on the forums, a short position, a detector's signal, a US filing or a FINRA short-volume spike — becomes one
``market_events`` row in the same shape: when it became public (``event_at``), what it says (``kind``, ``direction``
+1 / −1 / 0 or none, ``strength``), where it comes from (``ref_table``, ``ref_id``) and the context of that day (the
venue's regime, the VIX, a major macro announcement within a day). Idempotent: a row is never written twice.

Nothing here is an opinion of the tool: the direction is the source's own (a « buy » letter, a positive news item
as classified, a purchase by a manager), or none. The measure of what followed is lot S3 (``event_measure``).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.timeutil import PARIS, from_store

LETTER_DIRECTION = {"buy": 1, "sell": -1, "watch": 0, "neutral": 0}
MEDIA_DIRECTION = {"achat": 1, "vente": -1}
NEWS_DIRECTION = {"positif": 1, "negatif": -1, "neutre": 0}
NEWS_STRENGTH = {"forte": 3.0, "moyenne": 2.0, "faible": 1.0}
INSIDER_DIRECTION = {"acquisition": 1, "cession": -1}


@dataclass
class EventDraft:
    source_key: str
    family: str
    kind: str
    isin: str
    event_at: datetime
    ref_table: str
    ref_id: int
    direction: int | None = None
    strength: float | None = None
    title: str | None = None


@dataclass
class JournalReport:
    added: dict[str, int] = field(default_factory=dict)
    seen: int = 0

    def line(self) -> str:
        total = sum(self.added.values())
        parts = ", ".join(f"{k} {v}" for k, v in sorted(self.added.items()) if v)
        return f"{total} événement(s) ajouté(s)" + (f" ({parts})" if parts else "") + f" sur {self.seen} lus"


def _utc(v: datetime | None) -> datetime | None:
    return from_store(v) if v is not None else None


def _evening(d: date) -> datetime:
    """A day-level fact (a radar label, a consensus read in the evening): public at 22:00 Paris that day."""
    return datetime.combine(d, time(22, 0), tzinfo=PARIS).astimezone(UTC)


def _sign(x: float | None) -> int | None:
    if x is None:
        return None
    return 1 if x > 0 else -1 if x < 0 else 0


def consensus_min_change(config: Any) -> float:
    """``event_journal.consensus_min_change``: a smaller change of the analysts' average target is not an event."""
    g = (getattr(config, "raw", None) or {}).get("event_journal") or {}
    return float(g.get("consensus_min_change", 0.02))


def short_volume_z(config: Any) -> float:
    """``event_journal.short_volume_z``: deviations above its 20-day base for a FINRA short-volume share event."""
    g = (getattr(config, "raw", None) or {}).get("event_journal") or {}
    return float(g.get("short_volume_z", 3.0))


# --- one generator per source ---------------------------------------------------------------------------------------


def _letters(s: Session, since: datetime) -> Iterator[EventDraft]:
    from app.db.models import NewsletterItem, NewsletterValue

    q = (
        select(NewsletterValue, NewsletterItem)
        .join(NewsletterItem, NewsletterItem.id == NewsletterValue.item_id)
        .where(NewsletterItem.received_at >= since, NewsletterValue.isin.is_not(None))
    )
    for v, it in s.execute(q):
        d = LETTER_DIRECTION.get(v.direction)
        at = _utc(it.sent_at) or _utc(it.received_at)
        word = {1: "achat", -1: "vente", 0: "surveillance"}.get(d if d is not None else 9, "autre")
        yield EventDraft(f"lettre:{it.source}", "momentum", f"lettre_{word}", v.isin, at,  # type: ignore[arg-type]
                         "newsletter_values", v.id, d, None, (it.subject or "")[:300])  # fmt: skip


def _media(s: Session, since: datetime) -> Iterator[EventDraft]:
    from app.db.models import MediaMention

    for m in s.scalars(select(MediaMention).where(MediaMention.received_at >= since, MediaMention.isin.is_not(None))):
        d = MEDIA_DIRECTION.get(m.stance)
        yield EventDraft(f"media:{m.source}", "momentum", f"media_{m.stance or 'citation'}", m.isin,  # type: ignore[arg-type]
                         _utc(m.published_at), "media_mentions", m.id, d if d is not None else 0, None,  # type: ignore[arg-type]
                         (m.snippet or m.name or "")[:300])  # fmt: skip


def _news(s: Session, since: datetime) -> Iterator[EventDraft]:
    from app.db.models import NewsItem

    for n in s.scalars(select(NewsItem).where(NewsItem.fetched_at >= since)):
        if not n.isins:
            continue
        c = n.classification or {}
        typ = str(c.get("type") or "non_classee")
        d = NEWS_DIRECTION.get(str(c.get("direction"))) if c else None
        at = _utc(n.published_at) or _utc(n.fetched_at)
        for isin in n.isins:
            yield EventDraft(f"nouvelle:{n.source}", "actualites", f"nouvelle_{typ}", str(isin), at,  # type: ignore[arg-type]
                             "news_items", n.id, d, NEWS_STRENGTH.get(str(c.get("magnitude"))),
                             (n.title or "")[:300])  # fmt: skip


def _insiders(s: Session, since: datetime) -> Iterator[EventDraft]:
    from app.db.models import InsiderDeclaration

    q = select(InsiderDeclaration).where(InsiderDeclaration.received_at >= since, InsiderDeclaration.isin.is_not(None))
    for r in s.scalars(q):
        d = INSIDER_DIRECTION.get(r.nature or "")
        yield EventDraft(f"dirigeant:{r.source}", "momentum", f"dirigeant_{r.nature or 'autre'}", r.isin,  # type: ignore[arg-type]
                         _utc(r.market_timestamp), "insider_declarations", r.id, d, r.amount,  # type: ignore[arg-type]
                         (r.issuer_name or "")[:300])  # fmt: skip


def _filings(s: Session, since: datetime) -> Iterator[EventDraft]:
    from app.db.models import OfficialFiling

    q = select(OfficialFiling).where(OfficialFiling.received_at >= since, OfficialFiling.isin.is_not(None))
    for r in s.scalars(q):
        at = _utc(r.market_timestamp) or _utc(r.amf_timestamp) or _utc(r.received_at)
        yield EventDraft(f"communique:{r.source}", "officiel", f"communique_{r.family}", r.isin,  # type: ignore[arg-type]
                         at, "official_filings", r.id, None, None, (r.title or r.subtype_label or "")[:300])  # type: ignore[arg-type]  # fmt: skip


def _consensus(s: Session, since: datetime, min_change: float) -> Iterator[EventDraft]:
    """A change of the analysts' average target between two readings of the same source (the « note » is shown as
    published and never interpreted, docs/23 S4)."""
    from app.db.models import AnalystConsensus

    rows = list(s.scalars(select(AnalystConsensus).where(AnalystConsensus.day >= since.astimezone(PARIS).date())))
    for r in rows:
        prev = s.scalar(
            select(AnalystConsensus)
            .where(AnalystConsensus.isin == r.isin, AnalystConsensus.source == r.source, AnalystConsensus.day < r.day)
            .order_by(AnalystConsensus.day.desc())
        )
        a = (prev.data or {}).get("objectif_moyen") if prev is not None else None
        b = (r.data or {}).get("objectif_moyen")
        if not isinstance(a, int | float) or not isinstance(b, int | float) or a <= 0:
            continue
        ch = b / a - 1
        if abs(ch) < min_change:
            continue
        kind = "objectif_hausse" if ch > 0 else "objectif_baisse"
        yield EventDraft(f"consensus:{r.source}", "momentum", kind, r.isin,
                         _evening(r.day), "analyst_consensus", r.id, _sign(ch), round(ch, 4),
                         f"objectif moyen {a:.4g} → {b:.4g}")  # fmt: skip


def _earnings(s: Session, since: datetime) -> Iterator[EventDraft]:
    from app.db.models import EarningsEvent

    q = select(EarningsEvent).where(EarningsEvent.day >= since.astimezone(PARIS).date(),
                                    EarningsEvent.surprise_pct.is_not(None))  # fmt: skip
    for r in s.scalars(q):
        at = _utc(r.ts) or _evening(r.day)
        yield EventDraft(f"resultat:{r.source}", "momentum", "resultat_surprise", r.isin, at, "earnings_events", r.id,
                         _sign(r.surprise_pct), r.surprise_pct,
                         f"BPA publié {r.eps_actual} contre {r.eps_estimate} attendu")  # fmt: skip


def _radar(s: Session, since: datetime) -> Iterator[EventDraft]:
    from app.db.models import WeakSignalHit

    for r in s.scalars(select(WeakSignalHit).where(WeakSignalHit.day >= since.astimezone(PARIS).date())):
        yield EventDraft("radar", "outil", f"radar_{r.label}", r.isin, _utc(r.computed_at) or _evening(r.day),
                         "weak_signal_hits", r.id, None, r.attention_z, (r.text or "")[:300])  # fmt: skip


def _forums(s: Session, since: datetime) -> Iterator[EventDraft]:
    from app.db.models import SocialContent

    for r in s.scalars(select(SocialContent).where(SocialContent.computed_at >= since, SocialContent.status == "ok")):
        yield EventDraft(f"forum:{r.source}", "momentum", f"forum_{r.effect or r.reason}", r.isin,
                         _utc(r.computed_at), "social_content", r.id, None, None, (r.note or "")[:300])  # type: ignore[arg-type]  # fmt: skip


def _shorts(s: Session, since: datetime) -> Iterator[EventDraft]:
    """A published net short position that appears, grows or shrinks (EU 236/2012). Public the business day after
    the position date: dated 18:00 Paris that day, never before."""
    from app.db.models import ShortPosition

    for r in s.scalars(select(ShortPosition).where(ShortPosition.received_at >= since)):
        prev = s.scalar(
            select(ShortPosition)
            .where(ShortPosition.authority == r.authority, ShortPosition.isin == r.isin,
                   ShortPosition.holder == r.holder, ShortPosition.position_date < r.position_date)
            .order_by(ShortPosition.position_date.desc())
        )  # fmt: skip
        if prev is not None and abs(r.pct - prev.pct) < 1e-9:
            continue
        kind = "vad_nouvelle" if prev is None else "vad_hausse" if r.pct > prev.pct else "vad_baisse"
        d = 1 if kind == "vad_baisse" else -1
        nxt = r.position_date + timedelta(days=1)
        while nxt.weekday() >= 5:
            nxt += timedelta(days=1)
        at = datetime.combine(nxt, time(18, 0), tzinfo=PARIS).astimezone(UTC)
        yield EventDraft(f"vad:{r.authority}", "officiel", kind, r.isin, at, "short_positions", r.id, d, r.pct,
                         f"position courte {r.pct:.2%}")  # fmt: skip


def _short_volume(s: Session, since: datetime, z_min: float = 3.0, base: int = 20) -> Iterator[EventDraft]:
    """S5 — a day whose FINRA short-volume share is ``z_min`` deviations above its own 20-day base (US values)."""
    from statistics import mean, pstdev

    from app.db.models import ShortVolumeDaily

    d0 = since.astimezone(PARIS).date()
    for r in s.scalars(select(ShortVolumeDaily).where(ShortVolumeDaily.day >= d0)):
        if not r.total_volume:
            continue
        hist = list(s.scalars(select(ShortVolumeDaily).where(ShortVolumeDaily.isin == r.isin,
                                                             ShortVolumeDaily.day < r.day)
                              .order_by(ShortVolumeDaily.day.desc()).limit(base)))  # fmt: skip
        ratios = [h.short_volume / h.total_volume for h in hist if h.total_volume]
        if len(ratios) < base // 2:
            continue
        sd = pstdev(ratios)
        z = (r.short_volume / r.total_volume - mean(ratios)) / sd if sd > 0 else 0.0
        if z < z_min:
            continue
        at = datetime.combine(r.day + timedelta(days=1), time(0, 0), tzinfo=PARIS).astimezone(UTC)
        yield EventDraft(f"vad_volume:{r.source}", "officiel", "vad_volume_pic", r.isin, at, "short_volume_daily",
                         r.id, -1, round(z, 2),
                         f"part vendue à découvert {r.short_volume / r.total_volume:.0%}")  # fmt: skip


def _signals(s: Session, since: datetime) -> Iterator[EventDraft]:
    from app.db.models import Signal

    for r in s.scalars(select(Signal).where(Signal.ts >= since, Signal.deleted_at.is_(None))):
        d = 1 if r.direction == "long" else -1 if r.direction == "short" else None
        yield EventDraft(f"signal:{r.source}", "outil", f"signal_{r.detector}", r.isin, _utc(r.ts), "signals",  # type: ignore[arg-type]
                         r.id, d)  # fmt: skip


# --- context and storage --------------------------------------------------------------------------------------------


class _Context:
    """The venue's regime, the VIX and a major macro announcement within one day, as known on the event's day."""

    def __init__(self, s: Session) -> None:
        self.s = s
        self.cache: dict[tuple[str | None, date], dict[str, Any]] = {}
        self.mics: dict[str, str | None] = {}

    def mic(self, isin: str) -> str | None:
        if isin not in self.mics:
            from app.db.models import Instrument

            inst = self.s.get(Instrument, isin)
            self.mics[isin] = inst.mic if inst is not None else None
        return self.mics[isin]

    def of(self, mic: str | None, day: date) -> dict[str, Any]:
        key = (mic, day)
        if key in self.cache:
            return self.cache[key]
        from app.db.models import MacroEvent, MacroPoint
        from app.services.macro import latest_regime

        reg = latest_regime(self.s, day, mic) if mic else None
        src = mic if reg is not None else "EU"
        reg = reg or latest_regime(self.s, day, "EU", 10)
        vix = self.s.scalar(select(MacroPoint).where(MacroPoint.indicator == "vix", MacroPoint.day <= day)
                            .order_by(MacroPoint.day.desc()))  # fmt: skip
        lo = datetime.combine(day - timedelta(days=1), time.min, tzinfo=PARIS).astimezone(UTC)
        hi = datetime.combine(day + timedelta(days=2), time.min, tzinfo=PARIS).astimezone(UTC)
        macro = self.s.scalar(select(MacroEvent).where(MacroEvent.ts >= lo, MacroEvent.ts < hi,
                                                       MacroEvent.impact == "High"))  # fmt: skip
        ctx = {
            "regime": reg.regime if reg is not None else None,
            "regime_de": src if reg is not None else None,
            "vix": round(float(vix.value), 2) if vix is not None and (day - vix.day).days <= 7 else None,
            "annonce_macro": (f"{macro.currency} {macro.title}"[:80]) if macro is not None else None,
        }
        self.cache[key] = ctx
        return ctx


def collect(s: Session, config: Any, since: datetime, now: datetime | None = None) -> JournalReport:
    """Every source's items received since ``since`` → ``market_events`` (rows already there are skipped)."""
    from app.db.models import MarketEvent

    now = now or datetime.now(UTC)
    rep = JournalReport()
    ctx = _Context(s)
    gens: list[tuple[str, Iterator[EventDraft]]] = [
        ("lettres", _letters(s, since)), ("medias", _media(s, since)), ("actualites", _news(s, since)),
        ("dirigeants", _insiders(s, since)), ("communiques", _filings(s, since)),
        ("consensus", _consensus(s, since, consensus_min_change(config))), ("resultats", _earnings(s, since)),
        ("radar", _radar(s, since)), ("forums", _forums(s, since)), ("vad", _shorts(s, since)),
        ("vad_volume", _short_volume(s, since, short_volume_z(config))), ("signaux", _signals(s, since)),
    ]  # fmt: skip
    for name, gen in gens:
        with s.begin_nested():  # one source that fails never stops the others
            drafts = [d for d in gen if d.isin and d.event_at is not None]
            rep.seen += len(drafts)
            if not drafts:
                continue
            have: set[tuple[str, int, str, str]] = set()
            tables = {d.ref_table for d in drafts}
            ids = sorted({d.ref_id for d in drafts})
            for k in range(0, len(ids), 900):  # bounded IN lists (a backfill reads tens of thousands of items)
                have |= {
                    (r.ref_table, r.ref_id, r.kind, r.isin)
                    for r in s.execute(
                        select(MarketEvent.ref_table, MarketEvent.ref_id, MarketEvent.kind, MarketEvent.isin).where(
                            MarketEvent.ref_table.in_(tables), MarketEvent.ref_id.in_(ids[k : k + 900])
                        )
                    )
                }
            n = 0
            for d in drafts:
                key = (d.ref_table, d.ref_id, d.kind, d.isin)
                if key in have:
                    continue
                have.add(key)
                mic = ctx.mic(d.isin)
                s.add(MarketEvent(source_key=d.source_key[:48], family=d.family, kind=d.kind[:48], isin=d.isin, mic=mic,
                                  event_at=d.event_at, direction=d.direction, strength=d.strength,
                                  title=d.title or None, ref_table=d.ref_table, ref_id=d.ref_id,
                                  context=ctx.of(mic, d.event_at.astimezone(PARIS).date()),
                                  created_at=now))  # fmt: skip
                n += 1
            s.flush()
            rep.added[name] = rep.added.get(name, 0) + n
    return rep
