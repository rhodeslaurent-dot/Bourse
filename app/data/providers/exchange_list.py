"""docs/23 S3 — official list of a venue's shares when EODHD does not carry the venue (Milan, 23/10/2026):
the Euronext equities list (CSV or Excel downloaded from live.euronext.com) gives name, ISIN, symbol,
market and currency. Columns are recognised by their header words; the list is stored on the PC side
(``data/exchange_lists/<code>.yaml``) and read by ``universe_refresh`` instead of the EODHD symbol list."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from app.data.base import ProviderError
from app.data.providers.short_positions import _norm, read_table

LIST_DIR = Path("data/exchange_lists")
_ISIN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
_HEADERS = {
    "isin": ("isin",),
    "name": ("name", "nom", "denominazione", "societa"),
    "code": ("symbol", "symbole", "mnemo", "ticker", "codice"),
    "market": ("market", "marche", "mercato"),
    "currency": ("currency", "devise", "valuta"),
}


@dataclass(frozen=True)
class ListedShare:
    isin: str
    name: str
    code: str
    currency: str
    market: str | None


def _columns(header: list[object]) -> dict[str, int] | None:
    cells = [_norm(h) for h in header]
    out: dict[str, int] = {}
    for field in ("isin", "code", "market", "currency", "name"):
        for i, c in enumerate(cells):
            if i not in out.values() and c and any(c == w or c.startswith(w) for w in _HEADERS[field]):
                out[field] = i
                break
    return out if all(k in out for k in ("isin", "name", "code")) else None


def parse_list(content: bytes, filename: str = "", market_contains: str | None = None) -> list[ListedShare]:
    """Shares of the list (optionally only the rows whose market contains ``market_contains``, e.g.
    « Milan »); rows without a valid ISIN or symbol are skipped. Refused when the columns are unknown."""
    table = read_table(content, filename)
    for k, header in enumerate(table[:40]):
        cols = _columns(header)
        if cols is None:
            continue
        out: list[ListedShare] = []
        seen: set[str] = set()
        for raw in table[k + 1 :]:

            def cell(f: str, raw: list[object] = raw, cols: dict[str, int] = cols) -> str:
                return str(raw[cols[f]]).strip() if f in cols and cols[f] < len(raw) and raw[cols[f]] else ""

            isin, code, market = cell("isin").upper(), cell("code").upper(), cell("market") or None
            if not _ISIN.match(isin) or not code or isin in seen:
                continue
            if market_contains and market_contains.lower() not in (market or "").lower():
                continue
            seen.add(isin)
            out.append(ListedShare(isin, cell("name"), code, (cell("currency") or "EUR").upper(), market))
        return out
    raise ProviderError("colonnes non reconnues (nom, ISIN, symbole) : liste refusée")


def save(exchange: str, shares: list[ListedShare], source: str, directory: Path | None = None) -> Path:
    directory = directory or LIST_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{exchange}.yaml"
    doc = {
        "exchange": exchange,
        "source": source,
        "shares": [{"isin": s.isin, "name": s.name, "code": s.code, "currency": s.currency} for s in shares],
    }
    path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def load(exchange: str, directory: Path | None = None) -> list[ListedShare] | None:
    """The stored list of the venue, or None when it was never loaded."""
    path = (directory or LIST_DIR) / f"{exchange}.yaml"
    if not path.exists():
        return None
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [
        ListedShare(x["isin"], x.get("name", ""), x["code"], x.get("currency", "EUR"), None)
        for x in doc.get("shares") or []
    ]
