"""Official yearly list of French companies subject to the financial transaction tax (BOFiP
BOI-ANNX-000467: head office in France, market cap > 1 bn € on 1 December of the previous year).
Read-only download; the ISINs found in the table rows are kept with the row's text."""

from __future__ import annotations

import re
from html.parser import HTMLParser

import httpx

from app.data.base import ProviderError
from app.data.providers.rss import BROWSER_UA

DEFAULT_URL = "https://bofip.impots.gouv.fr/bofip/ANNX-000467"
ISIN_RE = re.compile(r"\b([A-Z]{2}[A-Z0-9]{9}[0-9])\b")


class _Rows(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._row is not None and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None


def parse(html: str) -> list[tuple[str, str]]:
    """(isin, company name) for every table row holding an ISIN; the name is the longest other cell."""
    p = _Rows()
    p.feed(html or "")
    out: dict[str, str] = {}
    for row in p.rows:
        isins = [m for cell in row for m in ISIN_RE.findall(cell)]
        if not isins:
            continue
        others = [c for c in row if not ISIN_RE.search(c)]
        name = max(others, key=len) if others else ""
        for isin in isins:
            out.setdefault(isin, name)
    return sorted(out.items())


def fetch(url: str = DEFAULT_URL, client: httpx.Client | None = None) -> list[tuple[str, str]]:
    c = client or httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": BROWSER_UA})
    try:
        r = c.get(url)
        if r.status_code >= 400:
            raise ProviderError(f"BOFiP {r.status_code}")
        return parse(r.text)
    except httpx.HTTPError as exc:
        raise ProviderError(f"BOFiP indisponible : {exc.__class__.__name__}") from exc
    finally:
        if client is None:
            c.close()
