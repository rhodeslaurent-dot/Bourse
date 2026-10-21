"""Pydantic output schemas for the two phase-1 LLM tasks (docs/06 D5, D6)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


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
    resume: str = Field(max_length=300)
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

    answer: str = Field(max_length=1200)
    facts_used: list[str] = Field(default_factory=list, max_length=12)
    caveats: list[str] = Field(default_factory=list, max_length=6)
    not_available: list[str] = Field(default_factory=list, max_length=6)


class LearningFinding(BaseModel):
    """docs/18 §18.6 — one observation drawn from the computed aggregates."""

    title: str = Field(max_length=200)
    detail: str = Field(max_length=600)
    evidence: list[str] = Field(default_factory=list, max_length=6)  # names of dossier tables / groups
    confidence: Literal["faible", "moyenne", "forte"]


class LearningQuestionOut(BaseModel):
    question: str = Field(max_length=300)
    why: str = Field(max_length=300)
    kind: Literal["texte", "choix"] = "texte"
    choices: list[str] = Field(default_factory=list, max_length=5)


class LearningSuggestionOut(BaseModel):
    kind: Literal["parametre", "processus", "donnees", "source"]
    title: str = Field(max_length=200)
    rationale: str = Field(max_length=800)
    expected_effect: str = Field(max_length=300)
    params_key: str | None = None
    proposed_value: float | None = None


class LearningReviewOut(BaseModel):
    """Weekly / on-demand review: the LLM reads aggregates only and never computes."""

    summary: str = Field(max_length=1500)
    findings: list[LearningFinding] = Field(default_factory=list, max_length=6)
    questions: list[LearningQuestionOut] = Field(default_factory=list, max_length=3)
    suggestions: list[LearningSuggestionOut] = Field(default_factory=list, max_length=5)
