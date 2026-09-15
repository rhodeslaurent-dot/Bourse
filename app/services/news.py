"""News ingestion (docs/06 D6, phase 1 part): dedup → instrument attachment via aliases → LLM
classification (rules fallback, reduced confidence) → ``premarket_watch`` for the next open."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data.providers.imap_gmail import ISIN_RE
from app.data.providers.rss import NewsItem as RssItem
from app.db.models import Instrument, InstrumentAlias, NewsItem, PremarketWatch
from app.llm.gateway import ClaudeGateway, LlmUnavailable
from app.llm.rules import classify_by_rules
from app.llm.schemas import NewsClassification

log = logging.getLogger("bourse.news")


@dataclass
class IngestReport:
    received: int = 0
    new: int = 0
    classified_llm: int = 0
    classified_rules: int = 0
    attached: int = 0
    llm_cost_usd: float = 0.0


def alias_index(s: Session) -> dict[str, str]:
    """lowercase alias → ISIN (names and tickers from the referential + aliases table)."""
    idx: dict[str, str] = {}
    for inst in s.scalars(select(Instrument).where(Instrument.active.is_(True))):
        if inst.name and len(inst.name) >= 3:
            idx[inst.name.lower()] = inst.isin
    for a in s.scalars(select(InstrumentAlias)):
        if len(a.alias) >= 3 and not a.alias.lower().endswith((".pa", ".xetra", ".as", ".br")):
            idx[a.alias.lower()] = a.isin
    return idx


def attach_instruments(text: str, aliases: dict[str, str]) -> list[str]:
    found: list[str] = []
    for isin in ISIN_RE.findall(text):
        if isin not in found:
            found.append(isin)
    low = text.lower()
    for alias, isin in aliases.items():
        if isin in found:
            continue
        if re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", low):
            found.append(isin)
    return found


def classify_item(
    s: Session, item: NewsItem, gateway: ClaudeGateway | None
) -> tuple[NewsClassification, str, str | None, float]:
    text = f"Titre : {item.title}\n\nTexte : {(item.body or '')[:4000]}"
    if gateway is not None:
        try:
            parsed, usage = gateway.parse(s, "news_classify", "news_classify", text, NewsClassification)
            version, _ = gateway.settings.prompts_dir, None  # noqa: F841
            from app.llm.gateway import load_prompt

            return parsed, "llm", load_prompt(gateway.settings.prompts_dir, "news_classify")[0], usage.cost_usd
        except LlmUnavailable as exc:
            log.warning("LLM indisponible (%s) : classification par règles", exc)
    return classify_by_rules(item.title, item.body or ""), "rules", None, 0.0


def ingest_rss_items(
    s: Session, items: list[RssItem], gateway: ClaudeGateway | None, max_items: int = 50
) -> IngestReport:
    rep = IngestReport(received=len(items))
    aliases = alias_index(s)
    now = datetime.now(UTC)
    for it in items[:max_items]:
        if s.scalar(select(NewsItem).where(NewsItem.dedup_hash == it.dedup_hash)) is not None:
            continue
        row = NewsItem(
            source=it.source,
            guid=it.guid,
            dedup_hash=it.dedup_hash,
            published_at=it.published_at,
            fetched_at=it.fetched_at,
            title=it.title,
            url=it.url,
            body=it.body,
        )
        row.isins = attach_instruments(f"{it.title} {it.body}", aliases)
        s.add(row)
        s.flush()
        rep.new += 1
        rep.attached += bool(row.isins)
        cls, by, version, cost = classify_item(s, row, gateway)
        row.classification = {
            **cls.model_dump(),
            "surprise": None,
        }  # surprise only with a dated consensus (docs/06 D6), never from the LLM
        row.classified_by, row.llm_prompt_version, row.llm_cost_usd, row.classified_at = by, version, cost, now
        rep.llm_cost_usd += cost
        if by == "llm":
            rep.classified_llm += 1
        else:
            rep.classified_rules += 1
        if row.isins and cls.direction != "neutre" and cls.magnitude in ("forte", "moyenne"):
            _add_premarket(s, row, cls)
    s.flush()
    return rep


def _add_premarket(s: Session, row: NewsItem, cls: NewsClassification) -> None:
    from app.domain.timeutil import PARIS

    ref = (row.published_at or row.fetched_at).astimezone(PARIS)
    day = ref.date()
    if ref.hour >= 17 and ref.minute >= 40 or ref.hour >= 18:
        from datetime import timedelta

        day = day + timedelta(days=1)  # evening releases feed the next morning's list
    for isin in row.isins or []:
        if (
            s.scalar(
                select(PremarketWatch).where(
                    PremarketWatch.day == day, PremarketWatch.isin == isin, PremarketWatch.source == row.source
                )
            )
            is None
        ):
            s.add(
                PremarketWatch(
                    day=day,
                    isin=isin,
                    news_id=row.id,
                    expected_direction=cls.direction,
                    magnitude=cls.magnitude,
                    source=row.source,
                )
            )


def negative_catalysts_on_positions(s: Session, since: datetime) -> list[NewsItem]:
    """docs/06 S4 input: strong negative catalysts on held instruments (alert wired by the monitor)."""
    from app.db.models import PositionRow

    held = {
        r.isin for r in s.scalars(select(PositionRow).where(PositionRow.closed_at.is_(None), PositionRow.qty_held > 0))
    }
    out = []
    for n in s.scalars(select(NewsItem).where(NewsItem.classified_at >= since)):
        c = n.classification or {}
        if c.get("direction") == "negatif" and c.get("magnitude") == "forte" and set(n.isins or []) & held:
            out.append(n)
    return out
