"""docs/33 §33.3 — delisted shares of London, Zurich and New York for research registration n° 2: a universe
without the values that left the exchange flatters every result (survivorship bias).

Three steps, each cheap before the next one: (1) the EODHD list of delisted shares, main venues only; (2) a
generous liquidity screen without any history, from one whole-exchange snapshot per month (``eod-bulk-last-day``
at a past date also returns the shares delisted since); (3) the history of the screened shares only. The
research applies its own exact point-in-time liquidity rule afterwards — the screen only avoids loading
millions of rows for shares that never came close to the universe."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import LoadedConfig
from app.data.base import ProviderError, QuotaExceeded
from app.db.models import Instrument, InstrumentAlias
from app.services.market_data import fx_to_eur

log = logging.getLogger("bourse.delisted")

# main venues of an EODHD exchange code (the US code mixes NYSE, NASDAQ and the OTC markets)
MAIN_VENUES: dict[str, set[str]] = {"US": {"NYSE", "NASDAQ", "NYSE MKT", "NYSE ARCA", "AMEX", "BATS"}}
SCREEN_FACTOR = 0.5  # a monthly snapshot at ≥ half the universe's turnover floor keeps the share (generous)


def pseudo_id(code: str, exchange: str) -> str:
    """Stable 12-character key of a delisted share without ISIN (``DL.`` + 9 hex): never an ISIN pattern."""
    return "DL." + hashlib.sha1(f"{code}.{exchange}".encode()).hexdigest()[:9].upper()


def main_venue_rows(rows: list[dict[str, Any]], exchange: str) -> list[dict[str, Any]]:
    venues = MAIN_VENUES.get(exchange)
    return [r for r in rows if r.get("Code") and (venues is None or str(r.get("Exchange", "")).upper() in venues)]


def month_starts(start: date, end: date) -> list[date]:
    """First weekday of each month from ``start`` to ``end`` (a closed day is retried on the next weekdays)."""
    out, d = [], date(start.year, start.month, 1)
    while d <= end:
        x = d
        while x.weekday() >= 5:
            x += timedelta(days=1)
        out.append(x)
        d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return out


def min_turnover_eur(config: LoadedConfig, exchange: str) -> float:
    u = config.params.universe or {}
    o = (u.get("market_overrides") or {}).get(exchange) or {}
    return float(o.get("min_adv_eur_cto", u.get("min_adv_eur_cto", 1_000_000)))


@dataclass
class ScreenReport:
    exchange: str
    listed: int = 0  # delisted shares on the main venues
    snapshots: int = 0  # monthly whole-exchange snapshots read
    seen: int = 0  # delisted shares found in at least one snapshot
    kept: dict[str, float] = field(default_factory=dict)  # code → best snapshot turnover (EUR)
    stopped: str | None = None  # quota or provider error that stopped the screen


def screen(
    s: Session, provider: Any, exchange: str, rows: list[dict[str, Any]], days: list[date], floor_eur: float
) -> ScreenReport:
    """Best single-day turnover (close × volume, converted at the ECB rate of the day) of each delisted share
    over the monthly snapshots; kept when it reaches ``SCREEN_FACTOR`` × the turnover floor once."""
    rep = ScreenReport(exchange, listed=len(rows))
    cur = {str(r["Code"]): str(r.get("Currency") or "USD") for r in rows}
    best: dict[str, float] = {}
    for d in days:
        bars: list[Any] = []
        for k in range(3):  # a closed day: the next weekdays
            try:
                bars = provider.fetch_eod_bulk(exchange, d + timedelta(days=k))
            except QuotaExceeded as exc:
                rep.stopped = f"quota : {exc}"[:160]
                break
            except ProviderError as exc:
                log.info("instantané %s du %s : %s", exchange, d, exc)
                bars = []
            if bars:
                break
        if rep.stopped:
            break
        if not bars:
            continue
        rep.snapshots += 1
        for b in bars:
            code = b.symbol.rsplit(".", 1)[0]
            if code not in cur:
                continue
            fx = fx_to_eur(s, cur[code], b.day)
            if fx is None:
                continue  # no rate: never guessed
            t = b.close * b.volume * fx
            best[code] = max(best.get(code, 0.0), t)
    rep.seen = len(best)
    rep.kept = {c: t for c, t in best.items() if t >= SCREEN_FACTOR * floor_eur}
    return rep


def store(s: Session, rows: list[dict[str, Any]], kept: set[str], exchange: str, mic: str) -> list[str]:
    """The kept delisted shares as inactive instruments, out of the universe (ISIN, or a pseudo key without
    one); an instrument already known under that key is left as it is. Returns the keys."""
    out = []
    for r in rows:
        code = str(r["Code"])
        if code not in kept:
            continue
        isin = str(r.get("Isin") or "").strip().upper() or pseudo_id(code, exchange)
        sym = f"{code}.{exchange}"
        if s.get(Instrument, isin) is None:
            s.add(Instrument(isin=isin, name=str(r.get("Name") or code)[:128], ticker_local=code[:24],
                             ticker_eodhd=sym, exchange=exchange, mic=mic, currency=str(r.get("Currency") or "USD")[:3],
                             kind="stock", active=False, in_universe=False))  # fmt: skip
            if s.scalar(select(InstrumentAlias).where(InstrumentAlias.provider == "eodhd",
                                                      InstrumentAlias.alias == sym)) is None:  # fmt: skip
                s.add(InstrumentAlias(isin=isin, provider="eodhd", alias=sym))
        out.append(isin)
    s.flush()
    return out
