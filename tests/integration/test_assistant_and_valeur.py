"""docs/17 §17.4 (A17.2) and §17.8: grounded assistant (fact pack, number guard, rules fallback),
instrument page and search."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import AssistantMessage, Instrument, PriceEod
from app.db.session import db_session
from app.llm.gateway import ClaudeGateway, LlmSettings
from app.llm.schemas import AssistantAnswer
from app.main import create_app
from app.services.assistant import ask, build_context, unsupported_numbers

T = datetime(2026, 10, 20, 15, 40, tzinfo=UTC)


@pytest.fixture
def client(config, calendars, db_engine, monkeypatch):
    monkeypatch.setenv("ALLOW_UNAUTHENTICATED_WRITES", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = create_app(config, calendars, start_scheduler=False, start_telegram=False)
    with TestClient(app) as c:
        yield c


def _seed():
    with db_session() as s:
        s.add(
            Instrument(
                isin="FR0000120271",
                name="TotalEnergies",
                ticker_local="TTE",
                mic="XPAR",
                pea_eligible=True,
                in_universe=True,
            )
        )
        for i, px in enumerate((55.1, 55.8, 56.5)):
            s.add(
                PriceEod(
                    isin="FR0000120271",
                    day=date(2026, 10, 16 + i),
                    open=px,
                    high=px,
                    low=px,
                    close=px,
                    volume=1000,
                    source="eodhd",
                    received_at=T,
                    data_status="eod",
                )
            )


def test_number_guard():
    ctx = {"cloture": 56.5, "rsi": 62.3, "latent_pct": 0.034}
    assert unsupported_numbers("La clôture est de 56,50 € et le RSI vaut 62.", ctx) == []
    assert unsupported_numbers("Hausse de 3,4 % depuis l'achat.", ctx) == []
    assert unsupported_numbers("Objectif 61,20 € d'ici 3 semaines.", ctx) == ["61,20"]


def test_rules_fallback_and_rejection(db_engine, config):
    _seed()
    with db_session() as s:
        ctx = build_context(s, "FR0000120271", 40000.0)
        assert ctx["valeur"]["nom"] == "TotalEnergies" and ctx["dernieres_clotures"][0]["cloture"] == 56.5
        m = ask(s, None, "Où en est la valeur ?", "FR0000120271", 40000.0)
        assert m.answered_by == "rules" and "non configuré" in m.answer["answer"]

    def fake_bad(model, system, user, schema, temperature, max_tokens):
        assert "<dossier>" in user and schema is AssistantAnswer
        return AssistantAnswer(answer="Le cours devrait atteindre 72,40 €."), 900, 120

    def fake_ok(model, system, user, schema, temperature, max_tokens):
        return (
            AssistantAnswer(answer="Dernière clôture 56,50 € le 2026-10-18 (clôture).", facts_used=["cloture 56.5"]),
            900,
            80,
        )

    settings = LlmSettings(
        "claude-haiku-4-5",
        "claude-sonnet-4-6",
        0,
        15,
        5,
        {"claude-sonnet-4-6": {"input": 3.0, "output": 15.0}},
        __import__("pathlib").Path("app/llm/prompts"),
        False,
    )
    with db_session() as s:
        bad = ask(s, ClaudeGateway(settings, call=fake_bad), "Objectif ?", "FR0000120271", 40000.0)
        assert bad.answered_by == "rejected" and "72,40" in bad.answer["answer"] and bad.cost_usd > 0
        ok = ask(s, ClaudeGateway(settings, call=fake_ok), "Dernier cours ?", "FR0000120271", 40000.0)
        assert ok.answered_by == "llm" and ok.model == "claude-sonnet-4-6"


def test_assistant_page_and_instrument_page(client):
    _seed()
    r = client.post(
        "/assistant", data={"question": "Tendance de TotalEnergies ?", "isin": "TotalEnergies"}, follow_redirects=False
    )
    assert r.status_code == 303 and r.headers["location"] == "/assistant?isin=FR0000120271"
    html = client.get("/assistant?isin=FR0000120271").text
    assert "Tendance de TotalEnergies" in html and "dossier de faits" in html and "données seules" in html
    with db_session() as s:
        assert s.scalar(select(AssistantMessage)).isin == "FR0000120271"
    page = client.get("/valeur/FR0000120271").text
    assert "TotalEnergies" in page and "56.50 €" in page and "<polyline" in page and "PEA éligible" in page
    assert client.get("/valeur/XX0000000000").status_code == 404
    found = client.get("/recherche?q=total", follow_redirects=False)
    assert found.status_code == 303 and found.headers["location"] == "/valeur/FR0000120271"
    assert "Aucune valeur trouvée" in client.get("/recherche?q=zz").text


def test_gateway_is_wired_when_llm_configured(config, calendars, db_engine, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    app = create_app(config, calendars, start_scheduler=False, start_telegram=False)
    assert app.state.llm is not None and app.state.llm.enabled
