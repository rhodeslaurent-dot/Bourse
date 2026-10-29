"""News ingestion (docs/06 D6, phase 1 part): dedup → instrument attachment via aliases → LLM
classification (rules fallback, reduced confidence) → ``premarket_watch`` for the next open."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data.providers.imap_gmail import ISIN_RE
from app.data.providers.rss import NewsItem as RssItem
from app.db.models import Instrument, InstrumentAlias, NewsItem, PremarketWatch
from app.domain.names import find_mentions, mention_safe, name_keys
from app.llm.gateway import ClaudeGateway, LlmUnavailable
from app.llm.rules import classify_by_rules, classify_regulatory
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


US_MICS = frozenset({"XNYS", "XNAS", "ARCX", "BATS", "XASE", "OTC", "OTCM"})


def _names(s: Session) -> list[tuple[tuple[int, int, int], str, str]]:
    """(priority, name, ISIN) for the referential names and the providers' names (never their tickers: an
    upper-case code without a space is a ticker). Priority: universe first, then European venues, then shorter."""
    out: list[tuple[tuple[int, int, int], str, str]] = []
    insts = {i.isin: i for i in s.scalars(select(Instrument).where(Instrument.active.is_(True)))}

    def prio(isin: str, name: str) -> tuple[int, int, int]:
        inst = insts.get(isin)
        return (0 if inst is not None and inst.in_universe else 1, 1 if inst is not None and inst.mic in US_MICS else 0,
                len(name))  # fmt: skip

    for inst in insts.values():
        if inst.name:
            out.append((prio(inst.isin, inst.name), inst.name, inst.isin))
    for a in s.scalars(select(InstrumentAlias).where(InstrumentAlias.provider == "eodhd")):
        if a.isin in insts and (" " in a.alias or any(c.islower() for c in a.alias)):
            out.append((prio(a.isin, a.alias), a.alias, a.isin))
    return sorted(out)


def name_index(s: Session) -> dict[str, str]:
    """Normalized name (full and core, docs: app/domain/names.py) → ISIN, to resolve a company name given by a
    reader (LLM). A key shared by two values goes to the best placed one (universe, European venue)."""
    idx: dict[str, str] = {}
    for _, name, isin in _names(s):
        for k in name_keys(name):
            idx.setdefault(k, isin)
    return idx


def alias_index(s: Session) -> dict[str, str]:
    """Normalized name → ISIN of the names that may be searched as words in free text (letters, news, forums):
    several words, or one word of 4+ letters that is not a common word; never a ticker (« sur », « aux », « pas »
    are French words before being tickers — report of 29/10/2026)."""
    return {k: v for k, v in name_index(s).items() if mention_safe(k)}


def attach_instruments(text: str, aliases: dict[str, str]) -> list[str]:
    found: list[str] = []
    for isin in ISIN_RE.findall(text):
        if isin not in found:
            found.append(isin)
    for isin, _ in find_mentions(text, aliases):
        if isin not in found:
            found.append(isin)
    return found


def classify_item(
    s: Session, item: NewsItem, gateway: ClaudeGateway | None
) -> tuple[NewsClassification, str, str | None, float]:
    text = f"Titre : {item.title}\n\nTexte : {(item.body or '')[:4000]}"
    official = classify_regulatory(item.title, item.body or "")  # docs/23 §23.3: fixed wordings, no LLM
    if official is not None:
        return official, "rules", None, 0.0
    if gateway is not None:
        try:
            parsed, usage = gateway.parse(s, "news_classify", "news_classify", text, NewsClassification)
            version, _ = gateway.settings.prompts_dir, None  # noqa: F841
            from app.llm.gateway import load_prompt

            return parsed, "llm", load_prompt(gateway.settings.prompts_dir, "news_classify")[0], usage.cost_usd
        except LlmUnavailable as exc:
            log.warning("LLM indisponible (%s) : classification par règles", exc)
    return classify_by_rules(item.title, item.body or ""), "rules", None, 0.0


def configured_scope(config: Any) -> str:
    llm = getattr(getattr(config, "params", None), "llm", None) if config is not None else None
    return str(getattr(llm, "news_classify_scope", "attached") or "attached") if llm else "attached"


def llm_worth(row: NewsItem, scope: str) -> bool:
    """docs/25 L1: Claude classifies an item only when its classification is used — a value of the
    referential is named (catalyst, fiche, thesis) or it comes from the press review (macro context needs
    the scope and themes). The rest is classified by rules, for free."""
    if scope == "all":
        return True
    if scope == "none":
        return False
    return bool(row.isins) or row.source == "presse"


def ingest_rss_items(
    s: Session,
    items: list[RssItem],
    gateway: ClaudeGateway | None,
    max_items: int = 50,
    scope: str = "attached",
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
        cls, by, version, cost = classify_item(s, row, gateway if llm_worth(row, scope) else None)
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
