"""docs/19 §19.6 (A19.5–A19.7): « Lire la suite » pages — allow-list at every hop, subscriber
pages reported, main text appended to the letter."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from app.data.providers.imap_gmail import NewsletterMail
from app.data.providers.linked_page import check_url, enrich_mail, fetch_linked, find_link, main_text

MAIL_HTML = """<html><body><p>Momentum : trois valeurs sous surveillance…</p>
<a href="https://track.zbmail.fr/c/abc"><span><b>Lire la suite</b></span></a>
<a href="https://www.zonebourse.com/desabonnement">Se désabonner</a></body></html>"""

ARTICLE = (
    """<html><head><script>var x=1</script></head><body><nav>Menu Accueil Bourse</nav>
<article><h1>Momentum Europe</h1><p>ASML (NL0010273215) : achat, entrée à 640, objectif 700, stop 600.</p>
<p>"""
    + ("Analyse détaillée du titre. " * 20)
    + """</p></article><footer>Mentions légales</footer></body></html>"""
)

RULE = {
    "source": "zonebourse",
    "link_texts": ["Lire la suite"],
    "allowed_domains": ["zonebourse.com"],
    "tracking_domains": ["zbmail.fr"],
}


def client(routes: dict[str, httpx.Response], seen: list[str]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return routes.get(str(request.url), httpx.Response(404))

    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


def test_find_link_and_url_checks():
    assert find_link(MAIL_HTML, ["lire la suite"]) == "https://track.zbmail.fr/c/abc"
    assert find_link("<a href='x'>Autre</a>", ["Lire la suite"]) is None
    assert check_url("https://www.zonebourse.com/a", ["zonebourse.com"], []) is None
    assert "schéma" in check_url("http://www.zonebourse.com/a", ["zonebourse.com"], [])
    assert "IP" in check_url("https://192.168.1.10/admin", ["zonebourse.com"], [])
    assert "non autorisé" in check_url("https://zonebourse.com.evil.io/x", ["zonebourse.com"], [])


def test_tracking_redirect_then_article_text():
    seen: list[str] = []
    c = client(
        {
            "https://track.zbmail.fr/c/abc": httpx.Response(
                302, headers={"location": "https://www.zonebourse.com/momentum/1"}
            ),
            "https://www.zonebourse.com/momentum/1": httpx.Response(200, text=ARTICLE),
        },
        seen,
    )
    page = fetch_linked("https://track.zbmail.fr/c/abc", ["zonebourse.com"], ["zbmail.fr"], client=c)
    assert page.status == "ok" and page.url == "https://www.zonebourse.com/momentum/1"
    assert "ASML (NL0010273215)" in page.text and "Menu" not in page.text and "var x" not in page.text


def test_redirect_to_unknown_domain_is_refused_without_request():
    seen: list[str] = []
    c = client(
        {"https://track.zbmail.fr/c/abc": httpx.Response(302, headers={"location": "https://evil.example/steal"})}, seen
    )
    page = fetch_linked("https://track.zbmail.fr/c/abc", ["zonebourse.com"], ["zbmail.fr"], client=c)
    assert page.status == "refuse" and "evil.example" in page.detail
    assert seen == ["https://track.zbmail.fr/c/abc"]  # the forbidden host was never called
    seen.clear()
    assert fetch_linked("https://unknown.tracker/x", ["zonebourse.com"], [], client=c).status == "refuse" and seen == []


def test_subscriber_page_is_reserved():
    seen: list[str] = []
    c = client(
        {
            "https://www.zonebourse.com/momentum/2": httpx.Response(
                302, headers={"location": "/connexion?next=/momentum/2"}
            ),
            "https://www.zonebourse.com/connexion?next=/momentum/2": httpx.Response(200, text="<p>Connectez-vous</p>"),
            "https://www.zonebourse.com/momentum/3": httpx.Response(
                200, text="<article><p>Contenu réservé aux abonnés.</p></article>"
            ),
        },
        seen,
    )
    assert fetch_linked("https://www.zonebourse.com/momentum/2", ["zonebourse.com"], client=c).status == "reserve"
    assert fetch_linked("https://www.zonebourse.com/momentum/3", ["zonebourse.com"], client=c).status == "reserve"


def test_enrich_mail_appends_page_text():
    seen: list[str] = []
    c = client(
        {
            "https://track.zbmail.fr/c/abc": httpx.Response(
                302, headers={"location": "https://www.zonebourse.com/momentum/1"}
            ),
            "https://www.zonebourse.com/momentum/1": httpx.Response(200, text=ARTICLE),
        },
        seen,
    )
    mail = NewsletterMail(
        "zonebourse",
        "<m1>",
        "Momentum",
        "noreply@zonebourse.com",
        None,
        datetime(2026, 10, 20, 8, tzinfo=UTC),
        "Momentum : trois valeurs…",
        "h1",
        MAIL_HTML,
    )
    mail, page = enrich_mail(mail, RULE, client=c)
    assert page.status == "ok" and "Contenu de la page liée" in mail.body_text and "NL0010273215" in mail.body_text
    assert mail.linked["statut"] == "ok" and mail.linked["longueur_texte"] > 200
    assert main_text("<p>court</p>") == "court"


def test_image_button_label_and_anchor_listing():
    from app.data.providers.linked_page import anchors

    html = (
        '<a href="https://www.zonebourse.com/m/9"><img src="b.png" alt="Lire la suite"></a>'
        '<a href="https://x.fr" title="Votre compte">C</a>'
    )
    assert find_link(html, ["Lire la suite"]) == "https://www.zonebourse.com/m/9"
    labels = [lab for _, lab in anchors(html)]
    assert "Lire la suite" in labels[0] and "Votre compte" in labels[1]


def test_mailjet_http_tracking_hop_and_cookie_only_to_destination():
    """Zonebourse letters go through Mailjet (http click tracking): tolerated for that hop only,
    and the session cookie is sent to zonebourse.com, never to the tracker."""
    sent: list[tuple[str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append((str(request.url), request.headers.get("cookie")))
        if request.url.host == "xgsmz.mjt.lu":
            return httpx.Response(302, headers={"location": "https://www.zonebourse.com/actualite/1"})
        return httpx.Response(200, text=ARTICLE)

    c = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    page = fetch_linked("http://xgsmz.mjt.lu/lnk/abc", ["zonebourse.com"], ["mjt.lu"], cookie="sid=1", client=c)
    assert page.status == "ok"
    assert sent == [("http://xgsmz.mjt.lu/lnk/abc", None), ("https://www.zonebourse.com/actualite/1", "sid=1")]
    assert "http" in check_url("http://www.zonebourse.com/a", ["zonebourse.com"], ["mjt.lu"])


def test_subject_filter():
    from app.scheduler.jobs.imap_poll import subject_ok

    imap = {"subject_includes": {"zonebourse": ["Momentum"]}}
    assert subject_ok(imap, "zonebourse", "Liste Momentum Europe du jour")
    assert not subject_ok(imap, "zonebourse", "Morning Meeting - Attention, fragile !")
    assert subject_ok(imap, "momentum_capital", "Lettre du jour") and subject_ok({}, "zonebourse", "x")
