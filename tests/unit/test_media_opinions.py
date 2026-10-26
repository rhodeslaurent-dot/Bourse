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
        assert row.classification["media_rejetes"] == ["Société Fantôme"]  # shown by media-check --feed


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


def test_weekly_write_up_uses_the_show_date_and_friday_close(db_engine):
    """bourse-portefeuille-conseil.fr publishes « 16/010/2026 – C'est Votre Argent » on Monday 19/10: the
    show (Friday 16/10, after the close) is the reference, so the Friday close is the price of reference."""
    from app.db.models import MediaMention, NewsItem
    from app.db.session import db_session
    from app.scheduler.jobs.news_scan import _norm_title
    from app.services.media import broadcast_day, extract_mentions

    pub = datetime(2026, 10, 19, 8, tzinfo=UTC)
    assert broadcast_day("16/010/2026 – C’est Votre Argent", pub) == date(2026, 10, 16)
    assert broadcast_day("01/01/2026 – C’est Votre Argent", pub) is None  # too old for this write-up
    assert "c'est votre argent" in _norm_title("16/010/2026 – C’est Votre Argent")
    out = {"values": [{"name": "TotalEnergies", "stance": "vente", "speaker": "Frédéric Rozier",
                       "snippet": "Frédéric Rozier vend TotalEnergies"}]}  # fmt: skip
    with db_session() as s:
        s.add(_bar(ISIN, date(2026, 10, 15), 59.0))
        s.add(_bar(ISIN, date(2026, 10, 16), 60.0))
        row = NewsItem(source="cva_portefeuille_gerants", guid="w42", dedup_hash="w42", url="u",
                       title="16/010/2026 – C’est Votre Argent", body="Frédéric Rozier vend TotalEnergies.",
                       fetched_at=pub, published_at=pub)  # fmt: skip
        s.add(row)
        s.flush()
        extract_mentions(s, [row], Gateway(out), {"totalenergies": ISIN}, {"broadcast_after_close": True})
        m = s.query(MediaMention).one()
        assert (m.price_ref, m.price_ref_day) == (60.0, date(2026, 10, 16))  # Friday's close, not Thursday's
        assert m.published_at.replace(tzinfo=UTC).date() == date(2026, 10, 16)


def test_excerpt_only_feed_reads_the_article_page_and_retries_when_unreadable(db_engine):
    """The site's feed stops at « Achat […] »: the values are read on the article page (same site only);
    an unreadable page leaves the item for the next cycle, never extracted from the excerpt."""
    from app.data.providers.rss import strip_html
    from app.db.models import MediaMention, NewsItem
    from app.db.session import db_session
    from app.services.media import extract_mentions

    assert strip_html("qu&#8217;il n&#8217;y aura") == "qu’il n’y aura"
    out = {"values": [{"name": "TotalEnergies", "stance": "achat", "speaker": "Julie Jourdan",
                       "snippet": "Achat TotalEnergies"}]}  # fmt: skip
    pub = datetime(2026, 10, 19, 8, tzinfo=UTC)
    with db_session() as s:
        row = NewsItem(source="cva_portefeuille_gerants", guid="w42", dedup_hash="w42", url="https://site/w42",
                       title="16/010/2026 – C’est Votre Argent", body="Conseils de la semaine. Achat […]",
                       fetched_at=pub, published_at=pub)  # fmt: skip
        s.add(row)
        s.flush()
        feed = {"follow_link": True, "url": "https://www.site/feed/"}
        assert extract_mentions(s, [row], Gateway(out), {"totalenergies": ISIN}, feed, fetch=lambda u, f: None) == 0
        assert "media" not in (row.classification or {})  # retried later
        seen = []
        n = extract_mentions(s, [row], Gateway(out), {"totalenergies": ISIN}, feed,
                             fetch=lambda u, f: seen.append(u) or "Julie Jourdan — Achat TotalEnergies")  # fmt: skip
        assert n == 1 and seen == ["https://site/w42"] and s.query(MediaMention).one().speaker == "Julie Jourdan"


WRITEUP_16_10_2026 = """16/010/2026 - C'est Votre Argent - Portefeuilles Boursiers & Conseils
16/010/2026 – C’est Votre Argent
par Ludovic | 19/10/2026 | C'est votre Argent ! | 0 commentaire
Conseils de mise en portefeuille cette semaine.
Julie JOURDAN
Nouveau portefeuille avec Julie JOURDAN de Amplegest. Certainement qu'il n'y aura que des actions américaines. Palo Alto est cité comme faisant parti du portefeuille mais pas montré à l'écran. Je le rajoute.
Je souhaite de bonnes sélections à Julie pour nous donner des idées d'investissements.
Achat :
Eli Lilly And Co
Booking.com
Amphenol
dynatrace
Costco
American Express
RTX
Palo Alto
Éric BLEINES
Toujours des mises à jour du portefeuilles entre les émissions. Je met les mouvements au moment où on les apprend donc le jour de sa venue.
Achat :
LDC
Nexans
Maurel et Prom
Vente :
Atos
Stellantis
Pernod Ricard
Retrouvez :
Portefeuille de Céline PIQUEMAL-PRADE
Portefeuille de Éric BLEINES
0 commentaire
Envoyer un commentaire Annuler la réponse"""  # noqa: E501 — page of 19/10/2026 read by the user


def test_manager_write_up_is_read_by_rules_with_manager_and_stance():
    from app.services.media import name_matcher, norm_name, parse_manager_writeup

    vals = parse_manager_writeup(WRITEUP_16_10_2026)
    jourdan = [v["name"] for v in vals if v["speaker"] == "Julie JOURDAN"]
    assert jourdan == ["Eli Lilly And Co", "Booking.com", "Amphenol", "dynatrace", "Costco", "American Express",
                       "RTX", "Palo Alto"]  # fmt: skip
    bleines = {(v["name"], v["stance"]) for v in vals if v["speaker"] == "Éric BLEINES"}
    assert bleines == {("LDC", "achat"), ("Nexans", "achat"), ("Maurel et Prom", "achat"), ("Atos", "vente"),
                       ("Stellantis", "vente"), ("Pernod Ricard", "vente")}  # fmt: skip
    assert len(vals) == 14 and not any("PIQUEMAL" in v["name"] for v in vals)  # « Retrouvez » ends the lists
    assert vals[-1]["snippet"] == "Éric BLEINES — Vente : Pernod Ricard"
    names = name_matcher({"nexans s.a.": "FR0000044448", "maurel & prom sa": "FR0000051070",
                          "ldc sa": "FR0013204336", "atos se": "FR0000051732", "atos": "FR0000051732",
                          "duo": "X1", "duo s.a.": "X2"})  # fmt: skip
    assert names[norm_name("Nexans")] == "FR0000044448" and names[norm_name("Maurel et Prom")] == "FR0000051070"
    assert names[norm_name("LDC")] == "FR0013204336" and names[norm_name("Atos")] == "FR0000051732"
    assert norm_name("duo") not in names  # two ISINs for one name: never guessed


def test_an_episode_is_never_marked_read_without_the_llm(db_engine):
    """26/10/2026: without the LLM (no key, budget reached) an episode was marked « analysé » with no value and
    never read again; it now stays unread and is retried at the next cycle."""
    from app.db.models import NewsItem
    from app.db.session import db_session
    from app.services.media import extract_mentions

    off = Gateway({"values": []})
    off.enabled = False
    with db_session() as s:
        now = datetime.now(UTC)
        row = NewsItem(source="cest_votre_argent", guid="ep-2", dedup_hash="ep-2", url="u", title="Émission",
                       body="J'achète TotalEnergies.", fetched_at=now, published_at=now)  # fmt: skip
        s.add(row)
        s.flush()
        assert extract_mentions(s, [row], off, {"totalenergies": ISIN}) == 0
        assert extract_mentions(s, [row], None, {"totalenergies": ISIN}) == 0
        assert "media" not in (row.classification or {}) and off.calls == 0
        on = Gateway({"values": [{"name": "TotalEnergies", "stance": "achat", "snippet": "J'achète"}]})
        assert extract_mentions(s, [row], on, {"totalenergies": ISIN}) == 1  # read once the LLM is back


def test_media_check_feed_detail_shows_filters_and_stored_episodes(db_engine, config, monkeypatch, capsys):
    from app import cli
    from app.data.providers.rss import NewsItem as Item
    from app.db.models import NewsItem
    from app.db.session import db_session

    now = datetime.now(UTC)

    def item(title, days):  # noqa: E306
        return Item("cva", title, title, "u", now - timedelta(days=days), now, "", title)

    titles = [("16/10/2026 – C'est Votre Argent", 10), ("23/10/2026 – C’est Votre Argent", 3), ("Autre", 1)]
    monkeypatch.setattr("app.data.providers.rss.fetch_feed", lambda name, url: [item(*x) for x in titles])
    feeds = {"cva": {"name": "cva", "url": "https://x/feed/", "title_includes": ["C'est Votre Argent"],
                     "follow_link": True}}  # fmt: skip
    with db_session() as s:
        s.add(NewsItem(source="cva", guid="g", dedup_hash="g", url="u", title="23/10/2026 – C’est Votre Argent",
                       body="", fetched_at=now, published_at=now,
                       classification={"media": [{"name": "Vinci", "stance": "achat", "speaker": "X"}],
                                       "media_rejetes": ["Vinci SA"]}))  # fmt: skip
    assert cli._media_feed_detail(config, feeds, "cva") == 0
    out = capsys.readouterr().out
    assert "3 élément(s) dans le flux" in out and "✗ plus de 7 j" in out and "✗ titre" in out
    assert "1 valeur(s) : Vinci (achat)" in out and "rejetés : Vinci SA" in out and "follow_link" in out
