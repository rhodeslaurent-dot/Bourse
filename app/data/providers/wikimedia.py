"""docs/26 — Wikipedia page views (Wikimedia REST API, official, free, no key) and the ISIN → article
mapping through Wikidata (property P946). A descriptive User-Agent is required by the Wikimedia policy.
Only daily counts are read: no page content, no editor, no personal data."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from urllib.parse import quote

import httpx

from app.data.base import ProviderError

SPARQL = "https://query.wikidata.org/sparql"
PAGEVIEWS = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/{lang}.wikipedia/all-access/user/{title}/daily/{start}/{end}"  # noqa: E501
DEFAULT_UA = "BoursePilot/1.0 (outil personnel d'aide a la decision; https://github.com/rhodeslaurent-dot/Bourse)"


@dataclass(frozen=True)
class PageViews:
    lang: str
    title: str
    views: dict[date, int]  # days returned by the API (a day absent from the answer is left absent)
    received_at: datetime


def sparql_query(isins: list[str], langs: list[str]) -> str:
    values = " ".join(f'"{i}"' for i in isins)
    opts = " ".join(
        f"OPTIONAL {{ ?a_{lg} schema:about ?item ; schema:isPartOf <https://{lg}.wikipedia.org/> ; schema:name ?t_{lg} . }}"  # noqa: E501
        for lg in langs
    )
    cols = " ".join(f"?t_{lg}" for lg in langs)
    return f"SELECT ?isin ?item {cols} WHERE {{ VALUES ?isin {{ {values} }} ?item wdt:P946 ?isin . {opts} }}"


def parse_sparql(payload: dict, langs: list[str]) -> dict[str, dict[str, str]]:
    """ISIN → {lang: article title}. An ISIN with several items keeps the first title per language."""
    out: dict[str, dict[str, str]] = {}
    for row in (payload.get("results") or {}).get("bindings") or []:
        isin = (row.get("isin") or {}).get("value")
        if not isin:
            continue
        titles = out.setdefault(isin, {})
        for lg in langs:
            t = (row.get(f"t_{lg}") or {}).get("value")
            if t and lg not in titles:
                titles[lg] = t
    return out


def wikidata_titles(
    isins: list[str], langs: list[str], client: httpx.Client, user_agent: str = DEFAULT_UA, batch: int = 50
) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for k in range(0, len(isins), batch):
        r = client.get(
            SPARQL,
            params={"query": sparql_query(isins[k : k + batch], langs), "format": "json"},
            headers={"User-Agent": user_agent, "Accept": "application/sparql-results+json"},
            timeout=60,
        )
        if r.status_code != 200:
            raise ProviderError(f"Wikidata : HTTP {r.status_code}")
        out.update(parse_sparql(r.json(), langs))
        time.sleep(1.0)  # the query service asks for one query at a time
    return out


def parse_pageviews(payload: dict) -> dict[date, int]:
    out: dict[date, int] = {}
    for it in payload.get("items") or []:
        ts = str(it.get("timestamp") or "")
        if len(ts) >= 8 and isinstance(it.get("views"), int):
            out[date(int(ts[:4]), int(ts[4:6]), int(ts[6:8]))] = it["views"]
    return out


def pageviews(
    lang: str, title: str, start: date, end: date, client: httpx.Client, user_agent: str = DEFAULT_UA
) -> PageViews | None:
    """None when the article has no data for the period (404 from the API)."""
    url = PAGEVIEWS.format(
        lang=lang, title=quote(title.replace(" ", "_"), safe=""), start=f"{start:%Y%m%d}", end=f"{end:%Y%m%d}"
    )
    r = client.get(url, headers={"User-Agent": user_agent}, timeout=30)
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        raise ProviderError(f"Wikimedia {lang}:{title} : HTTP {r.status_code}")
    return PageViews(lang, title, parse_pageviews(r.json()), datetime.now(UTC))
