"""Lot S5 (step 1 of the user's plan, 15/11/2026) — SEC EDGAR, read only: the daily index of filings, the header of
a filing (acceptance time, 8-K items) and the XML of a Form 4 (managers' dealings).

Formats (EDGAR documentation, « Accessing EDGAR Data »):

- daily index ``Archives/edgar/daily-index/YYYY/QTRn/master.YYYYMMDD.idx``: a text header, then
  ``CIK|Company Name|Form Type|Date Filed|File Name`` lines (none on a day without filings: 404);
- a filing's full submission ``Archives/edgar/data/<cik>/<accession>.txt``: an ``<SEC-HEADER>`` with
  ``<ACCEPTANCE-DATETIME>YYYYMMDDHHMMSS`` (Eastern time), ``CONFORMED SUBMISSION TYPE`` and, for an 8-K, one
  ``ITEM INFORMATION:`` line per item; a Form 4 carries its ``<ownershipDocument>`` XML between ``<XML>`` tags;
- ``files/company_tickers.json``: ``{"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, …}``.

The SEC asks every automated client to declare who it is in its User-Agent and to stay under 10 requests a second
(« fair access »): the agent is the user's own setting (``data.sec.user_agent``), never written here, and requests
are paced by the caller. The name of a reporting owner is never returned (the module reads the role only).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")
BASE = "https://www.sec.gov"

# 8-K item label (``ITEM INFORMATION``, as published) → family of app.domain.filings.FAMILIES (closed list), matched
# on the start of the label (the SEC's wording of an item may be cut or slightly changed); 9.01 « Financial Statements
# and Exhibits » says nothing by itself and is left out
ITEM_FAMILY: tuple[tuple[str, str], ...] = (
    ("results of operations", "resultats"),  # 2.02
    ("entry into a material definitive agreement", "operation"),  # 1.01
    ("termination of a material definitive agreement", "operation"),  # 1.02
    ("completion of acquisition or disposition", "operation"),  # 2.01
    ("bankruptcy or receivership", "info_privilegiee"),  # 1.03
    ("cybersecurity incident", "info_privilegiee"),  # 1.05
    ("creation of a direct financial obligation", "droits_de_vote"),  # 2.03
    ("costs associated with exit or disposal", "info_privilegiee"),  # 2.05
    ("material impairment", "info_privilegiee"),  # 2.06
    ("notice of delisting", "info_privilegiee"),  # 3.01
    ("unregistered sales of equity securities", "droits_de_vote"),  # 3.02
    ("material modification to rights of security holders", "droits_de_vote"),  # 3.03
    ("changes in registrant's certifying accountant", "gouvernance"),  # 4.01
    ("non-reliance on previously issued financial statements", "info_privilegiee"),  # 4.02
    ("changes in control of registrant", "opa"),  # 5.01
    ("departure of directors or certain officers", "gouvernance"),  # 5.02
    ("amendments to articles of incorporation", "gouvernance"),  # 5.03
    ("submission of matters to a vote", "gouvernance"),  # 5.07
    ("regulation fd disclosure", "document"),  # 7.01
    ("other events", "autre"),  # 8.01
)
FAMILY_ORDER = ("resultats", "opa", "operation", "info_privilegiee", "droits_de_vote", "gouvernance", "document",
                "autre")  # fmt: skip


@dataclass(frozen=True)
class IndexRow:
    cik: int
    company: str
    form: str
    day: date
    path: str  # edgar/data/<cik>/<accession>.txt

    @property
    def accession(self) -> str:
        return self.path.rsplit("/", 1)[-1].removesuffix(".txt")


def index_url(d: date) -> str:
    q = (d.month - 1) // 3 + 1
    return f"{BASE}/Archives/edgar/daily-index/{d.year}/QTR{q}/master.{d:%Y%m%d}.idx"


def parse_master_index(text: str) -> list[IndexRow]:
    """The filing lines of a daily ``master.idx`` (header and malformed lines skipped)."""
    out = []
    for line in text.splitlines():
        parts = line.split("|")
        if len(parts) != 5 or not parts[0].strip().isdigit():
            continue
        cik, company, form, filed, path = (p.strip() for p in parts)
        try:
            d = date.fromisoformat(filed) if "-" in filed else datetime.strptime(filed, "%Y%m%d").date()
        except ValueError:
            continue
        if path.startswith("edgar/"):
            out.append(IndexRow(int(cik), company, form.upper(), d, path))
    return out


@dataclass(frozen=True)
class Header:
    accession: str | None
    form: str | None
    accepted_at: datetime | None  # UTC
    items: tuple[str, ...]  # 8-K item labels, as published


_ACCEPT = re.compile(r"<ACCEPTANCE-DATETIME>\s*(\d{14})")
_FIELD = re.compile(r"^\s*([A-Z][A-Z \-]+):\s*(.+?)\s*$", re.M)


def parse_header(text: str) -> Header:
    """The ``<SEC-HEADER>`` of a full submission: accession, form, acceptance time (Eastern → UTC), 8-K items."""
    head = text.split("</SEC-HEADER>", 1)[0]
    m = _ACCEPT.search(head)
    accepted = None
    if m:
        try:
            accepted = datetime.strptime(m.group(1), "%Y%m%d%H%M%S").replace(tzinfo=NEW_YORK).astimezone(UTC)
        except ValueError:
            accepted = None
    acc = form = None
    items: list[str] = []
    for k, v in _FIELD.findall(head):
        if k == "ACCESSION NUMBER":
            acc = v
        elif k == "CONFORMED SUBMISSION TYPE":
            form = v.upper()
        elif k == "ITEM INFORMATION":
            items.append(v)
    return Header(acc, form, accepted, tuple(items))


def family_of_items(items: tuple[str, ...]) -> str | None:
    """The most telling family of an 8-K's items (results before M&A before…); None when only exhibits."""
    fams = {next((f for pre, f in ITEM_FAMILY if i.strip().lower().startswith(pre)), None) for i in items} - {None}
    return next((f for f in FAMILY_ORDER if f in fams), None)


# --- Form 4 -----------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Transaction:
    code: str  # P open-market purchase, S open-market sale, A grant, M exercise, F tax withholding…
    day: date | None
    shares: float | None
    price: float | None
    acquired: bool | None  # A (acquired) / D (disposed)


@dataclass
class Form4:
    issuer_cik: int | None
    symbol: str | None
    issuer_name: str | None
    role: str | None  # closed list: dirigeant, administrateur, actionnaire_10pct, autre (never a name)
    plan_10b5_1: bool | None
    transactions: list[Transaction] = field(default_factory=list)

    def open_market(self) -> tuple[float, float, float, date | None, date | None, int]:
        """(bought amount, sold amount, net shares, first day, last day, n) of the P and S transactions."""
        buy = sell = net = 0.0
        days = [t.day for t in self.transactions if t.code in ("P", "S") and t.day]
        n = 0
        for t in self.transactions:
            if t.code not in ("P", "S") or not t.shares:
                continue
            n += 1
            amt = t.shares * (t.price or 0.0)
            if t.code == "P":
                buy, net = buy + amt, net + t.shares
            else:
                sell, net = sell + amt, net - t.shares
        return buy, sell, net, min(days) if days else None, max(days) if days else None, n


def _xml_block(text: str) -> str | None:
    m = re.search(r"<XML>\s*(.*?)\s*</XML>", text, re.S | re.I)
    body = m.group(1) if m else (text if text.lstrip().startswith("<?xml") or "<ownershipDocument" in text else None)
    if body is None or "<!DOCTYPE" in body or "<!ENTITY" in body:  # no entity is ever expanded
        return None
    return body


def _txt(el: ET.Element | None, path: str) -> str | None:
    if el is None:
        return None
    x = el.find(path)
    if x is None or x.text is None:
        return None
    return x.text.strip() or None


def _num(v: str | None) -> float | None:
    try:
        return float(v) if v is not None else None
    except ValueError:
        return None


def _flag(v: str | None) -> bool:
    return (v or "").strip().lower() in ("1", "true")


def parse_form4(text: str) -> Form4 | None:
    """The Form 4 of a full submission (or its bare XML); None when absent or unreadable. The reporting owner's
    name and identifiers are not read: only the role, as a closed list."""
    body = _xml_block(text)
    if body is None:
        return None
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return None
    if root.tag != "ownershipDocument":
        return None
    issuer = root.find("issuer")
    rel = root.find("reportingOwner/reportingOwnerRelationship")
    role = None
    if rel is not None:
        if _flag(_txt(rel, "isOfficer")):
            role = "dirigeant"
        elif _flag(_txt(rel, "isDirector")):
            role = "administrateur"
        elif _flag(_txt(rel, "isTenPercentOwner")):
            role = "actionnaire_10pct"
        else:
            role = "autre"
    plan = _txt(root, "aff10b5One")
    cik = _txt(issuer, "issuerCik")
    symbol = (_txt(issuer, "issuerTradingSymbol") or "").upper() or None
    out = Form4(int(cik) if cik and cik.isdigit() else None, symbol, _txt(issuer, "issuerName"), role,
                _flag(plan) if plan is not None else None)  # fmt: skip
    for tx in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        d = _txt(tx, "transactionDate/value")
        try:
            day = date.fromisoformat(d[:10]) if d else None
        except ValueError:
            day = None
        ad = _txt(tx, "transactionAmounts/transactionAcquiredDisposedCode/value")
        out.transactions.append(Transaction(
            (_txt(tx, "transactionCoding/transactionCode") or "").upper(), day,
            _num(_txt(tx, "transactionAmounts/transactionShares/value")),
            _num(_txt(tx, "transactionAmounts/transactionPricePerShare/value")),
            None if ad is None else ad.upper() == "A",
        ))  # fmt: skip
    return out


def parse_company_tickers(data: object) -> dict[int, str]:
    """CIK → ticker of ``company_tickers.json`` (the first ticker of a CIK wins: the common share)."""
    out: dict[int, str] = {}
    rows = data.values() if isinstance(data, dict) else []
    for r in rows:
        if not isinstance(r, dict):
            continue
        cik, t = r.get("cik_str"), r.get("ticker")
        if isinstance(cik, int) and isinstance(t, str) and t and cik not in out:
            out[cik] = t.upper()
    return out
