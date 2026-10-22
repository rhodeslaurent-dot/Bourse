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
        "reglementaire",
        "autre",
    ]
    direction: Literal["positif", "negatif", "neutre"]
    magnitude: Literal["forte", "moyenne", "faible"]
    ampleur_annoncee: float = Field(ge=0, le=1)
    resume: Text(300)
    confiance: float = Field(ge=0, le=1)


class NewsletterValue(BaseModel):
    name: str
    isin: str | None = None
    direction: Literal["buy", "sell", "watch", "neutral"]
    levels: dict[str, float] = Field(default_factory=dict)
    snippet: str = ""


class NewsletterExtraction(BaseModel):
    values: list[NewsletterValue]
    sentiment: Literal["haussier", "baissier", "neutre"] = "neutre"


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


class LettersReviewOut(BaseModel):
    synthese: Text(1200)
    avis: Items(LetterOpinionOut, 40) = Field(default_factory=list)
    validations: Items(LetterValidationOut, 30) = Field(default_factory=list)
