"""Lot S5 (step 1 of the user's plan, 15/11/2026) — FINRA « Reg SHO daily short sale volume », read only.

File ``https://cdn.finra.org/equity/regsho/daily/CNMSshvolYYYYMMDD.txt`` (consolidated NMS), published each US
business day after the close, pipe-delimited: ``Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market``.
It is the share of the day's volume **reported** as short sales on FINRA's trade reporting facilities — not the
short interest, and not all venues: a fact as published, compared only to the value's own history.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

BASE = "https://cdn.finra.org/equity/regsho/daily"


@dataclass(frozen=True)
class ShortVolumeRow:
    day: date
    symbol: str
    short_volume: int
    short_exempt_volume: int
    total_volume: int


def url(d: date) -> str:
    return f"{BASE}/CNMSshvol{d:%Y%m%d}.txt"


def _int(v: str) -> int | None:
    try:
        return int(float(v))
    except ValueError:
        return None


def parse(text: str) -> list[ShortVolumeRow]:
    """The data lines (header and trailer skipped); a malformed line is skipped, never guessed."""
    out = []
    for line in text.splitlines():
        p = [x.strip() for x in line.split("|")]
        if len(p) < 5 or not (len(p[0]) == 8 and p[0].isdigit()):
            continue
        try:
            d = datetime.strptime(p[0], "%Y%m%d").date()
        except ValueError:
            continue
        sv, ex, tv = _int(p[2]), _int(p[3]), _int(p[4])
        if sv is None or ex is None or tv is None or tv <= 0 or sv < 0 or sv > tv or not p[1]:
            continue
        out.append(ShortVolumeRow(d, p[1].upper(), sv, ex, tv))
    return out


def norm(symbol: str) -> str:
    """One spelling for class shares across sources (``BRK.B``, ``BRK-B``, ``BRK/B`` → ``BRK-B``)."""
    return symbol.upper().replace(".", "-").replace("/", "-")
