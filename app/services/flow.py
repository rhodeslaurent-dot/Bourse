"""``/fonctionnement`` (user request of 26/10/2026): the tool as a flow diagram — data → analysis → decision →
follow-up → learning — each box being a feature, with the real last run of the jobs behind it (``jobs_runs``)
at the time of the visit. Descriptions are fixed text; every status, time and note comes from the database."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import JobRun


@dataclass(frozen=True)
class Node:
    title: str
    what: str  # what the feature does, in plain French
    jobs: tuple[str, ...]  # scheduler jobs behind it (empty: on request only)
    page: str | None = None
    page_label: str | None = None
    max_age_hours: float = 30.0  # older than this on a working day: « en retard »


@dataclass(frozen=True)
class Stage:
    title: str
    why: str
    nodes: tuple[Node, ...]


STAGES: tuple[Stage, ...] = (
    Stage(
        "1. Données",
        "Ce que l'outil collecte, chaque jour ou en continu.",
        (
            Node(
                "Univers des valeurs",
                "Listes des places suivies, capitalisations, secteurs ; une seule cotation par "
                "valeur (sa place d'origine).",
                ("universe_refresh",),
                "/watchlist",
                "Watchlist",
                24 * 8,
            ),
            Node(
                "Cours de clôture et indices",
                "Clôtures officielles de la veille, trous comblés, indicateurs du jour ; indice de chaque place.",
                ("eod_backfill_check",),
                "/sante",
                "Santé",
            ),
            Node(
                "Change et macro",
                "Taux BCE, Brent, VIX, taux d'État ; calendrier des annonces (BCE, Fed, inflation).",
                ("fx_refresh", "macro_snapshot", "macro_calendar"),
                "/rapport",
                "Rapport",
            ),
            Node(
                "Actualités, presse et podcasts",
                "Flux RSS des sources reconnues, classés ; podcasts (valeurs citées par les gérants).",
                ("news_scan",),
                "/lettres",
                "Lettres",
                6,
            ),
            Node(
                "Lettres d'analyse",
                "Lettres reçues par mail (Gmail), niveaux d'achat, objectifs et invalidations extraits.",
                ("imap_poll",),
                "/lettres",
                "Lettres",
                30,
            ),
            Node(
                "Positions courtes et fondamentaux",
                "Positions courtes nettes publiées (AMF…) ; consensus et fondamentaux gratuits.",
                ("short_positions_refresh", "fundamentals_refresh"),
                None,
                None,
                24 * 8,
            ),
            Node(
                "Comptes courtier",
                "Positions et ordres Saxo (lecture seule) ; jeton maintenu.",
                ("portfolio_sync", "saxo_token_keepalive"),
                "/positions",
                "Positions",
            ),
        ),
    ),
    Stage(
        "2. Analyse",
        "Ce que l'outil en déduit, sans rien décider.",
        (
            Node(
                "Régime de marché par place",
                "Vert / orange / rouge pour chaque place (indice, largeur, distribution, volatilité) et pour l'Europe.",
                ("daily_regime",),
                "/",
                "Décisions",
            ),
            Node(
                "Radar des signaux faibles",
                "Consultations Wikipédia, volume, forums : attention anormale, buzz précoce, emballement ; "
                "historique du forum lu lentement la nuit.",
                ("weak_signals", "forum_history"),
                "/opportunites",
                "Opportunités",
            ),
            Node(
                "Veille forums toutes les 20 min",
                "Accélération des échanges, lecture des messages, alerte et passage ciblé du moteur.",
                ("social_watch",),
                "/opportunites",
                "Opportunités",
                2,
            ),
            Node(
                "Watchlist momentum",
                "Valeurs en tendance et forte force relative.",
                ("watchlist_refresh",),
                "/watchlist",
                "Watchlist",
            ),
            Node(
                "Revue des lettres",
                "Synthèse, avis par valeur, confirmations et contradictions (IA vérifiée).",
                ("letters_review",),
                "/lettres",
                "Lettres",
            ),
        ),
    ),
    Stage(
        "3. Décision",
        "Ce que l'outil propose — vous décidez chez le courtier.",
        (
            Node(
                "Moteur de propositions",
                "Détecteurs → prix maximal, stop, objectifs → coûts et taille → portes "
                "(régime de la place, risque, score) → classement ; niveau de risque du jour.",
                ("opportunity_scan",),
                "/opportunites",
                "Opportunités",
            ),
            Node(
                "Comité d'investissement",
                "Avis contradictoire sur les meilleures propositions (observation, jamais appliqué).",
                ("committee_review",),
                "/opportunites",
                "Opportunités",
            ),
            Node(
                "Fiche de fixing", "Entrée au fixing de clôture sur un repli qui tient.", ("fixing_card",), None, None
            ),
            Node(
                "Réévaluation intraday",
                "Cours frais (Saxo 15 s, EODHD 15 min) : prix d'entrée atteint, déclencheur franchi, configuration "
                "dégradée ou limite dépassée ; seul un cours temps réel rend une entrée actionnable.",
                ("intraday_reeval",),
                "/opportunites",
                "Opportunités",
            ),
        ),
    ),
    Stage(
        "4. Suivi",
        "Ce qui protège et mesure ce qui est engagé.",
        (
            Node(
                "Surveillance des positions",
                "Stops, quantités non protégées, alertes P1.",
                ("position_monitor",),
                "/positions",
                "Positions",
            ),
            Node(
                "Rapport et revue",
                "Rapport du jour, revue du portefeuille du samedi, revue hebdomadaire.",
                ("daily_report", "portfolio_review", "weekly_review"),
                "/rapport",
                "Rapport",
                24 * 8,
            ),
            Node(
                "Résultats et performance",
                "Résultat des propositions passées, équité du jour.",
                ("outcomes_update", "kpi_snapshot"),
                "/performance",
                "Performance",
            ),
        ),
    ),
    Stage(
        "5. Apprentissage",
        "Ce qui vérifie que les règles gagnent vraiment.",
        (
            Node(
                "Backtest au coût complet",
                "Rejeu des détecteurs sur 8 ans, chaque dimanche.",
                ("backtest_weekly",),
                "/apprentissage",
                "Apprentissage",
                24 * 8,
            ),
            Node(
                "Recherche de déclencheurs",
                "Banc pré-enregistré lancé à la main (commande research) ; rapport agrégé.",
                (),
                "/apprentissage",
                "Apprentissage",
            ),
            Node("Sauvegarde", "Sauvegarde quotidienne de la base.", ("backup",), "/sante", "Santé"),
        ),
    ),
)


@dataclass
class NodeState:
    node: Node
    runs: list[dict[str, Any]] = field(default_factory=list)  # one per job: job, status, ended_at, note, error
    state: str = "jamais"  # ok | retard | erreur | jamais | manuel


def flow_state(s: Session, now: datetime | None = None) -> list[tuple[Stage, list[NodeState]]]:
    """Each stage with, per feature, the latest run of each of its jobs and an overall state."""
    now = now or datetime.now(UTC)
    names = sorted({j for st in STAGES for n in st.nodes for j in n.jobs})
    last_ids = dict(
        s.execute(select(JobRun.job, func.max(JobRun.id)).where(JobRun.job.in_(names)).group_by(JobRun.job)).all()
    )
    rows = (
        {r.job: r for r in s.scalars(select(JobRun).where(JobRun.id.in_(list(last_ids.values()))))} if last_ids else {}
    )
    out = []
    for st in STAGES:
        states = []
        for n in st.nodes:
            ns = NodeState(n)
            if not n.jobs:
                ns.state = "manuel"
            for j in n.jobs:
                r = rows.get(j)
                when = (r.ended_at or r.started_at) if r else None
                if when is not None and when.tzinfo is None:
                    when = when.replace(tzinfo=UTC)
                ns.runs.append(
                    {
                        "job": j,
                        "status": r.status if r else None,
                        "when": when,
                        "note": (r.note or "")[:220] if r else "",
                        "error": (r.error or "")[:220] if r else "",
                    }
                )
            if n.jobs:
                seen = [x for x in ns.runs if x["status"]]
                if not seen:
                    ns.state = "jamais"
                elif any(x["status"] == "error" for x in seen):
                    ns.state = "erreur"
                elif max((x["when"] for x in seen if x["when"]), default=now) < now - timedelta(
                    hours=n.max_age_hours + 48
                ):
                    ns.state = "retard"  # weekends and holidays tolerated (+48 h)
                else:
                    ns.state = "ok"
            states.append(ns)
        out.append((st, states))
    return out
