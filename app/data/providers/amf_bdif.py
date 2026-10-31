"""docs/34 §34.8 — managers' dealing declarations published by the AMF (BDIF, MAR art. 19), read-only.

Source: the public JSON listing the BDIF site itself calls (no key, no account)::

    GET https://bdif.amf-france.org/back/api/v1/informations
        ?TypesInformation=DD&DateDebut=<ISO UTC>&DateFin=<ISO UTC>&From=<offset>&Size=<n>
    → {"result": [{"numero": "2026DD1099088", "datePublication": "2026-03-06T18:08:10.927+01:00",
                   "dateInformation": "2026-03-06T00:00:00",
                   "societes": [{"jeton": "RS00002627", "raisonSociale": "…"}],
                   "documents": [{"path": "2026/2026DD1099088/<sha>.pdf"}]}, …]}
    GET https://bdif.amf-france.org/back/api/v1/documents/<path>   → the declaration PDF

The listing carries no figure: role, nature, date, price, volume and venue are only in the PDF (AMF form, labels
« NATURE DE LA TRANSACTION », « PRIX UNITAIRE », « VOLUME », « DATE DE LA TRANSACTION », « LIEU DE LA
TRANSACTION »…). The PDF is read only when a text extractor is available (``pypdf``, optional); otherwise the
PDF address is kept and the figures stay empty — never guessed (rule 2).

PRIVACY: the declarant's name is never kept. The « NOM /FONCTION » field is reduced to a role from a closed list
(``ROLES``) and a « personne liée » flag; the raw text is dropped as soon as it is classified.
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from app.data.base import ProviderUnavailable

SOURCE = "amf_bdif"
BASE_URL = "https://bdif.amf-france.org"
LIST_PATH = "/back/api/v1/informations"
DOC_PATH = "/back/api/v1/documents/"
UA = "bourse-pilot/1.0 (outil personnel, archivage quotidien, 1 requete/s)"
PARIS = ZoneInfo("Europe/Paris")
MAX_OFFSET = 10_000  # the listing pages no further than the 10 000th item: callers split long windows

Fetch = Callable[[str, dict[str, Any] | None], bytes]
PdfText = Callable[[bytes], str | None]

_ISIN = re.compile(r"\b([A-Z]{2}[A-Z0-9]{9}[0-9])\b")

# closed lists (docs/13): what is stored, never the raw text
ROLES = (
    "PDG", "directeur général", "directeur général délégué", "président du directoire", "membre du directoire",
    "président du conseil", "membre du conseil de surveillance", "administrateur", "gérant", "directeur financier",
    "président", "autre dirigeant", "personne liée", "non reconnu",
)  # fmt: skip
NATURES = ("acquisition", "cession", "souscription", "autre", "mixte")
PARSE_STATUSES = ("listing_only", "parsed", "pdf_unavailable", "pdf_unrecognised")


class DealingTransaction(BaseModel):
    nature: str | None = None  # NATURES (without « mixte »)
    nature_detail: str | None = None  # the form's nature wording (no personal data), truncated
    instrument: str | None = None  # « Action », « Option »… as declared, truncated
    isin: str | None = None
    tx_date: date | None = None
    price: float | None = None
    currency: str | None = None
    volume: float | None = None
    venue: str | None = None


class DealingDeclaration(BaseModel):
    source: str = SOURCE
    doc_id: str
    published_at: datetime  # UTC: when the AMF made it public (the date of availability)
    info_date: date | None = None
    issuer_name: str | None = None
    issuer_token: str | None = None  # AMF company token (RS…), public
    isin: str | None = None  # only when every transaction names the same valid ISIN
    role: str | None = None  # ROLES
    related_role: str | None = None  # for a « personne liée »: the role of the manager it is linked to
    linked_person: bool | None = None
    nature: str | None = None  # NATURES
    nature_detail: str | None = None
    tx_date: date | None = None
    tx_date_last: date | None = None
    price: float | None = None  # volume-weighted, only when nature and currency are common to all transactions
    currency: str | None = None
    volume: float | None = None
    amount: float | None = None
    venue: str | None = None
    n_transactions: int | None = None
    transactions: list[DealingTransaction] = []
    pdf_url: str | None = None
    parse_status: str = "listing_only"  # PARSE_STATUSES


@dataclass
class Listing:
    declarations: list[DealingDeclaration] = field(default_factory=list)
    skipped: int = 0  # items without number or publication date
    truncated: bool = False  # the 10 000-item paging limit was reached: split the window
    pdf_read: int = 0
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------------------------------------------
# text helpers


def _plain(s: object) -> str:
    text = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return " ".join(text.replace("’", "'").split())


def parse_number(value: str | None) -> float | None:
    """« 1 960,5000 Euro », « 1960.5 », « 12 345 » → float; None when no number is readable."""
    if not value:
        return None
    m = re.search(r"-?\d[\d \t  .,']*", value)
    if not m:
        return None
    txt = re.sub(r"[\s  ']", "", m.group(0)).rstrip(".,")
    if "," in txt and "." in txt:  # the last separator is the decimal one
        txt = txt.replace(".", "").replace(",", ".") if txt.rfind(",") > txt.rfind(".") else txt.replace(",", "")
    elif "," in txt:
        txt = txt.replace(",", ".")
    try:
        return float(txt)
    except ValueError:
        return None


_CURRENCIES = {"euro": "EUR", "euros": "EUR", "eur": "EUR", "dollar": "USD", "usd": "USD", "livre": "GBP",
               "gbp": "GBP", "franc suisse": "CHF", "chf": "CHF"}  # fmt: skip


def parse_currency(value: str | None) -> str | None:
    words = _plain(value)
    for k, v in _CURRENCIES.items():
        if re.search(r"\b" + re.escape(k) + r"\b", words):
            return v
    return None


_MONTHS = {"janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6, "juillet": 7, "aout": 8,
           "septembre": 9, "octobre": 10, "novembre": 11, "decembre": 12}  # fmt: skip


def parse_date(value: str | None) -> date | None:
    """« 2026-03-04 +01:00 », « 04/03/2026 », « 4 mars 2026 » → date."""
    v = _plain(value)
    if m := re.search(r"(\d{4})-(\d{2})-(\d{2})", v):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    elif m := re.search(r"(\d{1,2})[/.](\d{1,2})[/.](\d{4})", v):
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    elif m := re.search(r"(\d{1,2})(?:er)? ([a-z]+) (\d{4})", v):
        mo_ = _MONTHS.get(m.group(2))
        if mo_ is None:
            return None
        d, mo, y = int(m.group(1)), mo_, int(m.group(3))
    else:
        return None
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def classify_role(text: str | None) -> tuple[str | None, str | None, bool | None]:
    """(role, related_role, linked) from the « NOM /FONCTION » field. The name before the first comma is never
    looked at for the function; a « personne (étroitement) liée » gets role « personne liée » and, when given,
    the role of the manager it is linked to."""
    plain = _plain(text)
    if not plain:
        return None, None, None
    linked = bool(re.search(r"\blie(e|es|s)?\b|closely associated", plain))
    func = plain.split(",", 1)[1] if "," in plain else plain
    role = _function(func)
    if linked:
        return "personne liée", (role if role != "non reconnu" else None), True
    return role, None, False


def _function(t: str) -> str:
    t = t.replace("-", " ")
    rules: tuple[tuple[str, str], ...] = (
        (r"\bpdg\b|president directeur general|chairman and (chief executive|ceo)", "PDG"),
        (r"directeur general delegue|deputy (chief executive|ceo)", "directeur général délégué"),
        (r"directeur general|\bceo\b|chief executive", "directeur général"),
        (r"president du directoire", "président du directoire"),
        (r"membre du directoire", "membre du directoire"),
        (r"president du conseil|chairman", "président du conseil"),
        (r"conseil de surveillance", "membre du conseil de surveillance"),
        (r"administrat|board member|\bdirector\b", "administrateur"),
        (r"\bgerant", "gérant"),
        (r"directeur financier|\bcfo\b|chief financial", "directeur financier"),
        (r"\bpresident", "président"),
        (r"directeur|dirigeant|comite executif|comex|executive|responsable|secretaire general", "autre dirigeant"),
    )
    for pat, role in rules:
        if re.search(pat, t):
            return role
    return "non reconnu"


def classify_nature(text: str | None) -> str | None:
    t = _plain(text)
    if not t:
        return None
    if re.search(r"\bacquisition|\bachat", t):
        return "acquisition"
    if re.search(r"\bcession|\bvente", t):
        return "cession"
    if "souscription" in t:
        return "souscription"
    return "autre"


# ---------------------------------------------------------------------------------------------------------------
# PDF text (AMF form)

_LABELS = {
    # the real form writes this label on two lines (« … OU DE LA » / « PERSONNE ETROITEMENT LIEE : ») — the PC
    # archive of 31/10/2026 had no role read on any of its 24 393 declarations: the label may span lines
    "who": r"NOM[ \t]*/[ \t]*FONCTION[^:]*",
    "instrument": r"DESCRIPTION DE L.INSTRUMENT FINANCIER",
    "isin": r"CODE D.IDENTIFICATION[^:\n]*",
    "nature": r"NATURE DE LA TRANSACTION",
    "unit_price": r"PRIX UNITAIRE",
    "volume": r"VOLUME",
    "tx_date": r"DATE DE LA TRANSACTION",
    "venue": r"LIEU DE LA TRANSACTION",
}


_HEADER = re.compile(
    r"(?i)^(INFORMATIONS? (DETAILLEE|AGREGEE)|NOTIFICATION|COORDONNEES|DESCRIPTION|DATE DE|LIEU DE|COMMENTAIRE)"
)


def _value(block: str, label: str) -> str | None:
    """Text after « LABEL : » on its line, or on the next non-empty line when the extractor broke it."""
    m = re.search(r"(?im)^[ \t]*" + label + r"[ \t]*:[ \t]*(.*)$", block)
    if not m:
        return None
    val = m.group(1).strip()
    if val:
        return val
    nxt = next((ln.strip() for ln in block[m.end() :].split("\n") if ln.strip()), "")
    return nxt if nxt and ":" not in nxt and not _HEADER.match(nxt) else None  # never another label's line


def _values(block: str, label: str) -> list[str]:
    return [m.group(1).strip() for m in re.finditer(r"(?im)^[ \t]*" + label + r"[ \t]*:[ \t]*(.*)$", block)]


def _transaction(block: str) -> DealingTransaction:
    nature_txt = _value(block, _LABELS["nature"])
    instrument = _value(block, _LABELS["instrument"])
    isin_txt = _value(block, _LABELS["isin"]) or ""
    isin = m.group(1) if (m := _ISIN.search(isin_txt.upper().replace(" ", ""))) else None
    agg = re.split(r"(?i)INFORMATIONS? AGR[EÉ]G[EÉ]ES?", block, maxsplit=1)
    price: float | None
    currency: str | None
    volume: float | None
    if len(agg) == 2 and _value(agg[1], _LABELS["volume"]):
        p_txt = _value(agg[1], r"PRIX(?! UNITAIRE)")
        price, volume = parse_number(p_txt), parse_number(_value(agg[1], _LABELS["volume"]))
        currency = parse_currency(p_txt)
    else:  # detailed lines only: volume-weighted when every line is readable
        prices = _values(block, _LABELS["unit_price"])
        vols = [parse_number(v) for v in _values(block, _LABELS["volume"])]
        pv = [parse_number(p) for p in prices]
        currency = parse_currency(prices[0]) if prices else None
        if prices and len(pv) == len(vols) and all(x is not None for x in pv + vols):
            tot = sum(v for v in vols if v is not None)
            amt = sum(p * v for p, v in zip(pv, vols, strict=True) if p is not None and v is not None)
            price, volume = (amt / tot if tot else None), tot
        else:
            price, volume = None, None
    venue = _value(block, _LABELS["venue"])
    return DealingTransaction(
        nature=classify_nature(nature_txt),
        nature_detail=(nature_txt or None) and nature_txt[:80],
        instrument=(instrument or None) and instrument[:40],
        isin=isin,
        tx_date=parse_date(_value(block, _LABELS["tx_date"])),
        price=price,
        currency=currency,
        volume=volume,
        venue=(venue or None) and venue[:64],
    )


_TX_LABELS = ("instrument", "isin", "nature", "tx_date", "venue")


def _tx_anchor(text: str) -> str:
    """The label that opens each transaction: whichever transaction label the form writes first. The real AMF
    PDFs, as their text is extracted, put the nature before the instrument (PC archive of 30/10/2026: the split on
    the instrument left every single-transaction nature outside its block — 22 237 natures empty, 2 111 shifted
    into « mixte »)."""
    best: tuple[int, str] | None = None
    for key in _TX_LABELS:
        m = re.search(r"(?im)^[ \t]*" + _LABELS[key], text)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), _LABELS[key])
    return best[1] if best else _LABELS["instrument"]


def parse_declaration_text(text: str) -> dict[str, Any] | None:
    """Fields of one AMF declaration from its PDF text; None when the form is not recognised. The declarant's
    name is read only to be dropped (``classify_role``)."""
    if not text or not re.search(r"(?i)NATURE DE LA TRANSACTION", text):
        return None
    parts = re.split(r"(?im)^(?=[ \t]*" + _tx_anchor(text) + ")", text)
    blocks = parts[1:] or [text]  # a form whose blocks do not start with the instrument: one transaction
    role, related, linked = classify_role(_value(parts[0], _LABELS["who"]) or _value(text, _LABELS["who"]))
    txs = [_transaction(b) for b in blocks]
    out: dict[str, Any] = {
        "role": role, "related_role": related, "linked_person": linked, "transactions": txs,
        "n_transactions": len(txs), "price": None, "currency": None, "volume": None, "amount": None,
    }  # fmt: skip
    natures = {t.nature for t in txs}
    out["nature"] = next(iter(natures)) if len(natures) == 1 else "mixte"
    details = {t.nature_detail for t in txs}
    out["nature_detail"] = next(iter(details)) if len(details) == 1 else None
    dates = [t.tx_date for t in txs if t.tx_date is not None]
    out["tx_date"], out["tx_date_last"] = (min(dates), max(dates)) if dates else (None, None)
    isins = {t.isin for t in txs}
    out["isin"] = next(iter(isins)) if len(isins) == 1 else None
    venues = {t.venue for t in txs}
    out["venue"] = next(iter(venues)) if len(venues) == 1 else None
    currencies = {t.currency for t in txs}
    figures = all(t.price is not None and t.volume is not None for t in txs)
    if len(natures) == 1 and len(currencies) == 1 and figures:
        vol = sum(t.volume or 0.0 for t in txs)
        amt = sum((t.price or 0.0) * (t.volume or 0.0) for t in txs)
        out.update(volume=vol, amount=amt, price=(amt / vol if vol else None), currency=next(iter(currencies)))
    return out


def pypdf_text(content: bytes) -> str | None:
    """Text of a PDF with ``pypdf`` when it is installed (optional); None otherwise or on an unreadable file."""
    try:
        import io

        from pypdf import PdfReader  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        reader = PdfReader(io.BytesIO(content))
        return "\n".join((p.extract_text() or "") for p in reader.pages)
    except Exception:  # noqa: BLE001 — a damaged PDF is « illisible », never a guess
        return None


def pdf_extractor_available() -> bool:
    try:
        import pypdf  # type: ignore[import-not-found]  # noqa: F401
    except ImportError:
        return False
    return True


# ---------------------------------------------------------------------------------------------------------------
# listing


def window_params(start: date, end: date, offset: int, size: int) -> dict[str, Any]:
    """Paris days ``start``..``end`` (inclusive) as the UTC bounds the listing expects."""
    lo = datetime.combine(start, datetime.min.time(), PARIS).astimezone(UTC)
    hi = (datetime.combine(end + timedelta(days=1), datetime.min.time(), PARIS) - timedelta(seconds=1)).astimezone(UTC)
    return {
        "TypesInformation": "DD",
        "DateDebut": lo.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "DateFin": hi.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "From": offset,
        "Size": size,
    }


def _ts(value: object) -> datetime | None:
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (ts if ts.tzinfo else ts.replace(tzinfo=PARIS)).astimezone(UTC)


def _items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    if isinstance(payload.get("result"), list):
        return [x for x in payload["result"] if isinstance(x, dict)]
    hits = payload.get("hits")
    if isinstance(hits, dict) and isinstance(hits.get("hits"), list):  # older search-engine shape
        return [h.get("_source", h) for h in hits["hits"] if isinstance(h, dict)]
    return []


def _pdf_path(item: dict[str, Any]) -> str | None:
    docs = [d for d in item.get("documents") or [] if isinstance(d, dict) and str(d.get("path", "")).endswith(".pdf")]
    fr = [d for d in docs if _plain(d.get("langue") or d.get("lang") or "fr").startswith("fr")]
    pick = (fr or docs or [None])[0]
    return str(pick["path"]) if pick else None


def parse_listing(payload: Any, base_url: str = BASE_URL) -> tuple[list[DealingDeclaration], int]:
    """(declarations, items skipped) from one listing page; only the issuer, never a person, is read."""
    out: list[DealingDeclaration] = []
    skipped = 0
    for it in _items(payload):
        num = str(it.get("numero") or "").strip()
        ts = _ts(it.get("datePublication") or it.get("dateMiseEnLigne") or "")
        if not num or ts is None:
            skipped += 1
            continue
        socs = [x for x in it.get("societes") or [] if isinstance(x, dict)]
        soc = socs[0] if socs else {}
        info = _ts(it.get("dateInformation") or "")
        path = _pdf_path(it)
        out.append(
            DealingDeclaration(
                doc_id=num[:32],
                published_at=ts,
                info_date=info.astimezone(PARIS).date() if info else None,
                issuer_name=(str(soc.get("raisonSociale") or "").strip() or None),
                issuer_token=(str(soc.get("jeton") or "").strip()[:16] or None),
                pdf_url=f"{base_url}{DOC_PATH}{path.lstrip('/')}" if path else None,
            )
        )
    return out, skipped


def http_fetch(timeout: float = 30.0) -> Fetch:
    import httpx

    client = httpx.Client(timeout=timeout, follow_redirects=True, headers={"User-Agent": UA})

    def get(url: str, params: dict[str, Any] | None) -> bytes:
        try:
            r = client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"AMF BDIF injoignable : {exc.__class__.__name__}") from exc
        if r.status_code >= 400:
            raise ProviderUnavailable(f"AMF BDIF HTTP {r.status_code} sur {url}")
        return r.content

    return get


class _Throttle:
    def __init__(self, interval_s: float, sleep: Callable[[float], None]) -> None:
        self.interval = interval_s
        self.sleep = sleep
        self.last: float | None = None

    def wait(self) -> None:
        now = time.monotonic()
        if self.last is not None and self.interval > 0:
            gap = self.interval - (now - self.last)
            if gap > 0:
                self.sleep(gap)
        self.last = time.monotonic()


def fetch_declarations(
    start: date,
    end: date,
    fetch: Fetch | None = None,
    page_size: int = 100,
    interval_s: float = 1.0,
    pdf_text: PdfText | None = None,
    skip_pdf: Iterable[str] = (),
    sleep: Callable[[float], None] = time.sleep,
    base_url: str = BASE_URL,
) -> Listing:
    """Declarations published between ``start`` and ``end`` (Paris days), PDFs read with ``pdf_text`` when given
    (not for the ids in ``skip_pdf``). At least ``interval_s`` between two requests. Raises ProviderUnavailable
    when the listing cannot be read; a PDF that cannot be read only marks its declaration."""
    get = fetch or http_fetch()
    throttle = _Throttle(max(interval_s, 0.0), sleep)
    res = Listing()
    offset = 0
    while True:
        throttle.wait()
        raw = get(base_url + LIST_PATH, window_params(start, end, offset, page_size))
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ProviderUnavailable("AMF BDIF : réponse non JSON") from exc
        page, skipped = parse_listing(payload, base_url)
        res.declarations.extend(page)
        res.skipped += skipped
        n = len(_items(payload))
        offset += n
        if n < page_size:
            break
        if offset >= MAX_OFFSET:
            res.truncated = True
            res.notes.append(f"limite de {MAX_OFFSET} éléments atteinte : fenêtre {start}–{end} à découper")
            break
    unique: dict[str, DealingDeclaration] = {}
    for d in res.declarations:  # an item repeated across two pages is kept once
        unique.setdefault(d.doc_id, d)
    res.declarations = list(unique.values())
    if pdf_text is None:
        return res
    skip = set(skip_pdf)
    for d in res.declarations:
        if d.doc_id in skip or not d.pdf_url:
            continue
        throttle.wait()
        try:
            content = get(d.pdf_url, None)
        except ProviderUnavailable:
            d.parse_status = "pdf_unavailable"
            continue
        text = pdf_text(content)
        fields = parse_declaration_text(text or "")
        res.pdf_read += 1
        if fields is None:
            d.parse_status = "pdf_unrecognised"
            continue
        for k, v in fields.items():
            setattr(d, k, v)
        d.parse_status = "parsed"
    return res
