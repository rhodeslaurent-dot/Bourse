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


# docs/23 §23.3 — regulatory notices follow fixed wordings: classified by rules (no LLM), high confidence
_INSIDER = (
    "déclaration des dirigeants", "declaration des dirigeants", "transactions des dirigeants",
    "transaction de dirigeant",
    "eqs-dd", "directors' dealings", "directors dealings", "director's dealings", "managers' transactions",
    "managers transactions", "managerial transactions", "pdmr", "art. 19 mar", "article 19 mar",
    "personnes exerçant des responsabilités dirigeantes", "führungsaufgaben wahrnehmen", "eigengeschäfte",
    "insynshandel", "johdon liiketoimet", "ledende medarbejderes", "primærinnsider", "transakcje osób",
)  # fmt: skip
_BUY = r"\b(acquisition|achat|souscription|kauf|erwerb|purchase|buy|bought|köp|osto|køb|kjøp|zakup)\b"
_SELL = r"\b(cession|vente|verkauf|veräußerung|sale|sell|sold|försäljning|myynti|salg|sprzedaż)\b"
_THRESHOLD = (
    "franchissement de seuil", "franchissements de seuil", "déclaration de franchissement", "stimmrechtsmitteilung",
    "major shareholding", "major holdings", "flaggningsmeddelande", "liputusilmoitus",
    "major shareholder announcement",
)  # fmt: skip
_BUYBACK = (
    "rachat d'actions", "rachat d’actions", "programme de rachat", "aktienrückkauf", "share buyback",
    "share repurchase",
    "återköp av aktier", "tilbakekjøp av egne aksjer",
)  # fmt: skip


def classify_regulatory(title: str, body: str) -> NewsClassification | None:
    """Insider transaction, threshold crossing or buyback notice recognised from its official wording,
    else ``None`` (the LLM or the keyword rules classify it). A purchase by a manager is positive
    (moderate), a sale negative but weak (often a personal reason; docs/23 §23.1)."""
    import re

    head = f"{title} {(body or '')[:600]}".lower()
    if any(w in head for w in _INSIDER):
        buy, sell = re.search(_BUY, head) is not None, re.search(_SELL, head) is not None
        direction, magnitude = (
            ("positif", "moyenne")
            if buy and not sell
            else ("negatif", "faible")
            if sell and not buy
            else ("neutre", "faible")
        )
        nature = {"positif": "achat", "negatif": "vente"}.get(direction, "nature non précisée")
        return NewsClassification(
            type="transaction_dirigeant",
            direction=direction,
            magnitude=magnitude,
            ampleur_annoncee=0.4 if direction == "positif" else 0.1,
            resume=f"Transaction de dirigeant ({nature}) : {title}"[:240],
            confiance=0.9,
        )
    if any(w in head for w in _THRESHOLD):
        return NewsClassification(
            type="franchissement_seuil", direction="neutre", magnitude="faible", ampleur_annoncee=0.0,
            resume=f"Franchissement de seuil : {title}"[:240], confiance=0.9,
        )  # fmt: skip
    if any(w in head for w in _BUYBACK):
        return NewsClassification(
            type="rachat_actions", direction="positif", magnitude="faible", ampleur_annoncee=0.2,
            resume=f"Rachat d'actions : {title}"[:240], confiance=0.9,
        )  # fmt: skip
    return None
