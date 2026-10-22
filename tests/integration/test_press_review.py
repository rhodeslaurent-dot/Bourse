"""docs/21 — press review of a letter: links after the heading, allow-list, article meta, news items
classified, dedup by URL, macro context in the letters review dossier."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from app.data.providers.linked_page import page_meta, press_links
from app.db.models import NewsItem, NewsletterItem
from app.db.session import db_session
from app.services.press_review import ingest_press_review, macro_context

LETTER = """<html><body><p>Notre sélection du jour… <a href="https://www.capital.fr/fiche">Lire la suite</a></p>
<h3>Revue de presse</h3>
<a href="http://trk.example-mail.com/c/1">La BCE maintient ses taux</a>
<a href="https://evil.example.org/x">Article ailleurs</a>
<a href="https://www.lesechos.fr/abo">Article réservé</a>
<a href="https://www.lesechos.fr/abo">doublon</a>
<a href="https://news.example.com/unsub">Se désinscrire</a></body></html>"""

ARTICLE = (
    """<html><head><title>BCE</title><meta property="og:title" content="La BCE maintient ses taux directeurs">
<meta property="article:published_time" content="2026-10-22T09:15:00+02:00"></head>
<body><article><p>"""
    + ("La Banque centrale européenne a laissé ses taux inchangés. " * 20)
    + """</p></article></body></html>"""
)

PAYWALL = """<html><head><meta property="og:title" content="Les taux longs grimpent">
<meta property="og:description" content="Le rendement de l'OAT progresse."></head>
<body><main><p>Contenu réservé aux abonnés. Connectez-vous.</p></main></body></html>"""


def handler(req: httpx.Request) -> httpx.Response:
    if req.url.host == "trk.example-mail.com":
        return httpx.Response(302, headers={"location": "https://www.lefigaro.fr/eco/bce"})
    if req.url.host == "www.lefigaro.fr":
        return httpx.Response(200, text=ARTICLE)
    if req.url.host == "www.lesechos.fr":
        return httpx.Response(200, text=PAYWALL)
    raise AssertionError(f"requête inattendue vers {req.url}")  # evil.example.org must never be called


RULE = {
    "start_after": ["Revue de presse"],
    "exclude_texts": ["désinscri"],
    "allowed_domains": ["lefigaro.fr", "lesechos.fr"],
    "tracking_domains": ["trk.example-mail.com"],
    "max_links": 10,
}


def test_links_after_heading_only():
    links = press_links(LETTER, ["Revue de presse"], ["désinscri"], 10)
    assert [h for h, _ in links] == [
        "http://trk.example-mail.com/c/1",
        "https://evil.example.org/x",
        "https://www.lesechos.fr/abo",
    ]
    assert page_meta(ARTICLE)["titre"] == "La BCE maintient ses taux directeurs"


def test_ingest_press_review(db_engine, config):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    with db_session() as s:
        it = NewsletterItem(source="momentum_capital", message_id="m", dedup_hash="d", subject="Momentum",
                            received_at=datetime(2026, 10, 22, 11, 0, tzinfo=UTC), body_text="x")  # fmt: skip
        s.add(it)
        s.flush()
        res = ingest_press_review(s, LETTER, it, RULE, None, client=client, sleep=lambda _: None)
        assert [r["statut"] for r in res] == ["ok", "refuse", "reserve"]
        rows = {n.url: n for n in s.query(NewsItem).filter(NewsItem.source == "presse")}
        bce = rows["https://www.lefigaro.fr/eco/bce"]
        assert bce.title == "La BCE maintient ses taux directeurs" and bce.classification["date_source"] == "article"
        assert bce.classification["editeur"] == "www.lefigaro.fr" and bce.classification["portee"] == "macro"
        reserved = rows["https://www.lesechos.fr/abo"]
        assert reserved.body == "Le rendement de l'OAT progresse."  # title and lead only
        assert it.parsed["revue_presse"][1]["detail"].startswith("domaine non autorisé")
        again = ingest_press_review(s, LETTER, it, RULE, None, client=client, sleep=lambda _: None)
        assert len(again) == 3 and s.query(NewsItem).filter(NewsItem.source == "presse").count() == 2  # dedup
        ctx = macro_context(s, datetime(2026, 10, 22, 12, 0, tzinfo=UTC), 3, 10)
        assert any(a["titre"].startswith("La BCE") for a in ctx)
