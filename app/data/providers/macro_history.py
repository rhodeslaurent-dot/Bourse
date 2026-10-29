"""Past macro announcements (docs/22 §22.7, docs/07 §7.9.5): the history the R14 exposure needs.

The ForexFactory feed only gives this week and the next one, so ``macro_events`` could never hold the 4 past
events of a kind that the exception of R14 requires. Sources, in this order, each row keeping its own:

(a) the EODHD economic events API (``/economic-events``), which may be outside the subscribed plan: 401 / 402 / 403
    → « unavailable », as for the earnings calendar;
(b) official pages: the ECB's monetary policy decisions (press releases ``ecb.mpYYMMDD``), the Fed's FOMC calendars
    (statements ``monetaryYYYYMMDDa.htm``, minutes « Released Month D, YYYY ») and the BLS archives of the CPI and
    Employment Situation releases (``cpi_MMDDYYYY.htm``, ``empsit_MMDDYYYY.htm``).

A date is never invented: a page that gives no time of day gives a date only (``date_only``), stored at 00:00
Paris — the measure only reads the date. Only the closed list of announcement families of ``events.macro_families``
is kept; each row takes the first ForexFactory title of its family. The parsers read patterns of links and dates,
not the layout, and skip what they do not recognise. Read-only.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from app.data.base import ProviderError, ProviderUnavailable, QuotaExceeded

PARIS_TZ = "Europe/Paris"


@dataclass(frozen=True)
class PastEvent:
    family: str  # events.macro_families key
    currency: str  # USD | EUR
    day: date  # official date of the announcement (local date of the issuer, = Paris date for these families)
    ts: datetime | None  # UTC when the source gives a time, else None (date only)
    source: str  # ≤ 32 characters (macro_events.source)


# --- (a) EODHD economic events ------------------------------------------------------------------------------------

# closed list: (EODHD countries, words that must all appear in ``type``, words that must not, family)
EODHD_MAP: list[tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], str]] = [
    (("US",), ("interest rate decision",), (), "fed_decision"),
    (("US",), ("fomc", "minutes"), (), "fomc_minutes"),
    (("US",), ("non farm payrolls",), ("private", "adp"), "us_jobs"),
    (("US",), ("non-farm",), ("private", "adp"), "us_jobs"),
    (("US",), ("inflation rate",), ("pce", "expectations"), "us_cpi"),
    (("US",), ("cpi",), ("pce", "expectations"), "us_cpi"),
    (("EU", "EA", "EMU", "EZ"), ("interest rate decision",), (), "ecb_decision"),
    (("EU", "EA", "EMU", "EZ"), ("deposit facility rate",), (), "ecb_decision"),
    (("EU", "EA", "EMU", "EZ"), ("inflation rate", "flash"), (), "ea_cpi"),
]
EODHD_CCY = {"US": "USD", "EU": "EUR", "EA": "EUR", "EMU": "EUR", "EZ": "EUR"}


def eodhd_family(country: str, kind: str) -> str | None:
    c, k = (country or "").upper(), (kind or "").lower()
    for countries, must, must_not, fam in EODHD_MAP:
        if c in countries and all(w in k for w in must) and not any(w in k for w in must_not):
            return fam
    return None


def eodhd_events(get: Callable[..., Any], start: date, end: date, page: int = 1000) -> list[PastEvent]:
    """``get`` is ``EodhdProvider._get``. Raises ProviderUnavailable when the plan does not include the API."""
    out: list[PastEvent] = []
    for country in ("US", "EU"):
        offset = 0
        while True:
            try:
                data = get("/economic-events", **{"from": start.isoformat(), "to": end.isoformat(),
                                                   "country": country, "limit": page, "offset": offset})  # fmt: skip
            except QuotaExceeded as exc:
                if "402" in str(exc):  # « payment required »: outside the plan
                    raise ProviderUnavailable(f"événements économiques EODHD hors forfait : {exc}") from exc
                raise
            except ProviderError as exc:
                if any(f"EODHD {c}" in str(exc) for c in (401, 402, 403)):
                    raise ProviderUnavailable(f"événements économiques EODHD hors forfait : {exc}") from exc
                raise
            rows = data if isinstance(data, list) else []
            for r in rows:
                if not isinstance(r, dict):
                    continue
                fam = eodhd_family(str(r.get("country") or country), str(r.get("type") or ""))
                if fam is None:
                    continue
                try:
                    ts = datetime.fromisoformat(str(r.get("date"))).replace(tzinfo=UTC)
                except ValueError:
                    continue
                ccy = EODHD_CCY.get(str(r.get("country") or country).upper(), "USD")
                out.append(PastEvent(fam, ccy, ts.date(), ts, "eodhd_economic_events"))
            if len(rows) < page:
                break
            offset += page
    return out


# --- (b) official pages -------------------------------------------------------------------------------------------

ECB_MP = re.compile(r"ecb\.mp(\d{2})(\d{2})(\d{2})")
ECB_PR_TITLED = re.compile(r'href="[^"]*?/pr(\d{2})(\d{2})(\d{2})[^"]*"[^>]*>\s*Monetary policy decisions', re.I)
ECB_ISO_TITLED = re.compile(
    r'isoDate="(\d{4}-\d{2}-\d{2})"(?:(?!isoDate=).){0,600}?Monetary policy decisions', re.I | re.S
)
FED_STATEMENT = re.compile(r"monetary(\d{4})(\d{2})(\d{2})a\.htm")
FED_MINUTES_RELEASED = re.compile(r"[Rr]eleased\s+([A-Z][a-z]+)\s+(\d{1,2}),\s+(\d{4})")
BLS_RELEASE = re.compile(r"\b(cpi|empsit)_(\d{2})(\d{2})(\d{4})\.htm")
MONTHS = {m: i for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July", "August",
                                      "September", "October", "November", "December"], 1)}  # fmt: skip


def _date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def parse_ecb_decisions(html: str) -> list[PastEvent]:
    """Monetary policy decisions: the press releases ``ecb.mpYYMMDD`` (recent years), a link entitled « Monetary
    policy decisions » to a press release ``prYYMMDD`` (older years), or an ``isoDate`` entry followed by that title.
    Date only."""
    found = [_date(2000 + int(y), int(m), int(d)) for y, m, d in ECB_MP.findall(html)]
    found += [_date(2000 + int(y), int(m), int(d)) for y, m, d in ECB_PR_TITLED.findall(html)]
    found += [date.fromisoformat(x) for x in ECB_ISO_TITLED.findall(html) if _iso_ok(x)]
    days = sorted({d for d in found if d is not None})
    return [PastEvent("ecb_decision", "EUR", x, None, "bce_decisions_officielles") for x in days]


def _iso_ok(x: str) -> bool:
    try:
        date.fromisoformat(x)
    except ValueError:
        return False
    return True


def parse_fed_calendar(html: str) -> list[PastEvent]:
    """FOMC calendars (current page and yearly historical pages): the statement of each meeting
    (``monetaryYYYYMMDDa.htm``: the decision day) and the release date of the minutes. Date only."""
    out = []
    statements = {_date(int(y), int(m), int(dd)) for y, m, dd in FED_STATEMENT.findall(html)}
    for d in sorted(x for x in statements if x is not None):
        out.append(PastEvent("fed_decision", "USD", d, None, "fed_calendrier_fomc"))
    released = set()
    for mon, dd, y in FED_MINUTES_RELEASED.findall(html):
        if mon in MONTHS:
            released.add(_date(int(y), MONTHS[mon], int(dd)))
    for d in sorted(x for x in released if x):
        out.append(PastEvent("fomc_minutes", "USD", d, None, "fed_calendrier_fomc"))
    return out


def parse_bls_archive(html: str) -> list[PastEvent]:
    """BLS release archives: ``cpi_MMDDYYYY.htm`` (CPI) and ``empsit_MMDDYYYY.htm`` (Employment Situation = NFP and
    unemployment rate), the release day. Date only."""
    out = []
    seen: set[tuple[str, date]] = set()
    for kind, m, d, y in BLS_RELEASE.findall(html):
        day = _date(int(y), int(m), int(d))
        fam = "us_cpi" if kind == "cpi" else "us_jobs"
        if day is None or (fam, day) in seen:
            continue
        seen.add((fam, day))
        out.append(PastEvent(fam, "USD", day, None, "bls_archives"))
    return sorted(out, key=lambda e: (e.family, e.day))


OFFICIAL_PAGES: dict[str, list[str]] = {
    "bce": ["https://www.ecb.europa.eu/press/govcdec/mopo/html/index.en.html"],
    "fed": ["https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"],
    "bls": [
        "https://www.bls.gov/bls/news-release/cpi.htm",
        "https://www.bls.gov/bls/news-release/empsit.htm",
    ],
}
FED_HISTORICAL = "https://www.federalreserve.gov/monetarypolicy/fomchistorical{year}.htm"
ECB_YEAR_INCLUDE = "https://www.ecb.europa.eu/press/govcdec/mopo/{year}/html/index_include.en.html"
PARSERS: dict[str, Callable[[str], list[PastEvent]]] = {
    "bce": parse_ecb_decisions,
    "fed": parse_fed_calendar,
    "bls": parse_bls_archive,
}


def official_urls(source: str, start: date, end: date) -> list[str]:
    urls = list(OFFICIAL_PAGES.get(source, []))
    if source == "fed":  # the yearly pages hold the meetings older than the current calendar (about 5 years)
        urls += [FED_HISTORICAL.format(year=y) for y in range(start.year, end.year + 1)]
    if source == "bce":  # the index loads one fragment per year
        urls += [ECB_YEAR_INCLUDE.format(year=y) for y in range(start.year, end.year + 1)]
    return urls


def fetch_text(url: str, client: Any | None = None) -> str | None:
    """The page text; ``None`` for a page that does not exist (404). Raises ProviderUnavailable otherwise."""
    import httpx

    c = client or httpx.Client(timeout=30, follow_redirects=True,
                               headers={"User-Agent": "Mozilla/5.0 (BOURSE-PILOT, usage personnel)"})  # fmt: skip
    try:
        r = c.get(url)
        if r.status_code == 404:
            return None
        if r.status_code >= 400:
            raise ProviderUnavailable(f"{url} : HTTP {r.status_code}")
        return str(r.text)
    except httpx.HTTPError as exc:
        raise ProviderUnavailable(f"{url} : {exc.__class__.__name__}") from exc
    finally:
        if client is None:
            c.close()
