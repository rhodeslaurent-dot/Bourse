"""ADR-011 — evaluation of a model change on stored material, before switching: news already classified
(agreement per field with the stored classification) and letters reviews already run (schema validity,
numbers absent from the dossier, number of opinions). Real API calls, cache bypassed, hard cost cap."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import LetterOpinion, LetterReview, NewsItem
from app.llm.gateway import ClaudeGateway, LlmUnavailable
from app.llm.schemas import LettersReviewOut, NewsClassification
from app.services.assistant import unsupported_numbers

FIELDS = ("type", "direction", "magnitude", "portee")


@dataclass
class EvalReport:
    model: str
    news: int = 0
    agree: dict[str, int] = field(default_factory=lambda: dict.fromkeys(FIELDS, 0))
    disagreements: list[dict[str, Any]] = field(default_factory=list)
    letters: int = 0
    letters_invalid: int = 0
    letters_bad_numbers: int = 0
    letters_opinions: list[tuple[int, int]] = field(default_factory=list)  # (stored, new)
    failures: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    stopped: str = ""

    def rate(self, f: str) -> float | None:
        return self.agree[f] / self.news if self.news else None


def run_eval(
    s: Session,
    gateway: ClaudeGateway,
    classify_model: str,
    write_model: str,
    n_news: int,
    n_letters: int,
    max_usd: float,
) -> EvalReport:
    rep = EvalReport(f"{classify_model} / {write_model}")
    rows = list(
        s.scalars(select(NewsItem).where(NewsItem.classified_by == "llm").order_by(NewsItem.id.desc()).limit(n_news))
    )
    for n in rows:
        if rep.cost_usd >= max_usd:
            rep.stopped = f"plafond de coût atteint ({max_usd} $)"
            break
        text = f"Titre : {n.title}\n\nTexte : {(n.body or '')[:4000]}"
        try:
            out, usage = gateway.parse(s, "eval_news", "news_classify", text, NewsClassification,
                                       model=classify_model, use_cache=False)  # fmt: skip
        except LlmUnavailable as exc:
            rep.failures.append(f"nouvelle {n.id} : {exc}")
            continue
        rep.cost_usd += usage.cost_usd
        rep.news += 1
        old = n.classification or {}
        new = out.model_dump()
        diff = {f: (old.get(f), new.get(f)) for f in FIELDS if old.get(f) != new.get(f)}
        for f in FIELDS:
            rep.agree[f] += f not in diff
        if diff:
            rep.disagreements.append({"id": n.id, "titre": (n.title or "")[:100], **{f: v for f, v in diff.items()}})
    for lr in s.scalars(
        select(LetterReview).where(LetterReview.status == "ok").order_by(LetterReview.id.desc()).limit(n_letters)
    ):
        if rep.cost_usd >= max_usd:
            rep.stopped = f"plafond de coût atteint ({max_usd} $)"
            break
        if not lr.dossier:
            continue
        user = f"<dossier>\n{json.dumps(lr.dossier, ensure_ascii=False, indent=1)}\n</dossier>"
        try:
            out, usage = gateway.parse(s, "eval_letters", "letters_review", user, LettersReviewOut,
                                       model=write_model, max_tokens=10000, use_cache=False)  # fmt: skip
        except LlmUnavailable as exc:
            rep.letters_invalid += 1
            rep.failures.append(f"revue {lr.id} : {exc}")
            continue
        rep.cost_usd += usage.cost_usd
        rep.letters += 1
        rep.letters_bad_numbers += bool(unsupported_numbers(out.synthese, lr.dossier))
        stored = len(list(s.scalars(select(LetterOpinion.id).where(LetterOpinion.review_id == lr.id))))
        rep.letters_opinions.append((stored, len(out.avis)))
    return rep
