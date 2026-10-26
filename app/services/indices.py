"""Market indices of each followed venue (regime per market, docs/32): their identifiers, EODHD codes and
the incremental load of their daily history into ``prices_eod`` (pseudo-ISIN ``IDX.<code>``, never an
instrument of the universe). A code EODHD refuses is reported, never replaced by another index; the **same**
index may come from Yahoo (``yahoo``, source ``yfinance`` on each bar) when EODHD fails or is stale (26/10/2026:
FTSE MIB and FTSE 100 absent from the EODHD plan, ISEQ stopped in 2022)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.data.base import ProviderError, QuotaExceeded
from app.services.market_data import last_eod_day, upsert_eod

log = logging.getLogger("bourse.indices")

EUROPE = "EU"  # the European reference (STOXX 600): the risk level of the day and the fallback of a venue
DEFAULT_EUROPE = {"id": "EU0009658202", "eodhd": "STOXX.INDX", "nom": "STOXX Europe 600"}


@dataclass(frozen=True)
class IndexSpec:
    market: str  # MIC of the venue, or EU
    id: str  # identifier in prices_eod.isin
    eodhd: str  # EODHD symbol (exchange INDX)
    name: str
    yahoo: str | None = None  # the same index on Yahoo (fallback), e.g. ^FTSE


def index_specs(config: Any) -> list[IndexSpec]:
    """The European reference first, then one index per venue (``regime.indices_by_mic``)."""
    r = (getattr(config.params, "regime", None) or {}) if config is not None else {}
    eu = {**DEFAULT_EUROPE, **(r.get("europe_index") or {})}
    out = [IndexSpec(EUROPE, str(eu["id"]), str(eu["eodhd"]), str(eu.get("nom") or "Europe"), eu.get("yahoo"))]
    for mic, v in (r.get("indices_by_mic") or {}).items():
        if v and v.get("id") and v.get("eodhd"):
            out.append(
                IndexSpec(str(mic), str(v["id"]), str(v["eodhd"]), str(v.get("nom") or mic), v.get("yahoo") or None)
            )
    return out


def max_age_days(config: Any) -> int:
    """``regime.index_max_age_days``: an index older than this gives no regime (the venue follows Europe)."""
    r = (getattr(config.params, "regime", None) or {}) if config is not None else {}
    return int(r.get("index_max_age_days", 5))


def index_for(config: Any, market: str) -> IndexSpec | None:
    return next((x for x in index_specs(config) if x.market == market), None)


def refresh_indices(
    s: Session,
    provider: Any,
    config: Any,
    today: date,
    first_day: date = date(2016, 1, 1),
    fallback: Any | None = None,
) -> dict[str, tuple[int, str | None]]:
    """Load each index from the day after its last stored session (full history from ``first_day`` the first
    time). When EODHD fails, returns nothing or leaves the series stale, the same index is read from ``fallback``
    (Yahoo) if the spec names it. Returns {market: (bars written, error or None)}; a quota exhaustion stops
    cleanly. A series still stale afterwards is reported as such."""
    out: dict[str, tuple[int, str | None]] = {}
    stale_before = today - timedelta(days=max_age_days(config))
    for spec in index_specs(config):
        last = last_eod_day(s, spec.id)
        start = (last + timedelta(days=1)) if last else first_day
        if start > today:
            out[spec.market] = (0, None)
            continue
        n, err = 0, None
        try:
            bars = provider.fetch_eod([spec.eodhd], start, today + timedelta(days=1))
            n = upsert_eod(s, bars, {spec.eodhd: spec.id})
        except QuotaExceeded as exc:
            out[spec.market] = (0, f"quota : {exc}"[:160])
            break
        except ProviderError as exc:
            err = str(exc)[:160]
            log.warning("indice %s (%s) : %s", spec.name, spec.eodhd, str(exc)[:120])
        newest = last_eod_day(s, spec.id)
        if spec.yahoo and fallback is not None and (newest is None or newest < stale_before):
            try:  # the same index from another provider: never another index
                ybars = fallback.fetch_eod([spec.yahoo], start, today + timedelta(days=1))
                k = upsert_eod(s, ybars, {spec.yahoo: spec.id})
                n += k
                if k:
                    err = None
            except Exception as exc:  # noqa: BLE001 — the fallback failing leaves the EODHD diagnosis
                log.warning("indice %s (Yahoo %s) : %s", spec.name, spec.yahoo, str(exc)[:120])
            newest = last_eod_day(s, spec.id)
        if err is None and newest is None:
            err = "aucune séance renvoyée (code à vérifier)"
        elif err is None and newest < stale_before:
            err = f"périmé : dernière séance le {newest} (la place suit le régime européen)"
        out[spec.market] = (n, err)
        s.flush()
    return out
