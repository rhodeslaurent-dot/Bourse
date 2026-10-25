"""docs/25 L6 — cost of the Claude calls per purpose: today, last 7 days, month to date, with the share
served by the cache. Read from ``llm_calls`` only (the gateway logs every call, cached or not)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import LlmCall
from app.domain.timeutil import PARIS

LABELS = {
    "news_classify": "Classement des nouvelles",
    "letters_review": "Revue des lettres",
    "newsletter_extract": "Extraction des lettres",
    "media_extract": "Extraction des médias",
    "thesis": "Thèses d'opportunité",
    "assistant": "Assistant",
    "learning_review": "Revue d'apprentissage",
    "committee_premortem": "Comité : pré-mortem",
    "committee_chair": "Comité : président",
    "eval_news": "Évaluation modèles : nouvelles",
    "eval_letters": "Évaluation modèles : lettres",
}


@dataclass
class PurposeCost:
    purpose: str
    label: str
    calls_month: int = 0
    cached_month: int = 0
    cost_today: float = 0.0
    cost_7d: float = 0.0
    cost_month: float = 0.0
    tokens_in_month: int = 0
    tokens_out_month: int = 0
    models: set[str] = field(default_factory=set)

    @property
    def per_call(self) -> float | None:
        paid = self.calls_month - self.cached_month
        return self.cost_month / paid if paid else None

    @property
    def cache_share(self) -> float | None:
        return self.cached_month / self.calls_month if self.calls_month else None


@dataclass
class CostBoard:
    rows: list[PurposeCost]
    today: float
    last_7d: float
    month: float
    days_elapsed: int
    projected_month: float | None  # 7-day average × days in the month — a projection, labelled as such


def cost_board(s: Session, now: datetime | None = None) -> CostBoard:
    now = (now or datetime.now(UTC)).astimezone(PARIS)  # budgets follow the market day (gateway.budget_ok)
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month0 = day0.replace(day=1)
    week0 = day0 - timedelta(days=6)
    since = min(month0, week0).astimezone(UTC)
    by: dict[str, PurposeCost] = {}
    for c in s.scalars(select(LlmCall).where(LlmCall.ts >= since)):
        ts = c.ts.replace(tzinfo=UTC) if c.ts.tzinfo is None else c.ts
        row = by.setdefault(c.purpose, PurposeCost(c.purpose, LABELS.get(c.purpose, c.purpose)))
        cost = float(c.cost_usd or 0.0)
        if ts >= week0:
            row.cost_7d += cost
        if ts >= day0:
            row.cost_today += cost
        if ts >= month0:
            row.calls_month += 1
            row.cached_month += bool(c.cached)
            row.cost_month += cost
            row.tokens_in_month += int(c.input_tokens or 0)
            row.tokens_out_month += int(c.output_tokens or 0)
            row.models.add(c.model)
    rows = sorted(by.values(), key=lambda r: (-r.cost_month, -r.cost_7d, r.purpose))
    nxt = (month0 + timedelta(days=32)).replace(day=1)
    days_in_month = (nxt - month0).days
    last_7d = sum(r.cost_7d for r in rows)
    return CostBoard(
        rows=rows,
        today=sum(r.cost_today for r in rows),
        last_7d=last_7d,
        month=sum(r.cost_month for r in rows),
        days_elapsed=(day0 - month0).days + 1,
        projected_month=last_7d / 7 * days_in_month if rows else None,
    )
