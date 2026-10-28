"""Displayed vocabulary (docs/17 §17.2): technical identifiers → French labels and short help.

The API keeps the identifiers; templates call ``label(kind, value)`` and ``help_of(key)``.
"""

from __future__ import annotations

LABELS: dict[str, dict[str, str]] = {
    "execution_state": {
        "order_entered": "Ordre saisi",
        "filled_partial": "Partiellement exécutée",
        "filled": "Exécutée",
    },
    "protection_state": {
        "unprotected": "Non protégée",
        "partially_protected": "Partiellement protégée",
        "protected": "Protégée",
    },
    "data_status": {
        "realtime": "Temps réel",
        "delayed": "Différé 15 min",
        "eod": "Clôture",
        "stale": "Périmé",
        "unavailable": "Indisponible",
    },
    "order_state": {
        "entered": "Saisi chez le courtier",
        "partially_filled": "Partiellement exécuté",
        "filled": "Exécuté",
        "cancelled": "Annulé",
        "expired": "Expiré",
        "rejected": "Rejeté",
    },
    "regime": {"green": "Marché porteur", "orange": "Marché incertain", "red": "Marché défavorable"},
    "mode": {"comptant": "Comptant", "srd": "SRD"},
    "track_verdict": {
        "favorable": "favorable",
        "neutre": "neutre",
        "defavorable": "défavorable",
        "insuffisant": "insuffisant (trop peu de cas)",
    },
    "outcome_status": {
        "pending": "En attente de séance",
        "running": "En cours",
        "not_filled": "Entrée jamais atteinte",
        "stopped": "Stop touché",
        "target": "Objectif atteint",
        "horizon": "Fin d'horizon",
        "unavailable": "Cours indisponibles",
    },
    "decision_group": {
        "prise": "Prise",
        "ignoree": "Ignorée",
        "vue_sans_suite": "Vue sans suite",
        "sans_decision": "Sans décision",
    },
    "suggestion_kind": {
        "parametre": "Paramètre",
        "processus": "Façon de faire",
        "donnees": "Données",
        "source": "Source",
    },
    "memory_source": {"utilisateur": "saisie libre", "reponse": "réponse à l'IA", "suggestion": "proposition acceptée"},
    "availability": {"disponible": "Disponible", "reunion": "En réunion", "absent": "Absent"},
    "availability_source": {
        "schedule": "selon l'emploi du temps",
        "schedule_default": "hors créneau : mode par défaut",
        "web": "forcé depuis l'interface",
        "api": "forcé via l'API",
        "telegram": "forcé via Telegram",
    },
    "account": {"pea": "PEA", "cto_cash": "CTO comptant", "cto_srd": "CTO SRD", "cto": "CTO"},
    "action": {
        "BUY": "Achat",
        "WATCH": "À surveiller",
        "HOLD": "Conserver",
        "SELL": "Vendre",
        "REDUCE": "Alléger",
        "NO_TRADE": "Pas d'opération",
    },
    "proposal_status": {
        "open": "Ouverte",
        "expired": "Expirée",
        "taken": "Ordre saisi",
        "watch": "Surveillée",
        "ignored": "Ignorée",
    },
    "decision": {
        "seen": "Vu",
        "watch": "Watch",
        "order_entered": "Ordre saisi",
        "ignored": "Ignorée",
        "snoozed": "Reportée",
        "override": "Dérogation accordée",
    },
    "watch_reason": {
        "momentum": "Momentum (force relative)",
        "external": "Lettre / analyste",
        "manual": "Ajout manuel",
        "a_l_affut": "À l’affût (repli)",
    },
    "detector": {
        "d1": "D1 · gap + catalyseur",
        "d2": "D2 · cassure",
        "d3": "D3 · tendance",
        "d4": "D4 · repli sur moyenne",
        "d5": "D5 · relais",
        "d6": "D6 · catalyseur",
        "external": "Source externe",
    },
}

HELP: dict[str, str] = {
    "rs_rank": "Force relative (rang 0–100) : performance relative à l'univers sur 1, 3, 6 et 12 mois ; 100 = la plus forte",  # noqa: E501
    "cours_live": "Dernier cours relevé (Saxo toutes les 15 s si disponible, sinon EODHD toutes les 15 min), avec sa variation depuis la dernière clôture, son statut (temps réel, différé, périmé) et son heure de marché ; hors séance : dernière clôture",  # noqa: E501
    "mm50": "Moyenne mobile des 50 dernières clôtures",
    "mm200": "Moyenne mobile des 200 dernières clôtures",
    "pivot_60": "Plus haut des 60 dernières séances : niveau de cassure potentiel",
    "atr14": "Amplitude moyenne sur 14 séances (ATR) : mouvement quotidien typique, en €",
    "adv_eur_20": "Montant moyen échangé par séance sur 20 séances (liquidité)",
    "rvol": "Volume du jour rapporté à la moyenne des 20 dernières séances",
    "rsi14": "Indice de force (RSI 14) : > 70 suracheté, < 30 survendu",
    "high_52w": "Plus haut des 52 dernières semaines",
    "R": "R = risque initial : distance au stop × quantité ; un gain de 2 R vaut deux fois le risque pris",
    "to_verify": "Donnée différée ou état de compte périmé : aucune fiche d'ordre tant que ce n'est pas vérifié",
    "declarative": "État saisi à la main, non encore confirmé par le courtier",
    "expectancy": "Espérance par trade, en R : (taux de réussite × gain moyen) − (taux d'échec × perte moyenne)",
    "profit_factor": "Profit factor : somme des gains / somme des pertes",
    "gap_scenario": "Perte si toutes les positions ouvraient 10 % sous leur stop",
}

FEATURE_LABELS: dict[str, str] = {
    "rs_rank": "Force relative",
    "mm50": "Moyenne 50 j",
    "mm200": "Moyenne 200 j",
    "pivot_60": "Plus haut 60 j",
    "high_52w": "Plus haut 52 s",
    "atr14": "Amplitude 14 j",
    "rvol": "Volume relatif",
    "rsi14": "RSI 14",
    "adr20": "Amplitude 20 j (%)",
    "consolidation_20": "Consolidation 20 j",
}


def label(kind: str, value: object) -> str:
    """French label for a technical value; the value itself when unknown (never hidden)."""
    if value is None:
        return "—"
    return LABELS.get(kind, {}).get(str(value), str(value))


def help_of(key: str) -> str:
    return HELP.get(key, "")
