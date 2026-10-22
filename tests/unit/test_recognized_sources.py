"""docs/23 — recognised sources: regulatory notices classified by rules, managers' purchases scored in
block C, feed discovery and check tools (addresses read from the publisher, never guessed)."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime

from app import cli
from app.data.providers.feed_discovery import discover_feeds
from app.data.providers.rss import NewsItem as RssItem
from app.llm.rules import classify_regulatory


def test_official_wordings_are_classified_without_the_llm():
    buy = classify_regulatory("EQS-DD: Sixt SE: Kauf von Aktien durch Vorstand", "")
    assert (buy.type, buy.direction, buy.magnitude, buy.confiance) == (
        "transaction_dirigeant",
        "positif",
        "moyenne",
        0.9,
    )
    sale = classify_regulatory("Déclaration des dirigeants — Société X", "Nature de la transaction : cession")
    assert (sale.type, sale.direction, sale.magnitude) == ("transaction_dirigeant", "negatif", "faible")
    unknown = classify_regulatory("Managers' transactions — Nokia Oyj", "Nature of transaction: other")
    assert unknown.direction == "neutre"
    assert classify_regulatory("Déclaration de franchissement de seuil — Y", "").type == "franchissement_seuil"
    bb = classify_regulatory("Descriptif du programme de rachat d'actions", "")
    assert (bb.type, bb.direction) == ("rachat_actions", "positif")
    assert classify_regulatory("Résultats annuels en hausse", "chiffre d'affaires record") is None
    assert classify_regulatory("EQS-DD: Z AG", "wholesale business update").direction == "neutre"  # word boundary


def test_classify_item_never_calls_the_llm_for_a_regulatory_notice(db_engine):
    from app.db.models import NewsItem
    from app.db.session import db_session
    from app.services.news import classify_item

    class Boom:
        def parse(self, *a, **k):
            raise AssertionError("LLM appelé")

    with db_session() as s:
        row = NewsItem(source="eqs_news", guid="g", dedup_hash="h", fetched_at=datetime.now(UTC),
                       title="EQS-DD: Sixt SE: purchase of shares", body="", url="u")  # fmt: skip
        cls, by, version, cost = classify_item(s, row, Boom())
    assert (cls.type, by, version, cost) == ("transaction_dirigeant", "rules", None, 0.0)


def test_a_managers_purchase_adds_points_to_block_c_with_its_explanation():
    from app.domain.scoring import ScoreInput, score

    base = dict(rs_rank=85, above_mm50_mm200=True, mm50_above_mm200=True, above_rising_mm20=True, rvol=1.0,
                clean_trigger=True, over_extended=False, resistance_within_3pct=False, stop_distance_adr=1.0,
                earnings_within_10_days=False, adv_eur=5e6, market_cap_eur=5e9, gap_pct=0.0, non_eur=False,
                regime="vert")  # fmt: skip
    without = score(ScoreInput(**base), ["T", "R", "M"], 5)
    with_buy = score(ScoreInput(**base, insider_buy_days=4, insider_buy_points=5), ["T", "R", "M"], 5)
    assert with_buy.blocks["C"] - (without.blocks["C"] or 0) == 5
    assert "achat d'un dirigeant il y a 4 j → +5" in with_buy.explanations


def test_feeds_are_discovered_from_the_publisher_page():
    html = """<html><head>
      <link rel="alternate" type="application/rss+xml" title="Toutes les news" href="/rss/news.xml">
      <link rel="stylesheet" href="/s.css"></head><body>
      <a href="https://example.org/feeds/dirigeants">Déclarations des dirigeants (RSS)</a>
      <a href="/contact">Contact</a><a href="/rss/news.xml">doublon</a></body></html>"""
    assert discover_feeds(html, "https://example.org/fr/") == [
        ("https://example.org/rss/news.xml", "Toutes les news"),
        ("https://example.org/feeds/dirigeants", "Déclarations des dirigeants (RSS)"),
    ]


def test_sources_check_reads_configured_feeds_and_skips_pending_ones(tmp_path, monkeypatch, capsys):
    import yaml

    raw = yaml.safe_load(open("config/params.example.yaml", encoding="utf-8"))
    raw["data"]["rss_feeds"] = [
        {"name": "ok", "url": "https://a.example/rss"},
        {"name": "ko", "url": "https://b.example/rss"},
        {"name": "pending", "url": "à renseigner", "homepage": "https://c.example/"},
    ]
    path = tmp_path / "params.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    calls = []

    def fake(name, url, *a, **k):
        calls.append(name)
        if name == "ko":
            raise ValueError("flux ko: HTTP 404")
        return [
            RssItem(
                source=name,
                guid="1",
                dedup_hash="h",
                title="EQS-DD: X",
                url="u",
                body="",
                published_at=datetime(2026, 10, 23, tzinfo=UTC),
                fetched_at=datetime.now(UTC),
            )
        ]  # noqa: E501

    monkeypatch.setattr("app.data.providers.rss.fetch_feed", fake)
    assert cli.cmd_sources_check(argparse.Namespace(params=str(path), name=None)) == 1
    out = capsys.readouterr().out
    assert calls == ["ok", "ko"] and "OK     ok" in out and "ÉCHEC  ko" in out and "à renseigner : pending" in out
