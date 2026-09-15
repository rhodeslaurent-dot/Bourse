"""Newsletter ingestion (docs/06 D5): values extracted (rules, then LLM refinement), receipt time,
``p_open`` (official open), ``p_recv`` (quote at receipt, with its market timestamp and status),
``gap_open`` and ``drift_since_open``; each value becomes an *external* signal with a WATCH
proposal, its decision snapshot and — when the letter says BUY — the D5 drift gate."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime  # noqa: F401

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import LoadedConfig
from app.data.dto import Quote
from app.data.providers.imap_gmail import NewsletterMail, extract_values
from app.db.models import NewsletterItem, NewsletterValue, PriceEod, WatchlistRow
from app.domain.indicators import gap_open as gap_fn
from app.llm.gateway import ClaudeGateway, LlmUnavailable
from app.llm.schemas import NewsletterExtraction
from app.services.decisions import create_proposal, record_signal
from app.services.news import alias_index

log = logging.getLogger("bourse.newsletters")


@dataclass
class NewsletterReport:
    received: int = 0
    new: int = 0
    values: int = 0
    signals: int = 0
    parsed_by_llm: int = 0


def _eod(s: Session, isin: str, day) -> PriceEod | None:  # type: ignore[no-untyped-def]
    return s.scalar(select(PriceEod).where(PriceEod.isin == isin, PriceEod.day == day))


def _prev_close(s: Session, isin: str, day) -> float | None:  # type: ignore[no-untyped-def]
    row = s.scalar(select(PriceEod).where(PriceEod.isin == isin, PriceEod.day < day).order_by(PriceEod.day.desc()))
    return row.close if row else None


def _number_in_text(value: float, text: str) -> bool:
    """A level is accepted only if it appears literally in the letter (rule 3: the LLM never
    produces a price). Accepts 60 / 60,00 / 60.0 / 52,5 / 52.50 forms."""
    import re

    forms = {f"{value:g}", f"{value:.1f}", f"{value:.2f}"}
    forms |= {f.replace(".", ",") for f in list(forms)}
    return any(re.search(rf"(?<![\d,.]){re.escape(f)}(?![\d])", text) for f in forms)


def session_day_for(received_at: datetime, calendars=None, mic: str = "XPAR"):  # type: ignore[no-untyped-def]
    """Trading day of the letter: Paris local date, moved to the next open session of the venue (M15)."""
    from app.domain.timeutil import PARIS

    d = received_at.astimezone(PARIS).date()
    if calendars is None:
        return d
    venue = calendars.get(mic)
    try:
        return d if venue.is_open(d) else venue.next_session(d)
    except ValueError:
        return d


def ingest_mail(
    s: Session,
    mail: NewsletterMail,
    config: LoadedConfig,
    gateway: ClaudeGateway | None,
    quotes: dict[str, Quote] | None = None,
    calendars=None,  # type: ignore[no-untyped-def]
) -> NewsletterReport:
    rep = NewsletterReport(received=1)
    if s.scalar(select(NewsletterItem).where(NewsletterItem.dedup_hash == mail.dedup_hash)) is not None:
        return rep
    item = NewsletterItem(
        source=mail.source,
        message_id=mail.message_id,
        dedup_hash=mail.dedup_hash,
        sent_at=mail.sent_at,
        received_at=mail.received_at,
        subject=mail.subject,
        body_text=mail.body_text,
    )
    s.add(item)
    s.flush()
    rep.new = 1
    aliases = alias_index(s)
    values = extract_values(mail.body_text, aliases)
    rejected_levels: list[str] = []
    parsed_by = "rules"
    version = None
    if gateway is not None:
        try:
            ext, _ = gateway.parse(
                s,
                "newsletter_extract",
                "newsletter_extract",
                mail.body_text[:6000],
                NewsletterExtraction,
                max_tokens=2048,
            )
            from app.llm.gateway import load_prompt

            version = load_prompt(gateway.settings.prompts_dir, "newsletter_extract")[0]
            merged = {v.isin or (v.name or "").lower(): v for v in values}
            for lv in ext.values:
                isin = lv.isin or aliases.get(lv.name.lower())
                key = isin or lv.name.lower()
                # M16: keep only levels written in the letter; rule-extracted levels take precedence
                verified = {k: v for k, v in lv.levels.items() if _number_in_text(v, mail.body_text)}  # noqa: F821
                rejected_levels += [f"{lv.name}:{k}={v}" for k, v in lv.levels.items() if k not in verified]
                base = merged.get(key)
                if base is None:
                    from app.data.providers.imap_gmail import ExtractedValue

                    merged[key] = ExtractedValue(isin, lv.name, lv.direction, verified, lv.snippet)
                else:
                    base.direction = lv.direction if lv.direction != "neutral" else base.direction
                    for k, v in verified.items():
                        base.levels.setdefault(k, v)
            if rejected_levels:
                log.warning("niveaux LLM absents du texte, rejetés : %s", rejected_levels)
            values = list(merged.values())
            parsed_by = "llm"
            rep.parsed_by_llm = 1
        except LlmUnavailable as exc:
            log.warning("LLM indisponible (%s) : extraction par règles", exc)
    item.parsed = {
        "values": [{"isin": v.isin, "name": v.name, "direction": v.direction, "levels": v.levels} for v in values],
        "rejected_llm_levels": rejected_levels,
    }
    item.parsed_by, item.llm_prompt_version = parsed_by, version
    day = session_day_for(mail.received_at, calendars)  # noqa: F821
    max_drift = float(
        ((config.params.detectors or {}).get("d5_external") or {}).get("drift_since_open_max_for_buy", 0.02)
    )
    for v in values:
        row = NewsletterValue(item_id=item.id, isin=v.isin, name=v.name, direction=v.direction, levels=v.levels or None)
        s.add(row)
        rep.values += 1
        if not v.isin:
            continue
        eod = _eod(s, v.isin, day)
        prev = _prev_close(s, v.isin, day)
        q = (quotes or {}).get(v.isin)
        row.p_open = eod.open if eod else None
        row.close_prev = prev
        if q is not None and q.last is not None:
            row.p_recv, row.p_recv_market_ts, row.p_recv_status = q.last, q.market_timestamp, q.data_status.value
        if row.p_open and prev:
            row.gap_open = gap_fn(row.p_open, prev)
        if row.p_open and row.p_recv:
            row.drift_since_open = row.p_recv / row.p_open - 1
        sig = record_signal(
            s,
            v.isin,
            "external",
            mail.source,
            mail.received_at,
            "eod",
            q.data_status.value if q else "unavailable",
            config.params.version,
            evidence={
                "direction": v.direction,
                "levels": v.levels,
                "p_open": row.p_open,
                "p_recv": row.p_recv,
                "gap_open": row.gap_open,
                "drift_since_open": row.drift_since_open,
                "received_at": mail.received_at,
            },
            external_refs=[{"source": mail.source, "message_id": mail.message_id, "subject": mail.subject}],
            entry_low=v.levels.get("achat"),
            stop_initial=v.levels.get("stop"),
            targets=[v.levels["objectif"]] if "objectif" in v.levels else None,
        )
        row.signal_id = sig.id
        gates = {"passed": [], "blocked": []}
        if v.direction == "buy" and row.drift_since_open is not None and row.drift_since_open >= max_drift:
            gates["blocked"].append(
                f"BLOQUÉ : drift depuis l'ouverture {row.drift_since_open:+.1%} ≥ {max_drift:.0%} (detectors.d5_external.drift_since_open_max_for_buy) — aucun BUY sur signal externe seul"  # noqa: E501
            )
        action = "WATCH"  # phase 1: external signals feed the watchlist; BUY needs a D1–D4 setup (phase 2)
        create_proposal(
            s,
            sig,
            action,
            {v.isin: q.model_dump(mode="json")} if q else None,
            gates,
            config.params.version,
            to_verify=q is None or q.data_status.value != "realtime",
            entry_zone={"achat": v.levels.get("achat")} if v.levels.get("achat") else None,
            stop=v.levels.get("stop"),
            targets=[v.levels["objectif"]] if "objectif" in v.levels else None,
        )
        rep.signals += 1
        if (
            v.direction in ("buy", "watch")
            and s.scalar(select(WatchlistRow).where(WatchlistRow.isin == v.isin, WatchlistRow.active.is_(True))) is None
        ):
            s.add(
                WatchlistRow(isin=v.isin, reason="external", source=mail.source, active=True, levels=v.levels or None)
            )
    s.flush()
    return rep
