"""Lot L1 (docs/36 §36.9, ADR-017) — the official regulated releases of the AMF flow: pure helpers, no I/O.

* **family** of a release from its 6-digit type code (``INF_STP_PRI``: 3 digits of type, 3 of subtype), closed list
  ``FAMILIES``; an unknown code goes to « autre » and is counted by the caller, never guessed from the title;
* **release time**: the source writes the **Paris wall clock**, but its offset label cannot be trusted — the API
  of info-financiere.gouv.fr prints the wall clock with « +00:00 » and the real offset in a separate field; the
  DILA packages still wrote « +02:00 » after the clock change of 25/10/2026, while the releases kept falling at
  07:00, 07:30 and 18:00 Paris (9 packages checked on 01/11/2026: January « +01:00 », July « +02:00 », late October
  « +02:00 »). The wall clock is therefore read and localised in Europe/Paris; the label is only compared and the
  mismatches are counted (ADR-017). A wall clock of exactly 00:00:00 is a date without an hour (« jour »);
  a sentinel year (8888 in the 2008-2013 records) or an unreadable value gives no time at all;
* **coverage** of each month against the median of the same calendar month over the other years: a month below
  ``min_share`` of it is « non couvert », so that the study reads « inconnu » there, never « aucun communiqué ».
"""

from __future__ import annotations

import re
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")

FAMILIES = (
    "resultats", "chiffre_affaires", "operation", "info_privilegiee", "rapport_financier", "document",
    "droits_de_vote", "opa", "rachat_actions", "gouvernance", "rumeur", "franchissement_seuil", "autre",
)  # fmt: skip

FAMILY_LABELS = {
    "resultats": "résultats", "chiffre_affaires": "chiffre d'affaires", "operation": "acquisition ou cession",
    "info_privilegiee": "autre information privilégiée", "rapport_financier": "rapport financier",
    "document": "mise à disposition d'un document", "droits_de_vote": "droits de vote et capital",
    "opa": "offre publique", "rachat_actions": "rachat d'actions, contrat de liquidité", "gouvernance": "gouvernance",
    "rumeur": "déclaration sur une rumeur", "franchissement_seuil": "franchissement de seuil", "autre": "autre",
}  # fmt: skip

# INF_STP_PRI → family (list of values of the AMF flow, LV_01.07 → LV_01.09)
_CODE_FAMILY: dict[str, str] = {
    "001001": "resultats",  # comptes, résultats, chiffre d'affaires (jusqu'en 2016)
    "001007": "resultats",  # information privilégiée — comptes et résultats (depuis 2016)
    "001008": "chiffre_affaires",  # information privilégiée — chiffre d'affaires (depuis 2016)
    "005001": "chiffre_affaires",  # information trimestrielle
    "005002": "chiffre_affaires",
    "001002": "operation",  # information privilégiée — opérations : acquisitions, cessions
    "001003": "info_privilegiee",  # 2007-2010 seulement
    "001004": "info_privilegiee",
    "001005": "info_privilegiee",
    "001006": "info_privilegiee",  # information privilégiée — autres communiqués
    "003000": "rapport_financier",  # rapport financier annuel
    "004000": "rapport_financier",  # rapport financier semestriel
    **{f"002{k:03d}": "document" for k in range(1, 11)},  # AG, document d'enregistrement, prospectus…
    "006000": "droits_de_vote",
    **{f"009{k:03d}": "opa" for k in range(1, 8)},
    **{f"013{k:03d}": "rachat_actions" for k in range(1, 8)},
    "016000": "rachat_actions",  # contrat de liquidité (jusqu'en 2010)
    "008000": "gouvernance",
    "015000": "rumeur",
    "023000": "franchissement_seuil",  # décision de franchissement de seuil (libellé de l'API, vu le 01/11/2026)
    "300005": "rapport_financier",  # document de référence (code secondaire 003000), vu en 2025-2026
    "007000": "autre",  # honoraires des commissaires aux comptes
    "014000": "autre",  # État membre d'origine
    "022000": "autre",  # paiements aux gouvernements
    "024000": "autre",  # modification des droits attachés aux actions
}

_TS = re.compile(r"^\s*(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\.\d+)?\s*(Z|[+-]\d{2}:?\d{2})?\s*$")
_OFFSET = re.compile(r"^\s*([+-])?(\d{2}):?(\d{2})\s*$")
_ISIN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
MIN_YEAR, MAX_YEAR = 1990, 2100  # outside: a sentinel (8888) or a typing error, never a release time


def family_of(code: str | None) -> tuple[str, bool]:
    """(family, known): an unknown or empty code is « autre » with ``known`` False (counted by the caller)."""
    c = (code or "").strip()
    fam = _CODE_FAMILY.get(c)
    return (fam, True) if fam is not None else ("autre", False)


def family_for(code: str | None, secondary: Sequence[str] = ()) -> tuple[str, bool]:
    """(family, primary code known). An unknown primary code takes the family of its first known secondary code
    (``INF_STP_INF``), else of its 3-digit type when every known code of that type has one family (004001 →
    « rapport financier »; never for 001, which mixes results, operations and other privileged information)."""
    fam, ok = family_of(code)
    if ok:
        return fam, True
    for c in secondary:
        f, k = family_of(c)
        if k:
            return f, False
    prefix = (code or "").strip()[:3]
    fams = {f for k, f in _CODE_FAMILY.items() if k[:3] == prefix} if len(prefix) == 3 else set()
    return (fams.pop() if len(fams) == 1 else "autre"), False


def known_codes() -> dict[str, str]:
    return dict(_CODE_FAMILY)


def valid_isin(value: str | None) -> str | None:
    """The ISIN when it has the ISO 6166 shape and a correct check digit; otherwise None (never repaired)."""
    v = (value or "").strip().upper()
    if not _ISIN.match(v):
        return None
    digits = "".join(str(int(ch, 36)) for ch in v[:-1])
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 0:
            n *= 2
            n = n - 9 if n > 9 else n
        total += n
    return v if (10 - total % 10) % 10 == int(v[-1]) else None


def _norm_offset(label: str | None) -> str | None:
    if label is None:
        return None
    if label.strip().upper() == "Z":
        return "+00:00"
    m = _OFFSET.match(label)
    if not m:
        return None
    return f"{m.group(1) or '+'}{m.group(2)}:{m.group(3)}"


def _fmt_offset(delta: timedelta | None) -> str | None:
    if delta is None:
        return None
    minutes = int(delta.total_seconds() // 60)
    sign = "+" if minutes >= 0 else "-"
    return f"{sign}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}"


@dataclass(frozen=True)
class ReleaseTime:
    utc: datetime | None  # the Paris wall clock localised and converted to UTC; None when unreadable
    label: str | None  # offset as written by the source, « +HH:MM »
    paris_offset: str | None  # real offset of Paris at that wall clock
    label_ok: bool | None  # label == real offset (None without a label)
    precision: str  # « seconde » | « jour » (00:00:00: a date without an hour) | « illisible »


def paris_wall_clock(raw: str | None, label: str | None = None) -> ReleaseTime:
    """Release time from the source's text. ``label``: the offset given in a separate field (the API's
    ``timezone_uin_dat_amf``); without it, the offset written after the wall clock (DILA XML). The wall clock is
    always read as Paris time; the label never moves it (ADR-017)."""
    m = _TS.match(raw or "")
    if not m:
        return ReleaseTime(None, _norm_offset(label), None, None, "illisible")
    y, mo, d, hh, mi, ss = (int(m.group(i)) for i in range(1, 7))
    lab = _norm_offset(label) if label is not None else _norm_offset(m.group(7))
    if not MIN_YEAR <= y <= MAX_YEAR:
        return ReleaseTime(None, lab, None, None, "illisible")
    try:
        local = datetime(y, mo, d, hh, mi, ss, tzinfo=PARIS)  # fold=0: the first of an ambiguous autumn hour
    except ValueError:
        return ReleaseTime(None, lab, None, None, "illisible")
    real = _fmt_offset(local.utcoffset())
    precision = "jour" if (hh, mi, ss) == (0, 0, 0) else "seconde"
    return ReleaseTime(local.astimezone(UTC), lab, real, (lab == real) if lab is not None else None, precision)


def month_key(ts_utc: datetime) -> str:
    """« AAAA-MM » of the Paris day of a UTC time (coverage is counted per Paris month)."""
    return ts_utc.astimezone(PARIS).strftime("%Y-%m")


@dataclass(frozen=True)
class MonthCoverage:
    month: str  # AAAA-MM
    n: int
    reference: float | None  # median of the same calendar month over the other years, × the share of days read
    covered: bool | None  # None: no other year to compare with


def month_coverage(
    counts: Mapping[str, int], months: Sequence[str], min_share: float, fractions: Mapping[str, float] | None = None
) -> list[MonthCoverage]:
    """Each month of ``months`` against the median of the same calendar month in the other years of ``months``
    (zero months included: a missing year lowers no reference by more than its weight in the median).
    ``fractions``: share of the month inside the period (a month begun or ended inside it), scaling its
    reference."""
    by_cal: dict[str, list[tuple[str, int]]] = {}
    for m in months:
        by_cal.setdefault(m[5:7], []).append((m, int(counts.get(m, 0))))
    out: list[MonthCoverage] = []
    for m in months:
        n = int(counts.get(m, 0))
        others = [v for k, v in by_cal[m[5:7]] if k != m and (fractions or {}).get(k, 1.0) >= 1.0]
        if not others:
            out.append(MonthCoverage(m, n, None, None))
            continue
        ref = statistics.median(others) * (fractions or {}).get(m, 1.0)
        out.append(MonthCoverage(m, n, ref, n >= min_share * ref if ref > 0 else True))
    return out
