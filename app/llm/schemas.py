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
