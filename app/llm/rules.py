"""Keyword fallback when the LLM is unavailable (docs/14 §14.2): reduced confidence, same schema."""

from __future__ import annotations

from app.llm.schemas import NewsClassification

_TYPES = [
    (
        "resultats",
        (
            "résultats",
            "resultats",
            "chiffre d'affaires",
            "revenus",
            "semestriel",
            "trimestriel",
            "annuel",
            "bénéfice",
            "ebitda",
        ),
    ),
    ("guidance", ("guidance", "objectifs annuels", "perspectives", "relève", "releve", "abaisse")),
    ("contrat", ("contrat", "commande", "partenariat", "accord")),
    ("m_and_a", ("acquisition", "rachat", "offre publique", "opa", "fusion", "cession")),
    ("dividende", ("dividende",)),
    ("augmentation_capital", ("augmentation de capital", "levée de fonds", "placement privé")),
    ("gouvernance", ("nomination", "directeur", "conseil d'administration", "démission", "gouvernance")),
    (
        "reco_broker",
        (
            "relève sa recommandation",
            "abaisse sa recommandation",
            "objectif de cours",
            "recommandation",
            "broker",
            "analyste",
        ),
    ),
    ("reglementaire", ("amf", "autorisation", "réglementaire", "homologation", "fda", "ema")),
]
_POS = ("hausse", "supérieur aux attentes", "relève", "record", "croissance", "gain", "remporte", "renforce", "acheter")
_NEG = (
    "baisse",
    "avertissement",
    "profit warning",
    "inférieur",
    "suspension",
    "abaisse",
    "perte",
    "recul",
    "vendre",
    "dépréciation",
)
_STRONG = (
    "relèvement",
    "relève ses objectifs",
    "très supérieur",
    "record",
    "avertissement",
    "profit warning",
    "majeur",
    "significatif",
)


def classify_by_rules(title: str, body: str) -> NewsClassification:
    text = f"{title} {body}".lower()
    kind = "autre"
    for name, words in _TYPES:
        if any(w in text for w in words):
            kind = name
            break
    pos = sum(text.count(w) for w in _POS)
    neg = sum(text.count(w) for w in _NEG)
    direction = "positif" if pos > neg else "negatif" if neg > pos else "neutre"
    strong = any(w in text for w in _STRONG)
    magnitude = (
        "forte"
        if strong and kind in ("resultats", "guidance", "contrat", "m_and_a")
        else "moyenne"
        if kind in ("resultats", "guidance", "contrat", "m_and_a", "reco_broker")
        else "faible"
    )
    ampleur = 0.7 if strong else 0.4 if magnitude == "moyenne" else 0.1
    return NewsClassification(
        type=kind,
        direction=direction,
        magnitude=magnitude,
        ampleur_annoncee=ampleur,
        resume=title[:240],
        confiance=0.35,
    )
