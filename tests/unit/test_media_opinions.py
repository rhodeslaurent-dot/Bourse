"""docs/23 §23.7 — media opinions: values cited (checked against the text), caution after a media-driven
jump, record measured by source, podcast feeds read from Apple's directory."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import httpx2 as httpx

from app.llm.schemas import MediaExtraction

ISIN = "FR0000120271"


class Gateway:
    enabled = True

    def __init__(self, out):
        self.out, self.calls = out, 0

    def parse(self, s, purpose, prompt, user, schema, **kw):
        self.calls += 1
        return schema.model_validate(self.out), None


def _bar(isin, day, close):
    from app.db.models import PriceEod

    return PriceEod(isin=isin, day=day, open=close, high=close, low=close, close=close, volume=1000, source="eodhd",
                    received_at=datetime.now(UTC), data_status="eod")  # fmt: skip


def test_cited_values_are_checked_against_the_text_and_processed_once(db_engine):
    from app.db.models import MediaMention, NewsItem
    from app.db.session import db_session
    from app.services.media import extract_mentions

    out = {"values": [
        {"name": "TotalEnergies", "stance": "achat", "speaker": "Gérant A", "snippet": "J'achète TotalEnergies"},
        {"name": "Société Fantôme", "stance": "achat", "snippet": "inventée"},
    ]}  # fmt: skip
    gw = Gateway(out)
    with db_session() as s:
        s.add(_bar(ISIN, date(2026, 10, 22), 60.0))
        row = NewsItem(source="cest_votre_argent", guid="ep-1", dedup_hash="ep-1", url="u",
                       title="C'est votre argent", body="Gérant A : J'achète TotalEnergies.",
                       fetched_at=datetime(2026, 10, 23, 20, tzinfo=UTC), published_at=datetime(2026, 10, 23, 19, tzinfo=UTC))  # fmt: skip  # noqa: E501
        s.add(row)
        s.flush()
        assert extract_mentions(s, [row], gw, {"totalenergies": ISIN}) == 1
        assert extract_mentions(s, [row], gw, {"totalenergies": ISIN}) == 0 and gw.calls == 1  # once per item
        m = s.query(MediaMention).one()
        assert (m.isin, m.stance, m.speaker, m.price_ref, m.price_ref_day) == (
            ISIN,
            "achat",
            "Gérant A",
            60.0,
            date(2026, 10, 22),
        )
        assert row.classification["media"] == [{"name": "TotalEnergies", "stance": "achat", "speaker": "Gérant A"}]


def test_media_pressure_and_measured_record(db_engine):
    from app.db.models import MediaMention
    from app.db.session import db_session
    from app.services.media import MediaParams, favourable_sources, pressure, recent, track_record

    now = datetime(2026, 10, 23, tzinfo=UTC)
    with db_session() as s:
        d0 = date(2026, 1, 1)
        for k in range(60):
            s.add(_bar(ISIN, d0 + timedelta(days=k), 100.0 + k))  # rising: every buy wins
        for k in range(20):
            s.add(MediaMention(source="cest_votre_argent", item_guid=f"g{k}",
                               published_at=datetime(2026, 1, 2, tzinfo=UTC), isin=ISIN, name=f"TTE{k}",
                               stance="achat", price_ref=100.0, price_ref_day=d0, received_at=now))  # fmt: skip
        s.add(MediaMention(source="der_aktionaer", item_guid="x", published_at=now - timedelta(days=3), isin=ISIN,
                           name="TotalEnergies", stance="achat", price_ref=60.0, price_ref_day=date(2026, 10, 19),
                           received_at=now))  # fmt: skip
        s.flush()
        rec = {(r.source, r.stance): r for r in track_record(s, 20, 20)}
        assert rec[("cest_votre_argent", "achat")].n == 20 and rec[("cest_votre_argent", "achat")].favourable
        assert rec[("cest_votre_argent", "achat")].mean > 0
        assert favourable_sources(s, MediaParams()) == {"cest_votre_argent"}
        cited = recent(s, ISIN, now, 28)
        assert [m.source for m in cited] == ["der_aktionaer"]
        assert pressure(cited, 62.5, 0.03).source == "der_aktionaer"  # +4.2 % since the broadcast
        assert pressure(cited, 61.0, 0.03) is None  # +1.7 %: no caution


def test_podcast_feed_is_read_from_apples_directory():
    from app.data.providers.feed_discovery import podcast_feeds

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/lookup":
            return httpx.Response(200, json={"results": []})  # unknown id → search
        assert request.url.params["term"] == "C'est votre argent BFM"
        return httpx.Response(200, json={"results": [
            {"collectionName": "C'est votre argent", "artistName": "BFM Business", "collectionId": 1,
             "feedUrl": "https://feeds.example/cva.xml"}]})  # fmt: skip

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        found = podcast_feeds(c, "562595266", "C'est votre argent BFM")
    assert found == [("https://feeds.example/cva.xml", "C'est votre argent — BFM Business (id 1)")]


def test_media_schema_bounds_free_text():
    ext = MediaExtraction.model_validate({"values": [{"name": "X" * 300, "stance": "vente", "snippet": "y" * 900}]})
    assert len(ext.values[0].name) <= 128 and len(ext.values[0].snippet) <= 280


def test_letters_page_shows_media_citations_and_record(db_engine, config, calendars):
    from fastapi.testclient import TestClient

    from app.db.models import MediaMention
    from app.db.session import db_session
    from app.main import create_app

    now = datetime.now(UTC)
    with db_session() as s:
        s.add(MediaMention(source="cest_votre_argent", item_guid="g", published_at=now - timedelta(hours=2), isin=ISIN,
                           name="TotalEnergies", stance="vente", speaker="Gérant B", snippet="Je vends TotalEnergies",
                           received_at=now))  # fmt: skip
    app = create_app(config, calendars, start_scheduler=False, start_telegram=False)
    with TestClient(app) as c:
        html = c.get("/lettres").text
    assert "Médias : valeurs citées et bilan mesuré" in html and "Je vends TotalEnergies" in html and "Gérant B" in html
