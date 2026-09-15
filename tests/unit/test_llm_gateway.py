"""Rule 3: JSON validated, cache by hash, daily/monthly caps → LlmUnavailable, cost from params."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db.models import LlmCall
from app.db.session import db_session
from app.llm.gateway import ClaudeGateway, LlmSettings, LlmUnavailable, load_prompt
from app.llm.rules import classify_by_rules
from app.llm.schemas import NewsClassification


def _settings(**kw):
    base = dict(
        classify_model="claude-haiku-4-5",
        write_model="claude-sonnet-4-6",
        temperature=0,
        monthly_budget_usd=15,
        daily_budget_usd=1.0,
        prices={"claude-haiku-4-5": {"input": 1.0, "output": 5.0}},
        prompts_dir=Path("app/llm/prompts"),
        cache=True,
    )
    base.update(kw)
    return LlmSettings(**base)


def fake_call(model, system, user, schema, temperature, max_tokens):
    assert temperature == 0 and "classes" in system
    return (
        schema(
            type="resultats",
            direction="positif",
            magnitude="forte",
            ampleur_annoncee=0.7,
            resume="CA +18 %",
            confiance=0.9,
        ),
        500_000,
        100_000,
    )  # 0.5 $ + 0.5 $ = 1.0 $


def test_prompt_versioning():
    v, text = load_prompt(Path("app/llm/prompts"), "news_classify")
    assert v == "1" and "JSON" in text
    with pytest.raises(FileNotFoundError):
        load_prompt(Path("app/llm/prompts"), "nope")


def test_parse_cache_and_budget(db_engine):
    gw = ClaudeGateway(_settings(), call=fake_call)
    with db_session() as s:
        parsed, usage = gw.parse(
            s, "news_classify", "news_classify", "Société Alpha relève ses objectifs", NewsClassification
        )
        assert parsed.magnitude == "forte" and usage.cost_usd == pytest.approx(1.0) and not usage.cached
        parsed2, usage2 = gw.parse(
            s, "news_classify", "news_classify", "Société Alpha relève ses objectifs", NewsClassification
        )
        assert usage2.cached and usage2.cost_usd == 0 and parsed2 == parsed
        with pytest.raises(LlmUnavailable) as exc:
            gw.parse(s, "news_classify", "news_classify", "un autre texte", NewsClassification)
        assert "journalier" in str(exc.value)
        calls = s.scalars(select(LlmCall)).all()
        assert len(calls) == 2 and [c.cached for c in calls] == [False, True]


def test_disabled_without_key_and_api_error(db_engine, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    gw = ClaudeGateway(_settings())
    with db_session() as s, pytest.raises(LlmUnavailable):
        gw.parse(s, "x", "news_classify", "texte", NewsClassification)

    def boom(*a):
        raise RuntimeError("network")

    gw2 = ClaudeGateway(_settings(), call=boom)
    with db_session() as s, pytest.raises(LlmUnavailable):
        gw2.parse(s, "x", "news_classify", "texte 2", NewsClassification)


def test_rules_fallback_reduced_confidence():
    c = classify_by_rules("SOCIÉTÉ BÊTA : avertissement sur résultats", "profit warning, marge inférieure")
    assert c.type == "resultats" and c.direction == "negatif" and c.magnitude == "forte" and c.confiance < 0.5
    n = classify_by_rules("Nomination d'un directeur financier", "")
    assert n.type == "gouvernance" and n.magnitude == "faible"
    assert datetime.now(UTC).year >= 2026
