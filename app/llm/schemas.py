"""Pydantic output schemas for the two phase-1 LLM tasks (docs/06 D5, D6)."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, Field

# Length limits are *soft*: an over-long text from the model is clipped (and a too-long list cut)
# instead of failing the whole answer (22/10/2026: a 301-character summary made a letters review
# fail). The limits stay in the JSON schema given to the model. A literal quote is clipped without
# an ellipsis so that it remains a substring of the source text (docs/20 §20.4).


def _clip(n: int, ellipsis: bool = True) -> Any:
    def f(v: Any) -> Any:
        if not isinstance(v, str) or len(v) <= n:
            return v
        if not ellipsis:
            return v[:n]
        cut = v[: n - 1]
        space = cut.rfind(" ")
        return (cut[:space] if space > n * 0.6 else cut).rstrip(" ,;:") + "…"

    return f


def Text(n: int) -> Any:  # noqa: N802 — type factory
    return Annotated[str, BeforeValidator(_clip(n)), Field(json_schema_extra={"maxLength": n})]


def Quote(n: int) -> Any:  # noqa: N802
    return Annotated[str, BeforeValidator(_clip(n, ellipsis=False)), Field(json_schema_extra={"maxLength": n})]


def Items(item: Any, n: int) -> Any:  # noqa: N802
    return Annotated[
        list[item],
        BeforeValidator(lambda v: list(v)[:n] if isinstance(v, list | tuple) else v),
        Field(json_schema_extra={"maxItems": n}),
    ]


THEMES = (
    "taux", "inflation", "banques_centrales", "croissance", "emploi", "devises", "matieres_premieres", "energie",
    "geopolitique", "fiscalite_reglementation", "secteur", "resultats", "m_and_a", "autre",
)  # fmt: skip
Themes = Annotated[
    list[Literal[THEMES]],  # type: ignore[valid-type]
    BeforeValidator(
        lambda v: list(dict.fromkeys(x if x in THEMES else "autre" for x in v))[:4] if isinstance(v, list) else v
    ),
    Field(json_schema_extra={"maxItems": 4}),
]


class NewsClassification(BaseModel):
    type: Literal[
        "resultats",
        "guidance",
        "contrat",
        "m_and_a",
        "dividende",
        "augmentation_capital",
        "gouvernance",
        "reco_broker",
        "transaction_dirigeant",  # docs/23 §23.3 (MAR art. 19)
        "franchissement_seuil",
        "rachat_actions",
        "reglementaire",
        "autre",
    ]
    direction: Literal["positif", "negatif", "neutre"]
    magnitude: Literal["forte", "moyenne", "faible"]
    ampleur_annoncee: float = Field(ge=0, le=1)
    resume: Text(300)
    confiance: float = Field(ge=0, le=1)
    # docs/21 §21.5 — scope and themes (press review); defaults keep the v1 answers valid
    portee: Literal["macro", "secteur", "valeur"] = "valeur"
    themes: Themes = Field(default_factory=list)


class NewsletterValue(BaseModel):
    name: str
    isin: str | None = None
    direction: Literal["buy", "sell", "watch", "neutral"]
    levels: dict[str, float] = Field(default_factory=dict)
    snippet: str = ""


class NewsletterExtraction(BaseModel):
    values: list[NewsletterValue]
    sentiment: Literal["haussier", "baissier", "neutre"] = "neutre"


class MediaValue(BaseModel):
    """docs/23 §23.7 — a value cited by a podcast, a show or an article: an opinion, never a figure."""

    name: Text(128)
    stance: Literal["achat", "vente", "neutre"]
    speaker: Text(128) | None = None
    snippet: Text(280)


class MediaExtraction(BaseModel):
    values: Items(MediaValue, 15) = Field(default_factory=list)


class AssistantAnswer(BaseModel):
    """docs/17 §17.4 — grounded answer: every figure must come from the fact pack."""

    answer: Text(1200)
    facts_used: Items(str, 12) = Field(default_factory=list)
    caveats: Items(str, 6) = Field(default_factory=list)
    not_available: Items(str, 6) = Field(default_factory=list)


class LearningFinding(BaseModel):
    """docs/18 §18.6 — one observation drawn from the computed aggregates."""

    title: Text(200)
    detail: Text(600)
    evidence: Items(str, 6) = Field(default_factory=list)  # names of dossier tables / groups
    confidence: Literal["faible", "moyenne", "forte"]


class LearningQuestionOut(BaseModel):
    question: Text(300)
    why: Text(300)
    kind: Literal["texte", "choix"] = "texte"
    choices: Items(str, 5) = Field(default_factory=list)


class LearningSuggestionOut(BaseModel):
    kind: Literal["parametre", "processus", "donnees", "source"]
    title: Text(200)
    rationale: Text(800)
    expected_effect: Text(300)
    params_key: str | None = None
    proposed_value: float | None = None


class LearningReviewOut(BaseModel):
    """Weekly / on-demand review: the LLM reads aggregates only and never computes."""

    summary: Text(1500)
    findings: Items(LearningFinding, 6) = Field(default_factory=list)
    questions: Items(LearningQuestionOut, 3) = Field(default_factory=list)
    suggestions: Items(LearningSuggestionOut, 5) = Field(default_factory=list)


class LetterOpinionOut(BaseModel):
    """docs/20 §20.3 — what a letter says about one value (reading, never computing)."""

    name: Text(128)
    isin: str | None = None
    orientation: Literal["positive", "negative", "neutre"]
    explicite: bool = False
    action_lettre: Literal["achat", "vente", "surveiller", "aucune"] = "aucune"
    catalyseur: Text(160) = ""
    horizon: Literal["court", "moyen", "long", "non_precise"] = "non_precise"
    resume: Text(300)
    citation: Quote(200)
    lettres: Items(int, 8) = Field(default_factory=list)


class LetterValidationOut(BaseModel):
    cible: Literal["proposition", "position", "watchlist"]
    reference: Text(32)  # proposal id, or ISIN for a position / watchlist value
    verdict: Literal["confirme", "contredit", "sans_avis"]
    justification: Text(300)
    lettres: Items(int, 8) = Field(default_factory=list)


class IndexViewOut(BaseModel):
    """docs/07 §7.9.4 (option C): what the letters publish about the reference index (CAC 40) —
    levels copied from the text, never computed; « attendre » when the letter advises waiting."""

    indice: Text(32) = "CAC 40"
    obstacle_bas: float | None = None  # resistance zone to clear at the close
    obstacle_haut: float | None = None
    support_bas: float | None = None
    support_haut: float | None = None
    conseil: Literal["attendre", "acheter", "neutre", "non_precise"] = "non_precise"
    citation: Quote(200) = ""
    lettres: Items(int, 8) = Field(default_factory=list)


class LettersReviewOut(BaseModel):
    synthese: Text(1200)
    indice: IndexViewOut | None = None
    avis: Items(LetterOpinionOut, 40) = Field(default_factory=list)
    validations: Items(LetterValidationOut, 30) = Field(default_factory=list)


class ThesisArgument(BaseModel):
    texte: Text(220)
    faits: Items(str, 6) = Field(default_factory=list)  # identifiers of the facts (F1, F2…)
    nature: Literal["fait", "analyse", "opinion"] = "analyse"


class ThesisOut(BaseModel):
    """docs/22 §22.8 — investment thesis written from numbered facts; the LLM computes nothing."""

    arguments: Items(ThesisArgument, 5) = Field(default_factory=list)
    risques: Items(Text(220), 4) = Field(default_factory=list)
    pourquoi_maintenant: Text(300) = ""
    incertitudes: Text(300) = ""
    ce_qui_invaliderait: Text(300) = ""
