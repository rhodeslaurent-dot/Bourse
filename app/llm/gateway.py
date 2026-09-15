"""Claude API gateway (CLAUDE.md rule 3): classify / extract / write only, JSON validated by
Pydantic, temperature 0, cache by prompt hash, daily and monthly spend caps, rule-based fallback
decided by the caller. Model ids and prices are *parameters* (``params.yaml › llm``)."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import LlmCache, LlmCall

log = logging.getLogger("bourse.llm")
T = TypeVar("T", bound=BaseModel)


class LlmUnavailable(RuntimeError):
    """Budget reached, no API key, or API error: the caller falls back to rules."""


@dataclass(frozen=True)
class LlmUsage:
    input_tokens: int
    output_tokens: int
    cost_usd: float
    cached: bool


@dataclass
class LlmSettings:
    classify_model: str
    write_model: str
    temperature: float
    monthly_budget_usd: float
    daily_budget_usd: float
    prices: dict[str, dict[str, float]]  # model → {input, output} USD per MTok (dated in params)
    prompts_dir: Path
    cache: bool = True


def load_prompt(prompts_dir: Path, name: str) -> tuple[str, str]:
    """Return (version, text) for ``<name>_vN.md`` with the highest N."""
    files = sorted(prompts_dir.glob(f"{name}_v*.md"))
    if not files:
        raise FileNotFoundError(f"prompt {name} introuvable dans {prompts_dir}")
    f = files[-1]
    return f.stem.split("_v")[-1], f.read_text(encoding="utf-8")


def _sdk_parse(  # noqa: UP047
    model: str, system: str, user: str, schema: type[T], temperature: float, max_tokens: int
) -> tuple[T, int, int]:
    """Real call through the official SDK (``messages.parse`` validates against the schema)."""
    import anthropic

    client = anthropic.Anthropic()
    response = client.messages.parse(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_format=schema,
    )
    if response.stop_reason == "refusal":
        raise LlmUnavailable("réponse refusée par le modèle")
    parsed = response.parsed_output
    if parsed is None:
        raise LlmUnavailable("sortie JSON invalide")
    return parsed, response.usage.input_tokens, response.usage.output_tokens


class ClaudeGateway:
    def __init__(self, settings: LlmSettings, call: Callable[..., tuple[Any, int, int]] | None = None) -> None:
        self.settings = settings
        self._call = call or _sdk_parse
        self.enabled = bool(os.environ.get("ANTHROPIC_API_KEY")) or call is not None

    # --- budget -----------------------------------------------------------------------------
    def spent_usd(self, s: Session, since: datetime) -> float:
        return float(
            s.scalar(
                select(func.coalesce(func.sum(LlmCall.cost_usd), 0.0)).where(
                    LlmCall.ts >= since, LlmCall.cached.is_(False)
                )
            )
            or 0.0
        )

    def budget_ok(self, s: Session, now: datetime | None = None) -> tuple[bool, str]:
        from app.domain.timeutil import PARIS

        now = (now or datetime.now(UTC)).astimezone(PARIS)  # budgets follow the market day, not UTC
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
        day = self.spent_usd(s, day_start)
        month = self.spent_usd(s, month_start)
        if month >= self.settings.monthly_budget_usd:
            return False, f"plafond mensuel LLM atteint ({month:.2f} $ ≥ {self.settings.monthly_budget_usd} $)"
        if day >= self.settings.daily_budget_usd:
            return False, f"plafond journalier LLM atteint ({day:.2f} $ ≥ {self.settings.daily_budget_usd} $)"
        return True, ""

    def cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        p = self.settings.prices.get(model)
        if not p:
            raise LlmUnavailable(
                f"prix inconnu pour {model} (llm.prices_usd_per_mtok) : appel refusé, repli par règles"
            )
        return input_tokens * p["input"] / 1e6 + output_tokens * p["output"] / 1e6

    # --- call with cache ------------------------------------------------------------------
    def parse(
        self,
        s: Session,
        purpose: str,
        prompt_name: str,
        user_text: str,
        schema: type[T],
        model: str | None = None,
        max_tokens: int = 1024,
    ) -> tuple[T, LlmUsage]:
        model = model or self.settings.classify_model
        version, system = load_prompt(self.settings.prompts_dir, prompt_name)
        h = hashlib.sha256(f"{model}|{prompt_name}|{version}|{system}|{user_text}".encode()).hexdigest()
        if self.settings.cache:
            cached = s.scalar(select(LlmCache).where(LlmCache.prompt_hash == h))
            if cached is not None:
                s.add(
                    LlmCall(
                        ts=datetime.now(UTC),
                        model=model,
                        prompt_version=version,
                        prompt_hash=h,
                        cached=True,
                        purpose=purpose,
                    )
                )
                return schema.model_validate(cached.response), LlmUsage(0, 0, 0.0, True)
        if not self.enabled:
            raise LlmUnavailable("ANTHROPIC_API_KEY absent")
        ok, why = self.budget_ok(s)
        if not ok:
            raise LlmUnavailable(why)
        if model not in self.settings.prices:
            raise LlmUnavailable(
                f"prix inconnu pour {model} (llm.prices_usd_per_mtok) : appel refusé, repli par règles"
            )
        try:
            parsed, tin, tout = self._call(model, system, user_text, schema, self.settings.temperature, max_tokens)
        except LlmUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 — SDK errors → fallback rules
            raise LlmUnavailable(f"API Claude : {exc.__class__.__name__}") from exc
        cost = self.cost(model, tin, tout)
        s.add(
            LlmCall(
                ts=datetime.now(UTC),
                model=model,
                prompt_version=version,
                prompt_hash=h,
                input_tokens=tin,
                output_tokens=tout,
                cost_usd=cost,
                cached=False,
                purpose=purpose,
            )
        )
        if self.settings.cache:
            s.add(LlmCache(prompt_hash=h, response=json.loads(parsed.model_dump_json()), created_at=datetime.now(UTC)))
        s.flush()
        return parsed, LlmUsage(tin, tout, cost, False)


def settings_from(config) -> LlmSettings | None:  # type: ignore[no-untyped-def]
    llm = config.params.llm
    if llm is None or not config.feature_enabled("llm"):
        return None
    raw = config.raw.get("llm", {})
    return LlmSettings(
        classify_model=llm.classify_model,
        write_model=llm.write_model,
        temperature=llm.temperature,
        monthly_budget_usd=llm.monthly_budget_usd,
        daily_budget_usd=float(raw.get("daily_budget_usd", llm.monthly_budget_usd / 30)),
        prices=dict(raw.get("prices_usd_per_mtok", {})),
        prompts_dir=Path(llm.prompt_versions_dir),
        cache=llm.cache,
    )
