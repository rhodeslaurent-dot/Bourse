"""Lot S5 (step 1 of the user's plan, 15/11/2026) — the United States: managers' dealings (SEC Form 4), material
events (SEC 8-K) and the daily short-sale volume (FINRA Reg SHO) of the US values of the universe.

- Form 4 → ``insider_declarations`` (source ``sec_form4``): open-market purchases (code P) and sales (code S) of a
  filing summed; grants, exercises and tax withholdings kept as ``autre`` (they are known, never re-read). The
  reporting owner's **name is never stored**, only the role (closed list). A trade under a 10b5-1 plan (decided
  in advance) is said so (``nature_detail``).
- 8-K → ``official_filings`` (diffuser ``SEC``, source ``sec_8k``): the items as published and their family;
  ``market_timestamp`` is the SEC acceptance time (public availability).
- FINRA → ``short_volume_daily``: the day's reported short volume, total volume and exempt volume.

Only the values of the universe (and the held ones) listed in the US are read: the SEC daily index says which of
them filed, and only those filings are fetched. Every request carries the user's own User-Agent
(``data.sec.user_agent``, SEC fair access) and is paced; without that setting nothing is read and it is said so.
Public, official data only.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data.base import ProviderUnavailable
from app.data.providers import finra_regsho, sec_edgar
from app.db.models import InsiderDeclaration, Instrument, OfficialFiling, PositionRow, ShortVolumeDaily

log = logging.getLogger("bourse.us_sources")

FORM4, FORM8K = "sec_form4", "sec_8k"
# fetch(url, max_bytes, stop) → text, or None when the file does not exist (404)
Fetch = Callable[[str, int, str | None], str | None]


@dataclass(frozen=True)
class UsSettings:
    sec_enabled: bool
    user_agent: str | None  # None when not set: nothing is read from the SEC
    lookback_days: int
    min_interval_s: float
    max_filings: int
    finra_enabled: bool
    finra_lookback_days: int


def settings(config: Any) -> UsSettings:
    data = config.params.data
    sec = (getattr(data, "sec", None) or {}) if data is not None else {}
    fin = (getattr(data, "finra", None) or {}) if data is not None else {}
    ua = str(sec.get("user_agent") or "").strip()
    ok = bool(ua) and "renseigner" not in ua and "@" in ua  # the SEC asks for a name and a contact address
    return UsSettings(
        bool(sec.get("enabled", True)), ua if ok else None, int(sec.get("lookback_days", 3)),
        max(float(sec.get("min_interval_s", 0.25)), 0.11),  # never more than ~9 requests a second (limit: 10)
        int(sec.get("max_filings_per_run", 2000)), bool(fin.get("enabled", True)), int(fin.get("lookback_days", 5)),
    )  # fmt: skip


def http_fetch(user_agent: str | None, min_interval_s: float, client: Any | None = None,
               sleep: Callable[[float], None] = time.sleep) -> Fetch:  # fmt: skip
    """One paced HTTP reader; a file is read up to ``max_bytes`` or up to ``stop`` (the end of a filing's header:
    an 8-K's exhibits are never downloaded)."""
    import httpx

    cl = client or httpx.Client(timeout=30.0, follow_redirects=True)
    last = [0.0]
    headers = {"User-Agent": user_agent or "bourse-pilot", "Accept-Encoding": "gzip, deflate"}

    def fetch(url: str, max_bytes: int = 3_000_000, stop: str | None = None) -> str | None:
        wait = min_interval_s - (time.monotonic() - last[0])
        if wait > 0:
            sleep(wait)
        last[0] = time.monotonic()
        with cl.stream("GET", url, headers=headers) as r:
            if r.status_code == 404:
                return None
            if r.status_code in (403, 429):
                raise ProviderUnavailable(f"{r.status_code} sur {url.split('/')[2]} (User-Agent ou débit refusé)")
            r.raise_for_status()
            parts: list[str] = []
            n = 0
            for chunk in r.iter_text():
                parts.append(chunk)
                n += len(chunk)
                if (stop and stop in "".join(parts[-2:])) or n >= max_bytes:
                    break
        return "".join(parts)

    return fetch


def us_values(s: Session) -> dict[str, Instrument]:
    """Normalised US ticker → instrument, for the values of the universe and the held ones listed in the US."""
    held = {p.isin for p in s.scalars(select(PositionRow).where(PositionRow.closed_at.is_(None)))}
    q = select(Instrument).where(
        Instrument.ticker_eodhd.like("%.US"), (Instrument.in_universe.is_(True)) | (Instrument.isin.in_(held))
    )
    return {finra_regsho.norm(i.ticker_eodhd.rsplit(".", 1)[0]): i for i in s.scalars(q) if i.ticker_eodhd}


def weekdays(start: date, end: date) -> list[date]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


@dataclass
class UsReport:
    days_read: int = 0
    days_missing: list[date] = field(default_factory=list)  # no daily file (holiday) or not yet published
    filings_seen: int = 0
    form4_added: int = 0
    form8k_added: int = 0
    known: int = 0
    skipped: int = 0  # unreadable, or the issuer is not a value followed
    truncated: bool = False
    short_rows: int = 0
    notes: list[str] = field(default_factory=list)

    def line(self) -> str:
        parts = [f"SEC : {self.days_read} jour(s) lu(s), {self.form4_added} Form 4 et {self.form8k_added} 8-K ajoutés, "
                 f"{self.known} déjà connus"]  # fmt: skip
        if self.skipped:
            parts.append(f"{self.skipped} écarté(s)")
        if self.truncated:
            parts.append("plafond de lecture atteint (suite au prochain passage)")
        if self.short_rows:
            parts.append(f"FINRA : {self.short_rows} ligne(s) de vente à découvert")
        return " ; ".join(parts + self.notes)


def _known(s: Session, accessions: list[str]) -> set[str]:
    out: set[str] = set()
    for k in range(0, len(accessions), 900):
        part = accessions[k : k + 900]
        q4 = select(InsiderDeclaration.doc_id).where(InsiderDeclaration.source == FORM4,
                                                     InsiderDeclaration.doc_id.in_(part))  # fmt: skip
        q8 = select(OfficialFiling.uin_id).where(OfficialFiling.diffuser == "SEC", OfficialFiling.uin_id.in_(part))
        out |= set(s.scalars(q4)) | set(s.scalars(q8))
    return out


def _filed_evening(d: date) -> datetime:
    """A filing without a readable acceptance time: public at the latest at the end of its filing day (22:00 ET)."""
    return datetime.combine(d, datetime.min.time().replace(hour=22), tzinfo=sec_edgar.NEW_YORK).astimezone(UTC)


def store_form4(s: Session, row: sec_edgar.IndexRow, text: str, isin_of: dict[int, str], now: datetime) -> str:
    """'added', 'skipped' (not a followed issuer, or unreadable)."""
    f4 = sec_edgar.parse_form4(text)
    hdr = sec_edgar.parse_header(text)
    if f4 is None or f4.issuer_cik is None or f4.issuer_cik not in isin_of:
        return "skipped"
    buy, sell, net, d0, d1, n = f4.open_market()
    codes = sorted({t.code for t in f4.transactions if t.code})
    nature = "acquisition" if buy > sell else "cession" if sell > buy else "autre"
    detail = ",".join(codes)[:60] + (" ; plan 10b5-1" if f4.plan_10b5_1 else "")
    amount = buy if nature == "acquisition" else sell if nature == "cession" else None
    s.add(InsiderDeclaration(
        source=FORM4, doc_id=row.accession[:32], market_timestamp=hdr.accepted_at or _filed_evening(row.day),
        info_date=row.day, issuer_name=(f4.issuer_name or row.company)[:160], isin=isin_of[f4.issuer_cik],
        isin_source="cik", role=f4.role, nature=nature, nature_detail=detail or None, tx_date=d0, tx_date_last=d1,
        price=(amount / abs(net)) if amount and net else None, currency="USD", volume=abs(net) if net else None,
        amount=amount, venue="US", n_transactions=n,
        transactions=[{"code": t.code, "jour": t.day.isoformat() if t.day else None, "titres": t.shares,
                       "prix": t.price, "acquis": t.acquired} for t in f4.transactions][:50],
        pdf_url=f"{sec_edgar.BASE}/Archives/{row.path}"[:500], parse_status="parsed", received_at=now,
        processed_at=now,
    ))  # fmt: skip
    return "added"


def store_8k(s: Session, row: sec_edgar.IndexRow, text: str, isin_of: dict[int, str], ticker_of: dict[int, str],
             now: datetime) -> str:  # fmt: skip
    hdr = sec_edgar.parse_header(text)
    if row.cik not in isin_of:
        return "skipped"
    fam = sec_edgar.family_of_items(hdr.items) or "autre"
    items = "; ".join(hdr.items)
    s.add(OfficialFiling(
        source=FORM8K, diffuser="SEC", uin_id=row.accession[:32], issuer_name=row.company[:200], isin=isin_of[row.cik],
        ticker=(ticker_of.get(row.cik) or "")[:16] or None, type_codes=list(hdr.items)[:20], family=fam,
        subtype_label=items[:160] or None, title=("8-K : " + items)[:500] if items else "8-K", language="en",
        disclosure="OBL", update_status="NEW", market_timestamp=hdr.accepted_at or _filed_evening(row.day),
        time_source="sec", time_precision="seconde" if hdr.accepted_at else "jour", data_status="eod",
        received_at=now, processed_at=now,
    ))  # fmt: skip
    return "added"


def sec_load(s: Session, config: Any, start: date, end: date, fetch: Fetch, now: datetime | None = None) -> UsReport:
    """The SEC daily indexes of [start, end] → the Form 4 and 8-K filings of the followed US values (idempotent)."""
    now = now or datetime.now(UTC)
    st = settings(config)
    rep = UsReport()
    vals = us_values(s)
    if not vals:
        rep.notes.append("aucune valeur américaine suivie")
        return rep
    raw = fetch(f"{sec_edgar.BASE}/files/company_tickers.json", 8_000_000, None)
    ticker_of = sec_edgar.parse_company_tickers(json.loads(raw)) if raw else {}
    isin_of = {cik: vals[finra_regsho.norm(t)].isin for cik, t in ticker_of.items() if finra_regsho.norm(t) in vals}
    if not isin_of:
        rep.notes.append("correspondance CIK → valeur vide (company_tickers.json illisible)")
        return rep
    budget = st.max_filings
    for d in weekdays(start, end):
        text = fetch(sec_edgar.index_url(d), 30_000_000, None)
        if text is None:
            rep.days_missing.append(d)
            continue
        rep.days_read += 1
        rows: dict[str, sec_edgar.IndexRow] = {}
        for r in sec_edgar.parse_master_index(text):
            if r.form in ("4", "8-K") and r.cik in isin_of:  # a Form 4 is listed under its issuer and its owner
                rows.setdefault(r.accession, r)
        rep.filings_seen += len(rows)
        known = _known(s, list(rows))
        for acc, r in rows.items():
            if acc in known:
                rep.known += 1
                continue
            if budget <= 0:
                rep.truncated = True
                break
            budget -= 1
            url = f"{sec_edgar.BASE}/Archives/{r.path}"
            try:
                body = fetch(url, 300_000, "</SEC-HEADER>") if r.form == "8-K" else fetch(url, 3_000_000, None)
            except ProviderUnavailable:
                raise
            except Exception as exc:  # noqa: BLE001 — one filing failing never stops the others
                log.info("dépôt SEC %s illisible : %s", acc, exc.__class__.__name__)
                rep.skipped += 1
                continue
            if not body:
                rep.skipped += 1
                continue
            with s.begin_nested():
                res = (store_8k(s, r, body, isin_of, ticker_of, now) if r.form == "8-K"
                       else store_form4(s, r, body, isin_of, now))  # fmt: skip
            if res == "added":
                rep.form8k_added += r.form == "8-K"
                rep.form4_added += r.form == "4"
            else:
                rep.skipped += 1
        s.flush()
        if rep.truncated:
            break
    return rep


def finra_load(s: Session, start: date, end: date, fetch: Fetch, rep: UsReport | None = None,
               now: datetime | None = None) -> UsReport:  # fmt: skip
    """FINRA's daily short-sale volume files of [start, end] → ``short_volume_daily`` for the followed US values."""
    now = now or datetime.now(UTC)
    rep = rep or UsReport()
    vals = us_values(s)
    for d in weekdays(start, end):
        text = fetch(finra_regsho.url(d), 20_000_000, None)
        if text is None:
            continue
        rows = [r for r in finra_regsho.parse(text) if finra_regsho.norm(r.symbol) in vals]
        have = {
            isin for isin in s.scalars(select(ShortVolumeDaily.isin).where(ShortVolumeDaily.day == d,
                                                                           ShortVolumeDaily.source == "finra"))
        }  # fmt: skip
        for r in rows:
            isin = vals[finra_regsho.norm(r.symbol)].isin
            if isin in have:
                continue
            have.add(isin)
            s.add(ShortVolumeDaily(isin=isin, day=r.day, symbol=r.symbol[:16], short_volume=r.short_volume,
                                   short_exempt_volume=r.short_exempt_volume, total_volume=r.total_volume,
                                   source="finra", received_at=now))  # fmt: skip
            rep.short_rows += 1
        s.flush()
    return rep
