"""Every value a letter recommends, analysed by the tool (docs/20 §20.10, user complaint of 29/10/2026: « les
valeurs remontées par la lettre Momentum apparaissent en orange sans analyse supplémentaire »).

One path for the letters: the reception (``imap_poll``, ``POST /api/ingest/newsletter``) and the letters review pass
the recommended values (« achat » or « à surveiller », ISIN resolved) to the engine's targeted scan, which builds one
proposal per value and day with the tool's own analysis and a verdict — or a « non analysable » proposal with the
reason. The funnel of each letter (read → resolved → analysed → verdicts) is stored in
``newsletter_items.parsed["funnel"]``, shown by ``letters-trace`` and in one line of the daily report."""

from __future__ import annotations

import logging
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import LoadedConfig
from app.db.models import NewsletterItem, NewsletterValue, Proposal, Signal
from app.domain.letter_levels import canonical_levels, recalculable
from app.domain.letter_setup import STOP_POLICIES, LetterParams
from app.domain.timeutil import PARIS

log = logging.getLogger("bourse.letters")


def letters_group(config: LoadedConfig) -> dict[str, Any] | None:
    g = getattr(config.params, "letters", None)
    return g if isinstance(g, dict) else None


def letter_params(config: LoadedConfig) -> LetterParams:
    """``letters`` (dated, optional). Absent → the former rule (R15 as the engine applied it: only letters with a
    buy, support or trigger level, a stop and a target are recalculated, on the letter's own stop), said on the
    cards of the other values (« non analysable »)."""
    stop_p = config.params.risk.stop
    max_adr = float(getattr(stop_p, "max_distance_adr", None) or 2.5)
    g = letters_group(config)
    if g is None:
        return LetterParams(stop_policy="letter_only", trigger_buffer_pct=0.0, max_stop_distance_adr=max_adr,
                            analyse_every_recommendation=False)  # fmt: skip
    policy = str(g.get("stop_policy") or "letter_within_max_else_structure")
    return LetterParams(
        stop_policy=policy if policy in STOP_POLICIES else "letter_within_max_else_structure",
        trigger_buffer_pct=float(g.get("trigger_buffer_pct", 0.003)),
        entry_max_pct_above_trigger=float(g.get("entry_max_pct_above_trigger", 0.01)),
        trigger_alert_pct=float(g.get("trigger_alert_pct", 0.03)),
        min_history_sessions=int(g.get("min_history_sessions", 60)),
        stop_lookback_low=int(g.get("stop_lookback_low", 10)),
        max_stop_distance_adr=max_adr,
        analyse_every_recommendation=bool(g.get("analyse_every_recommendation", True)),
    )


def _day_start(day: date) -> datetime:
    return datetime.combine(day, datetime.min.time(), tzinfo=PARIS).astimezone(UTC)


def todays_letter_proposals(s: Session, day: date, isins: list[str] | None = None) -> dict[str, Proposal]:
    """The letter proposal of the day per value (signal ``moteur_lettre``), the latest when several exist."""
    q = select(Proposal).where(Proposal.created_at >= _day_start(day),
                               Proposal.created_at < _day_start(day + timedelta(days=1)),
                               Proposal.deleted_at.is_(None))  # fmt: skip
    if isins is not None:
        q = q.where(Proposal.isin.in_(isins or ["-"]))
    props = list(s.scalars(q.order_by(Proposal.id)))
    sigs = {x.id: x for x in s.scalars(select(Signal).where(Signal.id.in_([p.signal_id for p in props] or [-1])))}
    return {p.isin: p for p in props if (sig := sigs.get(p.signal_id)) is not None and sig.source == "moteur_lettre"}


def analyse(
    s: Session,
    config: LoadedConfig,
    isins: list[str],
    quotes: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> Any | None:
    """The engine's targeted scan on the recommended values; with the engine disabled, each value still gets its
    « non analysable » proposal (the reason said)."""
    from app.domain.letter_setup import NotAnalysable
    from app.services import engine

    now = now or datetime.now(UTC)
    day = now.astimezone(PARIS).date()
    isins = sorted(set(isins))
    if not isins:
        return None
    if not engine.enabled(config):
        rep = engine.ScanReport()
        na = NotAnalysable("moteur", "moteur de propositions désactivé (groupes admission, detectors, scoring)")
        items = [(i, recs[0], na) for i in isins if (recs := engine._letter_levels(s, i, day))]
        engine._persist_not_analysable(s, config, day, now, items, quotes, rep)
        return rep
    return engine.scan(s, config, day, isins=isins, now=now, quotes=quotes)


def recommended_isins(s: Session, items: list[NewsletterItem]) -> list[str]:
    ids = [i.id for i in items] or [-1]
    return sorted(
        {
            v.isin
            for v in s.scalars(select(NewsletterValue).where(NewsletterValue.item_id.in_(ids)))
            if v.isin and v.direction in ("buy", "watch")
        }
    )


def analyse_items(
    s: Session,
    config: LoadedConfig,
    items: list[NewsletterItem],
    quotes: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> tuple[Any | None, list[dict[str, Any]]]:
    """Reception (docs/22 §22.4 D, docs/20 §20.10): all the recommended values of the letters just received, then
    their funnels stored. A failure of the scan leaves each value « non analysable » with the exception's class."""
    from app.domain.letter_setup import NotAnalysable
    from app.services import engine

    now = now or datetime.now(UTC)
    isins = recommended_isins(s, items)
    rep = None
    try:
        with s.begin_nested():
            rep = analyse(s, config, isins, quotes, now)
    except Exception as exc:  # noqa: BLE001 — never silence: each value gets its reason
        log.warning("analyse des lettres en échec (%s)", exc.__class__.__name__, exc_info=True)
        day = now.astimezone(PARIS).date()
        rep = engine.ScanReport(errors=[exc.__class__.__name__])
        na = NotAnalysable("erreur", f"analyse en échec ({exc.__class__.__name__})")
        engine._persist_not_analysable(
            s, config, day, now, [(i, r[0], na) for i in isins if (r := engine._letter_levels(s, i, day))], quotes, rep
        )
    return rep, store_funnels(s, items, now.astimezone(PARIS).date())


def item_funnel(s: Session, item: NewsletterItem, day: date) -> dict[str, Any]:
    values = list(s.scalars(select(NewsletterValue).where(NewsletterValue.item_id == item.id)))
    rec = sorted({v.isin for v in values if v.isin and v.direction in ("buy", "watch")})
    props = todays_letter_proposals(s, day, rec)
    verdicts = Counter(str((p.details or {}).get("verdict") or "sans_verdict") for p in props.values())
    na = Counter(
        str((p.details or {}).get("non_analysable") or "autre")
        for p in props.values()
        if (p.details or {}).get("verdict") == "non_analysable"
    )
    return {
        "read": len(values),
        "resolved": sum(1 for v in values if v.isin),
        "unresolved_names": sum(1 for v in values if not v.isin),
        "with_levels": sum(1 for v in values if v.levels),
        "recalculable": sum(1 for v in values if recalculable(canonical_levels(v.levels or {})[0])),
        "recommended": len(rec),
        "analysed": sum(1 for p in props.values() if (p.details or {}).get("verdict") != "non_analysable"),
        "proposals": len(props),
        "without_proposal": [i for i in rec if i not in props],
        "verdicts": dict(verdicts),
        "non_analysable": dict(na),
        "day": day.isoformat(),
    }


def store_funnels(s: Session, items: list[NewsletterItem], day: date) -> list[dict[str, Any]]:
    out = []
    for it in items:
        f = item_funnel(s, it, day)
        it.parsed = {**(it.parsed or {}), "funnel": f}  # reassigned: the JSON column is not mutation-tracked
        out.append(f)
    s.flush()
    return out


NA_LABELS = {
    "historique": "historique",
    "indicateurs": "indicateurs",
    "niveaux": "niveaux",
    "stop": "stop",
    "change": "change",
    "inactive": "valeur inactive",
    "referentiel": "référentiel",
    "moteur": "moteur désactivé",
    "erreur": "erreur",
}


def funnel_text(funnels: list[dict[str, Any]]) -> str | None:
    """« Lettres : 9 valeurs lues, 7 résolues, 7 analysées — 2 entrées possibles, 3 rejets, 1 non analysable
    (historique) »."""
    if not funnels:
        return None
    tot: Counter[str] = Counter()
    ver: Counter[str] = Counter()
    na: Counter[str] = Counter()
    for f in funnels:
        for k in ("read", "resolved", "recommended", "analysed"):
            tot[k] += int(f.get(k) or 0)
        ver.update(f.get("verdicts") or {})
        na.update(f.get("non_analysable") or {})
    parts = []
    entries = ver.get("entrer_maintenant", 0) + ver.get("fiche_fixing", 0)
    for n, word in (
        (entries, "entrée(s) possible(s)"),
        (ver.get("alerte_declencheur", 0), "alerte(s) au déclencheur"),
        (ver.get("attendre_repli", 0), "repli(s) attendu(s)"),
        (ver.get("bloque", 0), "bloquée(s)"),
        (ver.get("rejet", 0), "rejet(s)"),
    ):
        if n:
            parts.append(f"{n} {word}")
    if ver.get("non_analysable"):
        why = ", ".join(f"{NA_LABELS.get(k, k)} {v}" for k, v in sorted(na.items()))
        parts.append(f"{ver['non_analysable']} non analysable(s) ({why})")
    head = (
        f"Lettres : {tot['read']} valeur(s) lue(s), {tot['resolved']} résolue(s), {tot['recommended']} "
        f"recommandée(s), {tot['analysed']} analysée(s)"
    )
    return head + (" — " + ", ".join(parts) if parts else "")


def funnel_line(s: Session, config: LoadedConfig, since: datetime, until: datetime) -> str | None:
    """One line of the daily report over the letters received in [since, until) (``letters.report_line``)."""
    g = letters_group(config)
    if g is not None and not g.get("report_line", True):
        return None
    items = [
        i
        for i in s.scalars(select(NewsletterItem).where(NewsletterItem.received_at >= since))
        if (i.received_at if i.received_at.tzinfo else i.received_at.replace(tzinfo=UTC)) < until
    ]
    return funnel_text([(i.parsed or {}).get("funnel") for i in items if (i.parsed or {}).get("funnel")])
