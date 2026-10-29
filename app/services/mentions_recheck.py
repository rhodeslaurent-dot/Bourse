"""Recheck of the values read in letters and news since a date with the name matching of 29/10/2026
(app/domain/names.py): a value whose company name (or ISIN) is not written in the text was a ticker read in a
French word (« sur » → Surteco…). Its open proposals are withdrawn (soft: ``deleted_at``, snapshots kept, rule
10) and the watchlist line it created is deactivated; the news items get their values attached again. Dry run by
default."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Instrument, InstrumentAlias, NewsItem, NewsletterItem, NewsletterValue, Proposal, WatchlistRow
from app.domain.names import name_keys, norm
from app.domain.timeutil import PARIS


@dataclass
class RecheckReport:
    spurious: list[tuple[str, str, str]] = field(default_factory=list)  # (day, source, isin · name)
    proposals: list[int] = field(default_factory=list)
    watchlist: list[str] = field(default_factory=list)
    news_changed: int = 0
    resolved: list[str] = field(default_factory=list)  # « name → ISIN » now recognised

    def lines(self) -> list[str]:
        out = [f"{len(self.spurious)} valeur(s) lue(s) dans un mot et non dans un nom de société :"]
        out += [f"  {d} · {src} · {what}" for d, src, what in self.spurious[:40]]
        out.append(f"propositions retirées : {', '.join(f'n° {i}' for i in self.proposals) or 'aucune'}")
        out.append(f"lignes de watchlist désactivées : {', '.join(self.watchlist) or 'aucune'}")
        out.append(f"nouvelles dont les valeurs attachées changent : {self.news_changed}")
        out.append(f"valeurs désormais reconnues ({len(self.resolved)}) : {', '.join(self.resolved[:40]) or 'aucune'}")
        return out


def _keys_by_isin(s: Session, isins: set[str]) -> dict[str, set[str]]:
    keys: dict[str, set[str]] = {i: set() for i in isins}
    for inst in s.scalars(select(Instrument).where(Instrument.isin.in_(isins or {""}))):
        keys[inst.isin] |= name_keys(inst.name or "")
    for a in s.scalars(select(InstrumentAlias).where(InstrumentAlias.isin.in_(isins or {""}))):
        if " " in a.alias or any(c.islower() for c in a.alias):  # provider names, never tickers
            keys[a.isin] |= name_keys(a.alias)
    return keys


def written(text: str, isin: str, keys: set[str]) -> bool:
    t = f" {norm(text)} "
    return isin.lower() in t or any(f" {k} " in t for k in keys if k)


def recheck(s: Session, since: date, apply: bool, now: datetime) -> RecheckReport:
    from app.services.news import alias_index, attach_instruments

    rep = RecheckReport()
    start = datetime.combine(since, datetime.min.time(), tzinfo=PARIS)
    items = {i.id: i for i in s.scalars(select(NewsletterItem).where(NewsletterItem.received_at >= start))}
    values = [
        v for v in s.scalars(select(NewsletterValue).where(NewsletterValue.item_id.in_(list(items) or [-1]))) if v.isin
    ]
    keys = _keys_by_isin(s, {v.isin for v in values if v.isin})
    kept: set[str] = set()
    bad: list[NewsletterValue] = []
    for v in values:
        it = items[v.item_id]
        if written(f"{it.subject}\n{it.body_text}", v.isin or "", keys.get(v.isin or "", set())):
            kept.add(v.isin or "")
        else:
            bad.append(v)
            day = f"{it.received_at.astimezone(PARIS):%d/%m}"
            rep.spurious.append((day, it.source, f"{v.isin} · lu dans « {v.name} »"))
    for v in bad:
        if v.signal_id is not None:
            for p in s.scalars(
                select(Proposal).where(Proposal.signal_id == v.signal_id, Proposal.deleted_at.is_(None))
            ):
                rep.proposals.append(p.id)
                if apply:
                    p.deleted_at = now
        if v.isin in kept:
            continue  # the same value is really cited elsewhere: its watchlist line stays
        for w in s.scalars(
            select(WatchlistRow).where(
                WatchlistRow.isin == v.isin,
                WatchlistRow.active.is_(True),
                WatchlistRow.reason == "external",
                WatchlistRow.source == items[v.item_id].source,
            )
        ):
            if w.isin not in rep.watchlist:
                rep.watchlist.append(w.isin)
            if apply:
                w.active = False
    # names given by the reader that the former lookup missed (« Veolia », « Atos Group »…): ISIN set now
    from app.db.models import LetterOpinion
    from app.domain.names import resolve
    from app.services.news import name_index

    names = name_index(s)
    unresolved = list(
        s.scalars(
            select(NewsletterValue).where(
                NewsletterValue.item_id.in_(list(items) or [-1]), NewsletterValue.isin.is_(None)
            )
        )
    )
    unresolved += list(s.scalars(select(LetterOpinion).where(LetterOpinion.day >= since, LetterOpinion.isin.is_(None))))
    for row in unresolved:
        isin = resolve(row.name or "", names)
        if isin is None:
            continue
        label = f"{row.name} → {isin}"
        if label not in rep.resolved:
            rep.resolved.append(label)
        if apply:
            row.isin = isin
    aliases = alias_index(s)
    for n in s.scalars(select(NewsItem).where(NewsItem.fetched_at >= start)):
        new = attach_instruments(f"{n.title} {n.body}", aliases)
        if sorted(new) != sorted(n.isins or []):
            rep.news_changed += 1
            if apply:
                n.isins = new
    return rep
