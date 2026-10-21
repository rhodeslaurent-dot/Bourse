"""Page behind a newsletter's « Lire la suite » link (docs/19 §19.6).

Safety first: only ``https`` and allow-listed hosts are requested, at every redirect hop, so a
forged mail cannot make the home PC call an arbitrary or internal address. One page per letter,
no JavaScript, main text only. A subscriber-only page is reported as ``reserve``, never guessed.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx

from app.data.providers.rss import BROWSER_UA

MAX_TEXT = 20_000
MAX_HOPS = 5
PAYWALL_MARKERS = (
    "réservé aux abonnés",
    "reserve aux abonnes",
    "contenu réservé",
    "connectez-vous",
    "abonnez-vous pour lire",
    "accès réservé",
)
LOGIN_PATH = re.compile(r"/(login|connexion|signin|abonnement|subscribe)\b", re.I)


@dataclass
class LinkedPage:
    status: str  # ok | reserve | refuse | erreur | absent
    url: str | None = None
    http_status: int | None = None
    text: str = ""
    detail: str | None = None
    fetched_at: datetime | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "statut": self.status,
            "url": self.url,
            "http": self.http_status,
            "detail": self.detail,
            "recupere_le": self.fetched_at.isoformat() if self.fetched_at else None,
            "longueur_texte": len(self.text),
        }


class _Anchors(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "a":
            self._href = a.get("href") or None
            self._text = [a.get("title") or "", a.get("aria-label") or ""]
        elif tag == "img" and self._href is not None:  # image buttons: the label is in alt / title
            self._text += [" ", a.get("alt") or "", " ", a.get("title") or ""]

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join("".join(self._text).split())))
            self._href = None


def anchors(html: str) -> list[tuple[str, str]]:
    """(href, visible label) of every link of the letter, for diagnosis."""
    p = _Anchors()
    p.feed(html or "")
    return p.links


def find_link(html: str, link_texts: list[str]) -> str | None:
    """First anchor whose visible text contains one of ``link_texts`` (case-insensitive)."""
    p = _Anchors()
    p.feed(html or "")
    wanted = [t.lower() for t in link_texts]
    for href, text in p.links:
        if any(w in text.lower() for w in wanted):
            return href
    return None


def _host_allowed(host: str, domains: list[str]) -> bool:
    host = host.lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in (x.lower().lstrip(".") for x in domains))


def check_url(url: str, allowed: list[str], tracking: list[str]) -> str | None:
    """Reason to refuse ``url`` (None when it may be requested). The destination must be ``https``;
    plain ``http`` is tolerated only for an allow-listed click-tracking hop (mailers such as
    Mailjet still emit ``http`` links), which never receives the session cookie."""
    u = urlparse(url)
    host = u.hostname or ""
    try:
        ipaddress.ip_address(host)
        return f"adresse IP refusée ({host})"
    except ValueError:
        pass
    if u.scheme not in ("https", "http"):
        return f"schéma refusé ({u.scheme or 'aucun'})"
    if not _host_allowed(host, allowed + tracking):
        return f"domaine non autorisé : {host} (à ajouter à allowed_domains ou tracking_domains si légitime)"
    if u.scheme == "http" and not _host_allowed(host, tracking):
        return f"schéma refusé (http) pour {host} : seule une redirection de suivi de clics autorisée peut être en http"
    return None


class _MainText(HTMLParser):
    SKIP = {"script", "style", "nav", "header", "footer", "noscript", "svg", "form", "aside"}
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "tr", "section", "article", "main"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: dict[str, list[str]] = {"article": [], "main": [], "body": []}
        self._skip = 0
        self._in: dict[str, int] = {"article": 0, "main": 0}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIP:
            self._skip += 1
        if tag in self._in:
            self._in[tag] += 1
        if tag in self.BLOCK:
            self._add("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        if tag in self._in and self._in[tag]:
            self._in[tag] -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self._add(data)

    def _add(self, s: str) -> None:
        self.parts["body"].append(s)
        for k, depth in self._in.items():
            if depth:
                self.parts[k].append(s)


def main_text(html: str) -> str:
    p = _MainText()
    p.feed(html or "")
    for key in ("article", "main", "body"):
        raw = "".join(p.parts[key])
        lines = [" ".join(ln.split()) for ln in raw.splitlines()]
        text = "\n".join(ln for ln in lines if ln)
        if len(text) >= 200 or key == "body":
            return text[:MAX_TEXT]
    return ""


def looks_reserved(final_url: str, text: str) -> bool:
    low = text.lower()
    if LOGIN_PATH.search(urlparse(final_url).path or ""):
        return True
    return len(text) < 1500 and any(m in low for m in PAYWALL_MARKERS)


def fetch_linked(
    url: str,
    allowed: list[str],
    tracking: list[str] | None = None,
    cookie: str | None = None,
    client: httpx.Client | None = None,
    timeout: float = 15.0,
) -> LinkedPage:
    """Follow ``url`` hop by hop (each hop checked) and return the page's main text."""
    tracking = tracking or []
    now = datetime.now(UTC)
    headers = {"User-Agent": BROWSER_UA, "Accept": "text/html"}
    c = client or httpx.Client(timeout=timeout, follow_redirects=False)
    current = url
    try:
        for _ in range(MAX_HOPS + 1):
            why = check_url(current, allowed, tracking)
            if why:
                return LinkedPage("refuse", current, detail=why, fetched_at=now)
            hop_headers = dict(headers)
            u = urlparse(current)
            if cookie and u.scheme == "https" and _host_allowed(u.hostname or "", allowed):
                hop_headers["Cookie"] = cookie  # the session cookie only ever goes to the destination site
            r = c.get(current, headers=hop_headers)
            if r.is_redirect and r.headers.get("location"):
                current = urljoin(current, r.headers["location"])
                continue
            host = urlparse(current).hostname or ""
            if not _host_allowed(host, allowed):
                return LinkedPage(
                    "refuse", current, r.status_code, detail=f"destination hors {allowed}", fetched_at=now
                )
            if r.status_code in (401, 402, 403):
                return LinkedPage("reserve", current, r.status_code, detail="accès refusé", fetched_at=now)
            if r.status_code >= 400:
                return LinkedPage("erreur", current, r.status_code, detail=f"HTTP {r.status_code}", fetched_at=now)
            text = main_text(r.text)
            if looks_reserved(current, text):
                return LinkedPage("reserve", current, r.status_code, detail="page réservée aux abonnés", fetched_at=now)
            return LinkedPage("ok", current, r.status_code, text=text, fetched_at=now)
        return LinkedPage("erreur", current, detail="trop de redirections", fetched_at=now)
    except httpx.HTTPError as exc:
        return LinkedPage("erreur", current, detail=exc.__class__.__name__, fetched_at=now)
    finally:
        if client is None:
            c.close()


def enrich_mail(mail, rule: dict, cookie: str | None = None, client: httpx.Client | None = None):  # type: ignore[no-untyped-def]
    """Fetch the linked page of ``mail`` per ``rule`` (one ``data.imap.follow_links`` entry).
    Returns ``(mail, page)``: a copy of the letter with the page text appended (when fetched) and
    ``linked`` set, and the LinkedPage."""
    from dataclasses import replace

    href = find_link(mail.html, list(rule.get("link_texts") or ["Lire la suite"]))
    if not href:
        page = LinkedPage("absent", detail="aucun lien « " + "/".join(rule.get("link_texts") or []) + " »")
    else:
        page = fetch_linked(
            href,
            list(rule.get("allowed_domains") or []),
            list(rule.get("tracking_domains") or []),
            cookie=cookie,
            client=client,
            timeout=float(rule.get("timeout_s", 15)),
        )
    body = mail.body_text
    if page.status == "ok" and page.text:
        body = f"{body}\n\n--- Contenu de la page liée ({page.url}) ---\n{page.text}"
    return replace(mail, body_text=body, linked=page.as_dict()), page
