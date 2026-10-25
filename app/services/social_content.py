"""docs/30 S4a — reads the recent messages of a value's forum threads, has each one labelled by the light model
(closed lists), and stores the aggregates and up to three neutral summaries. Option A (user decision of
25/10/2026): no message text, no author, no pseudonym is ever stored; authors are counted with a salted hash
that changes at each read. Triggered by the radar, once a day for open positions, or on request."""

from __future__ import annotations

import logging
import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import LoadedConfig
from app.data.providers import social
from app.db.models import Instrument, LlmCall, SocialContent
from app.domain.social_content import UNCLASSIFIED, ContentParams, Message, aggregate, effect
from app.domain.timeutil import PARIS
from app.services.social import forum_url, social_params

log = logging.getLogger("bourse.social_content")
PURPOSE = "forum_content"


def content_params(config: LoadedConfig) -> dict[str, Any]:
    return dict(((config.raw.get("weak_signals") or {}).get("content")) or {})


def domain_params(c: dict[str, Any]) -> ContentParams:
    d = ContentParams()
    return ContentParams(
        int(c.get("min_distinct_authors", d.min_distinct_authors)),
        float(c.get("author_concentration_max", d.author_concentration_max)),
        float(c.get("promotion_share_max", d.promotion_share_max)),
        int(c.get("pump_min_messages", d.pump_min_messages)),
        int(c.get("summaries_max", d.summaries_max)),
    )


def selectors(config: LoadedConfig) -> dict[str, str]:
    bp = social_params(config).get("boursorama") or {}
    return {
        "message": str(bp.get("message_class") or ""),
        "author": str(bp.get("author_class") or ""),
        "text": str(bp.get("text_class") or ""),
        "date": str(bp.get("message_date_class") or ""),
    }


def _day_start(now: datetime) -> datetime:
    return now.astimezone(PARIS).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


def spent_today(s: Session, now: datetime) -> float:
    return float(
        s.scalar(
            select(func.coalesce(func.sum(LlmCall.cost_usd), 0.0)).where(
                LlmCall.purpose == PURPOSE, LlmCall.ts >= _day_start(now), LlmCall.cached.is_(False)
            )
        )
        or 0.0
    )


def values_today(s: Session, now: datetime) -> int:
    return int(
        s.scalar(
            select(func.count(func.distinct(SocialContent.isin))).where(
                SocialContent.computed_at >= _day_start(now), SocialContent.status != "structure"
            )
        )
        or 0
    )


def scrub_numbers(summary: str, text: str) -> str:
    """Rule 3 guard: a number of the summary that is not in the message is removed (never shown)."""
    from app.services.assistant import unsupported_numbers

    for tok in unsupported_numbers(summary, {"message": text}):
        summary = summary.replace(tok, "…")
    return summary


def classify(
    s: Session, gateway: Any, config: LoadedConfig, name: str, texts: list[str]
) -> tuple[list[dict[str, Any] | None], float, str | None]:
    """Labels of each text (None when not labelled), cost, and the reason when the model was not used."""
    from app.llm.gateway import LlmUnavailable
    from app.llm.schemas import ForumBatchLabels

    c = content_params(config)
    batch = int(c.get("batch_size", 20))
    cap = float(c.get("max_usd_per_day", 0.30))
    model = c.get("model") or None
    out: list[dict[str, Any] | None] = [None] * len(texts)
    cost, why = 0.0, None
    if gateway is None:
        return out, 0.0, "LLM non configuré"
    for k in range(0, len(texts), batch):
        if spent_today(s, datetime.now(UTC)) >= cap:
            why = f"plafond du contenu atteint ({cap:.2f} $ par jour)"
            break
        chunk = texts[k : k + batch]
        user = f"Société : {name}\n\n" + "\n\n".join(f"[{j}] {t}" for j, t in enumerate(chunk))
        try:
            res, usage = gateway.parse(
                s, PURPOSE, "forum_classify", user, ForumBatchLabels, model=model, max_tokens=3000
            )
        except LlmUnavailable as exc:
            why = str(exc)[:200]
            break
        cost += usage.cost_usd
        for lab in res.messages:
            if 0 <= lab.i < len(chunk):
                out[k + lab.i] = lab.model_dump()
    return out, cost, why


def analyse(
    s: Session,
    config: LoadedConfig,
    client: httpx.Client,
    inst: Instrument,
    reason: str,
    gateway: Any = None,
    now: datetime | None = None,
) -> SocialContent | None:
    """One read of the value's recent threads. None when the feature is off or the day's caps are reached;
    otherwise a stored row whose ``status`` says what could be done (``structure`` = selectors not set yet:
    nothing is guessed)."""
    c = content_params(config)
    if not c.get("enabled"):
        return None
    now = now or datetime.now(UTC)
    if values_today(s, now) >= int(c.get("max_values_per_day", 30)):
        log.info("contenu : %d valeurs déjà lues aujourd'hui (plafond)", values_today(s, now))
        return None
    bp = social_params(config).get("boursorama") or {}
    window_h = float(c.get("window_hours", 24))
    row = SocialContent(
        isin=inst.isin, computed_at=now, source="forum_boursorama", reason=reason, window_hours=window_h, cost_usd=0.0
    )
    sel = selectors(config)
    url = forum_url(inst, bp.get("url_template_by_mic") or {})
    if url is None or not bp.get("thread_link_regex") or not sel["message"] or not sel["text"]:
        row.status = "structure"
        row.note = "structure des pages du forum à régler (social-check --threads)" if url else "pas d'adresse de forum"
        s.add(row)
        s.flush()
        return row
    pause = float(bp.get("interval_s", 2.0))
    paris_now = now.astimezone(PARIS)
    salt = secrets.token_hex(16)  # ephemeral: authors can be counted, never recognised
    try:
        threads = social.thread_links(social.boursorama_page(url, client), url, str(bp["thread_link_regex"]))
        time.sleep(pause)
        msgs: list[social.ForumMessage] = []
        for t in threads[: int(c.get("thread_pages_max", 3))]:
            msgs += social.forum_messages(social.boursorama_page(t, client), sel, paris_now, salt)
            time.sleep(pause)
    except Exception as exc:  # noqa: BLE001 — a page failing gives « indisponible », never an estimate
        row.status, row.note = "indisponible", f"lecture du forum en échec ({exc.__class__.__name__})"
        s.add(row)
        s.flush()
        return row
    since = now - timedelta(hours=window_h)
    first_day = since.astimezone(PARIS).date()  # a page that shows only the day: the whole day counts
    recent = [
        m for m in msgs if (m.ts is not None and m.ts >= since) or (m.ts is None and m.day and m.day >= first_day)
    ]
    seen, uniq = set(), []
    for m in sorted(recent, key=lambda m: m.ts or now, reverse=True):
        key = (m.author_key, m.text[:120])
        if key not in seen:
            seen.add(key)
            uniq.append(m)
    uniq = uniq[: int(c.get("max_messages_per_value", 60))]
    labels, cost, why = (
        classify(s, gateway, config, inst.name or inst.isin, [m.text for m in uniq]) if uniq else ([], 0.0, None)
    )
    p = domain_params(c)
    dm = []
    for m, lab in zip(uniq, labels, strict=True):
        if lab is None:
            dm.append(Message(m.author_key, m.ts.timestamp() if m.ts else None))
            continue
        dm.append(
            Message(
                m.author_key,
                m.ts.timestamp() if m.ts else None,
                lab["theme"],
                lab["nature"],
                lab["direction"],
                lab["precision"] == "precise",
                scrub_numbers(str(lab.get("resume") or ""), m.text) if lab["theme"] != UNCLASSIFIED else "",
            )
        )
    agg = aggregate(dm, p, now.timestamp())
    row.data = agg.as_dict() | {"fils_lus": len(threads[: int(c.get("thread_pages_max", 3))])}
    row.summaries = agg.summaries
    row.effect = effect(agg, bool(inst.small_cap), p) if uniq else None
    row.threads = threads[: int(c.get("thread_pages_max", 3))]
    row.cost_usd = round(cost, 5)
    if not uniq:
        row.status, row.note = "ok", f"aucun message sur {window_h:.0f} h"
    elif why and agg.classified == 0:
        row.status, row.note = ("plafond" if "plafond" in why else "non_classe"), why
    else:
        row.status, row.note = "ok", why
    s.add(row)
    s.flush()
    return row


def latest(s: Session, isin: str, now: datetime | None = None, max_age_hours: float = 48) -> SocialContent | None:
    now = now or datetime.now(UTC)
    return s.scalar(
        select(SocialContent)
        .where(SocialContent.isin == isin, SocialContent.computed_at >= now - timedelta(hours=max_age_hours))
        .order_by(SocialContent.computed_at.desc(), SocialContent.id.desc())
    )


THEME_FR = {
    "resultats": "résultats", "guidance": "prévisions", "contrat": "contrat", "m_and_a": "opération (OPA, fusion)",
    "augmentation_capital": "augmentation de capital", "gouvernance": "gouvernance",
    "reco_broker": "avis d'analyste", "transaction_dirigeant": "transaction de dirigeant",
    "franchissement_seuil": "franchissement de seuil", "rachat_actions": "rachat d'actions",
    "reglementaire": "réglementaire", "dividende": "dividende",
}  # fmt: skip


def facts(row: SocialContent | None) -> list[str]:
    """Citable facts of the card and the thesis (French), from the stored aggregates only."""
    if row is None:
        return []
    if row.status in ("structure", "indisponible"):
        return [f"échanges du forum : indisponibles ({row.note})"]
    d = row.data or {}
    out = [
        f"forum : {d.get('messages', 0)} message(s) sur {row.window_hours:.0f} h"
        f" ({d.get('messages_recents', 0)} sur 2 h), {d.get('auteurs_distincts', 0)} auteur(s) distinct(s)"
    ]
    if d.get("theme_dominant"):
        nature = d.get("par_nature") or {}
        rum = nature.get("rumeur", 0)
        out.append(
            f"thème dominant : {THEME_FR.get(d['theme_dominant'], d['theme_dominant'])}, "
            f"{d.get('auteurs_sur_theme', 0)} auteur(s), direction {d.get('direction') or 'ambiguë'}"
            + (f", {rum} rumeur(s)" if rum else "")
            + (f", part précise {d['part_precise']:.0%}" if d.get("part_precise") is not None else "")
        )
    out.append(
        f"solidité : {d.get('solidite', 'indisponible')}" + (f" — {'; '.join(d['motifs'])}" if d.get("motifs") else "")
    )
    if row.status != "ok":
        out.append(f"contenu non analysé : {row.note}")
    for sm in row.summaries or []:
        out.append(f"résumé : {sm}")
    return out
