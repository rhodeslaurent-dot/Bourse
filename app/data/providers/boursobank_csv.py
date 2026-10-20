"""BoursoBank CSV export parser (positions + operations history), docs/04 §4.5.

Format to be validated on a real (anonymised) export in phase 1 (A1.1): the parser is tolerant
(``;`` or ``,`` separator, French numbers ``1 234,56``, header aliases) and *asserts* the structure
it needs so that a silent format change raises instead of importing garbage (docs/14 §14.2).
Timestamps in the export are Paris local time (C2) and are converted to UTC.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")

POSITION_ALIASES = {
    "name": ("name", "libellé", "libelle", "valeur", "nom"),
    "isin": ("isin", "code isin"),
    "quantity": ("quantity", "quantité", "quantite", "qté", "qte"),
    "buying_price": ("buyingprice", "buying_price", "pru", "prix de revient", "prix moyen"),
    "last_price": ("lastprice", "last_price", "cours", "dernier cours", "dernier"),
}
OPERATION_ALIASES = {
    "date": ("date", "date opération", "date operation", "dateop"),
    "isin": ("isin", "code isin"),
    "name": ("name", "libellé", "libelle", "valeur"),
    "side": ("sens", "opération", "operation", "type", "side"),
    "quantity": ("quantity", "quantité", "quantite", "qté"),
    "price": ("price", "prix", "cours", "prix unitaire"),
    "fees": ("frais", "courtage", "fees", "commission"),
    "amount": ("montant", "amount", "montant net"),
    "reference": ("référence", "reference", "id", "numéro", "numero"),
}


class BoursoFormatError(ValueError):
    pass


@dataclass(frozen=True)
class BoursoPosition:
    name: str
    isin: str
    quantity: int
    buying_price: float
    last_price: float | None


@dataclass(frozen=True)
class BoursoOperation:
    date: datetime
    isin: str
    name: str
    side: str  # buy | sell | other
    quantity: int
    price: float
    fees: float
    amount: float | None
    reference: str


def parse_number(text: str | None) -> float | None:
    if text is None:
        return None
    s = text.strip().replace(" ", "").replace("\xa0", "").replace(" ", "").replace("€", "")
    if s in ("", "-", "—"):
        return None
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    m = re.match(r"^[+-]?\d+(\.\d+)?", s)
    if not m:
        raise BoursoFormatError(f"nombre illisible : {text!r}")
    return float(m.group(0))


class _Semi(csv.Dialect):
    delimiter = ";"
    quotechar = '"'
    doublequote = True
    lineterminator = "\n"
    quoting = csv.QUOTE_MINIMAL


def _sniff(text: str) -> csv.Dialect | type[csv.Dialect]:
    head = text.splitlines()[0] if text else ""
    return csv.excel if head.count(",") > head.count(";") else _Semi


def _map_headers(
    fieldnames: list[str], aliases: dict[str, tuple[str, ...]], required: tuple[str, ...]
) -> dict[str, str]:
    norm = {f: f.strip().lower().lstrip("﻿") for f in fieldnames}
    out: dict[str, str] = {}
    for key, names in aliases.items():
        for f, n in norm.items():
            if n in names:
                out[key] = f
                break
    missing = [k for k in required if k not in out]
    if missing:
        raise BoursoFormatError(f"colonnes manquantes dans l'export BoursoBank : {missing} (trouvées : {fieldnames})")
    return out


def parse_positions(text: str) -> list[BoursoPosition]:
    reader = csv.DictReader(io.StringIO(text), dialect=_sniff(text))
    cols = _map_headers(reader.fieldnames or [], POSITION_ALIASES, ("isin", "quantity", "buying_price"))
    out = []
    for row in reader:
        isin = (row[cols["isin"]] or "").strip().upper()
        if not re.match(r"^[A-Z]{2}[A-Z0-9]{9}\d$", isin):
            raise BoursoFormatError(f"ISIN invalide : {isin!r}")
        qty = parse_number(row[cols["quantity"]])
        pru = parse_number(row[cols["buying_price"]])
        if qty is None or pru is None:
            raise BoursoFormatError(f"quantité/PRU manquants pour {isin}")
        out.append(
            BoursoPosition(
                name=(row.get(cols.get("name", ""), "") or "").strip(),
                isin=isin,
                quantity=int(qty),
                buying_price=pru,
                last_price=parse_number(row.get(cols["last_price"])) if "last_price" in cols else None,
            )
        )
    return out


def _side(text: str) -> str:
    t = text.strip().lower()
    if t.startswith(("achat", "buy", "souscription")):
        return "buy"
    if t.startswith(("vente", "sell", "cession")):
        return "sell"
    return "other"


def _date(text: str) -> datetime:
    t = text.strip()
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(t, fmt).replace(tzinfo=PARIS).astimezone(UTC)  # export en heure de Paris
        except ValueError:
            continue
    raise BoursoFormatError(f"date illisible : {text!r}")


def parse_operations(text: str) -> list[BoursoOperation]:
    reader = csv.DictReader(io.StringIO(text), dialect=_sniff(text))
    cols = _map_headers(reader.fieldnames or [], OPERATION_ALIASES, ("date", "isin", "side", "quantity", "price"))
    out = []
    for row in reader:
        isin = (row[cols["isin"]] or "").strip().upper()
        side = _side(row[cols["side"]] or "")
        if side == "other":
            continue
        qty = parse_number(row[cols["quantity"]]) or 0
        price = parse_number(row[cols["price"]]) or 0.0
        fees = (parse_number(row.get(cols.get("fees", ""), "0")) or 0.0) if "fees" in cols else 0.0
        amount = parse_number(row.get(cols["amount"])) if "amount" in cols else None
        ref = (
            row.get(cols["reference"]) if "reference" in cols else None
        ) or f"{isin}-{row[cols['date']]}-{side}-{int(abs(qty))}-{price}"
        out.append(
            BoursoOperation(
                _date(row[cols["date"]]),
                isin,
                (row.get(cols.get("name", ""), "") or "").strip(),
                side,
                int(abs(qty)),
                price,
                abs(fees),
                amount,
                ref.strip(),
            )
        )
    return out
