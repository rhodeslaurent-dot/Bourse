"""Market indices of each followed venue (regime per market, docs/32): their identifiers, EODHD codes and
the incremental load of their daily history into ``prices_eod`` (pseudo-ISIN ``IDX.<code>``, never an
instrument of the universe). A code EODHD refuses is reported, never replaced by another index."""

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


def index_specs(config: Any) -> list[IndexSpec]:
    """The European reference first, then one index per venue (``regime.indices_by_mic``)."""
    r = (getattr(config.params, "regime", None) or {}) if config is not None else {}
    eu = {**DEFAULT_EUROPE, **(r.get("europe_index") or {})}
    out = [IndexSpec(EUROPE, str(eu["id"]), str(eu["eodhd"]), str(eu.get("nom") or "Europe"))]
    for mic, v in (r.get("indices_by_mic") or {}).items():
        if v and v.get("id") and v.get("eodhd"):
            out.append(IndexSpec(str(mic), str(v["id"]), str(v["eodhd"]), str(v.get("nom") or mic)))
    return out


def index_for(config: Any, market: str) -> IndexSpec | None:
    return next((x for x in index_specs(config) if x.market == market), None)


def refresh_indices(
    s: Session, provider: Any, config: Any, today: date, first_day: date = date(2016, 1, 1)
) -> dict[str, tuple[int, str | None]]:
    """Load each index from the day after its last stored session (full history from ``first_day`` the first
    time). Returns {market: (bars written, error or None)}; a quota exhaustion stops cleanly."""
    out: dict[str, tuple[int, str | None]] = {}
    for spec in index_specs(config):
        last = last_eod_day(s, spec.id)
        start = (last + timedelta(days=1)) if last else first_day
        if start > today:
            out[spec.market] = (0, None)
            continue
        try:
            bars = provider.fetch_eod([spec.eodhd], start, today + timedelta(days=1))
        except QuotaExceeded as exc:
            out[spec.market] = (0, f"quota : {exc}"[:160])
            break
        except ProviderError as exc:
            out[spec.market] = (0, str(exc)[:160])
            log.warning("indice %s (%s) : %s", spec.name, spec.eodhd, str(exc)[:120])
            continue
        n = upsert_eod(s, bars, {spec.eodhd: spec.id})
        out[spec.market] = (n, None if (n or last) else "aucune séance renvoyée (code à vérifier)")
        s.flush()
    return out
