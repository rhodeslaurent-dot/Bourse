"""A1.6 / A1.8: RSS classified and attached, newsletter values with p_open/p_recv/drift, external
signals recorded with WATCH proposals, decision snapshots frozen, decisions via API."""

from datetime import UTC, date, datetime  # noqa: I001
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.data.dto import DataStatus, EodBar, MarketPerimeter, PriceType, Quote
from app.data.providers.imap_gmail import parse_eml
from app.db.models import (
    DecisionSnapshot,
    Instrument,
    NewsItem,
    NewsletterValue,
    PremarketWatch,
    Proposal,
    Signal,
    WatchlistRow,
)
from app.db.session import db_session
from app.llm.gateway import ClaudeGateway, LlmSettings
from app.llm.schemas import NewsClassification, NewsletterExtraction, NewsletterValue as NlValue
from app.main import create_app
from app.services.market_data import upsert_eod
from app.services.newsletters import ingest_mail

FX = Path(__file__).resolve().parents[1] / "fixtures"
SENDERS = {"zonebourse": ["@zonebourse.com"], "momentum_capital": ["@capital.fr"]}
DAY = date(2026, 9, 15)


def fake_llm(model, system, user, schema, temperature, max_tokens):
    if schema is NewsClassification:
        neg = "avertissement" in user.lower()
        return (
            schema(
                type="resultats",
                direction="negatif" if neg else "positif",
                magnitude="forte",
                ampleur_annoncee=0.8,
                resume="fixture",
                confiance=0.9,
            ),
            1000,
            100,
        )
    return (
        NewsletterExtraction(
            values=[
                NlValue(
                    name="TOTALENERGIES", isin="FR0000120271", direction="buy", levels={"objectif": 60.0, "stop": 52.5}
                )
            ]
        ),
        2000,
        200,
    )


@pytest.fixture
def client(config, calendars, db_engine, monkeypatch):
    for var in (
        "CF_ACCESS_TEAM_DOMAIN",
        "CF_ACCESS_AUD",
        "TELEGRAM_BOT_TOKEN",
        "HEALTHCHECKS_PING_KEY",
        "EODHD_API_TOKEN",
        "ANTHROPIC_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    app = create_app(config=config, calendars=calendars, start_scheduler=False, start_telegram=False)
    with TestClient(app) as c:
        c.app.state.llm = ClaudeGateway(
            LlmSettings(
                "claude-haiku-4-5",
                "claude-sonnet-4-6",
                0,
                15,
                5.0,
                {"claude-haiku-4-5": {"input": 1.0, "output": 5.0}},
                Path("app/llm/prompts"),
                True,
            ),
            call=fake_llm,
        )
        with db_session() as s:
            s.add(
                Instrument(
                    isin="FR0000120271", name="Société Alpha", mic="XPAR", ticker_eodhd="TTE.PA", in_universe=True
                )
            )
            s.add(
                Instrument(isin="FR0000120073", name="Air Liquide", mic="XPAR", ticker_eodhd="AI.PA", in_universe=True)
            )
            now = datetime.now(UTC)
            upsert_eod(
                s,
                [
                    EodBar(
                        source="eodhd",
                        market_perimeter=MarketPerimeter.PRIMARY,
                        received_at=now,
                        processed_at=now,
                        data_status=DataStatus.EOD,
                        symbol="TTE.PA",
                        day=date(2026, 9, 14),
                        open=54,
                        high=55,
                        low=53,
                        close=54.0,
                        volume=1000,
                    ),
                    EodBar(
                        source="eodhd",
                        market_perimeter=MarketPerimeter.PRIMARY,
                        received_at=now,
                        processed_at=now,
                        data_status=DataStatus.EOD,
                        symbol="TTE.PA",
                        day=DAY,
                        open=55.62,
                        high=56,
                        low=55,
                        close=55.9,
                        volume=1000,
                    ),
                ],
                {"TTE.PA": "FR0000120271"},
            )
        yield c


def test_rss_ingest_classifies_attaches_and_premarket(client):
    xml = (FX / "rss" / "actusnews_sample.xml").read_text(encoding="utf-8")
    rep = client.post("/api/ingest/rss", json={"source": "actusnews", "content": xml}).json()
    assert rep["new"] == 3 and rep["classified_llm"] == 3 and rep["llm_cost_usd"] > 0
    rep2 = client.post("/api/ingest/rss", json={"source": "actusnews", "content": xml}).json()
    assert rep2["new"] == 0  # dedup by guid
    with db_session() as s:
        alpha = s.scalar(select(NewsItem).where(NewsItem.guid == "an-1001"))
        assert (
            alpha.isins == ["FR0000120271"]
            and alpha.classification["direction"] == "positif"
            and alpha.classified_by == "llm"
            and alpha.llm_prompt_version == "1"
        )
        pw = s.scalars(select(PremarketWatch)).all()
        assert len(pw) == 1 and pw[0].isin == "FR0000120271" and pw[0].day == DAY
    assert client.post("/api/ingest/rss", json={"source": "bad_feed", "content": "<html>bad</html>"}).status_code == 400
    assert "coût du mois" in client.get("/sante").text


def test_rss_rules_fallback_without_llm(client):
    client.app.state.llm = None
    xml = (FX / "rss" / "actusnews_sample.xml").read_text(encoding="utf-8")
    rep = client.post("/api/ingest/rss", json={"source": "actusnews", "content": xml}).json()
    assert rep["classified_rules"] == 3 and rep["llm_cost_usd"] == 0
    with db_session() as s:
        beta = s.scalar(select(NewsItem).where(NewsItem.guid == "an-1002"))
        assert (
            beta.classification["direction"] == "negatif"
            and beta.classification["confiance"] < 0.5
            and beta.classified_by == "rules"
        )


def test_newsletter_values_drift_signal_snapshot(client, config):
    recv = datetime(2026, 9, 15, 8, 3, tzinfo=UTC)
    mail = parse_eml((FX / "imap" / "zonebourse_sample.eml").read_bytes(), SENDERS, received_at=recv)
    q = Quote(
        source="eodhd_rest",
        market_perimeter=MarketPerimeter.PRIMARY,
        received_at=recv,
        processed_at=recv,
        data_status=DataStatus.DELAYED,
        symbol="TTE.PA",
        market_timestamp=datetime(2026, 9, 15, 7, 45, tzinfo=UTC),
        price_type=PriceType.LAST,
        last=57.0,
        declared_delay_minutes=15,
    )
    with db_session() as s:
        rep = ingest_mail(s, mail, config, client.app.state.llm, {"FR0000120271": q})
        assert rep.new == 1 and rep.values >= 2 and rep.signals >= 1 and rep.parsed_by_llm == 1
        v = s.scalar(select(NewsletterValue).where(NewsletterValue.isin == "FR0000120271"))
        assert v.p_open == 55.62 and v.close_prev == 54.0 and v.p_recv == 57.0 and v.p_recv_status == "delayed"
        assert v.gap_open == pytest.approx(55.62 / 54 - 1) and v.drift_since_open == pytest.approx(57 / 55.62 - 1)
        sig = s.get(Signal, v.signal_id)
        assert (
            sig.detector == "external"
            and sig.source == "zonebourse"
            and sig.evidence["drift_since_open"] == pytest.approx(57 / 55.62 - 1)
        )
        p = s.scalar(select(Proposal).where(Proposal.signal_id == sig.id))
        assert p.action == "WATCH" and p.to_verify and p.stop == 52.5 and p.targets == [60.0]
        assert any("drift" in b for b in p.gates["blocked"])  # +2.5 % ≥ 2 % → no BUY on the letter alone
        snap = s.scalar(select(DecisionSnapshot).where(DecisionSnapshot.proposal_id == p.id))
        assert (
            snap.quotes["FR0000120271"]["data_status"] == "delayed"
            and snap.params_version == config.params.version
            and snap.portfolio_state is not None
        )
        assert (
            s.scalar(select(WatchlistRow).where(WatchlistRow.isin == "FR0000120271", WatchlistRow.reason == "external"))
            is not None
        )
        rep2 = ingest_mail(s, mail, config, client.app.state.llm, {})
        assert rep2.new == 0  # dedup by message id
        pid = p.id
    d = client.post(
        "/api/decisions", json={"proposal_id": pid, "decision": "watch", "reason": "attendre le pullback"}
    ).json()
    assert d["status"] == "watch"
    detail = client.get(f"/api/proposals/{pid}").json()
    assert (
        detail["decisions"][0]["decision"] == "watch" and detail["snapshot"]["quotes"]["FR0000120271"]["last"] == 57.0
    )
    opp = client.get("/api/opportunities", params={"day": datetime.now(UTC).date().isoformat()}).json()
    assert any(o["proposal_id"] == pid for o in opp["proposals"])
    assert client.post("/api/decisions", json={"proposal_id": 9999, "decision": "seen"}).status_code == 404


def test_newsletter_api_manual_and_eml(client):
    import base64

    raw = (FX / "imap" / "momentum_sample.eml").read_bytes()
    rep = client.post("/api/ingest/newsletter", json={"eml_base64": base64.b64encode(raw).decode()}).json()
    assert rep["new"] == 1
    rep2 = client.post(
        "/api/ingest/newsletter",
        json={
            "source": "abc_premium",
            "subject": "Avant l'ouverture",
            "body_text": "AIR LIQUIDE : achat, objectif 190 €.",
        },
    ).json()
    assert rep2["new"] == 1 and rep2["values"] >= 1
    bad = client.post(
        "/api/ingest/newsletter",
        json={"eml_base64": base64.b64encode((FX / "imap" / "unrelated_sample.eml").read_bytes()).decode()},
    )
    assert bad.status_code == 400
