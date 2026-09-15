"""Minimal ISIN ↔ provider symbol mapping for phase 1 (a); the full instrument referential comes
with sub-lot C (docs/05 §5.5). Reads ``config/instruments_map.yaml`` if present."""

from __future__ import annotations

import os
from functools import lru_cache

import yaml


@lru_cache(maxsize=1)
def _load() -> dict[str, dict[str, str]]:
    path = os.environ.get("INSTRUMENTS_MAP_PATH", "config/instruments_map.yaml")
    if not os.path.exists(path):
        return {}
    data = yaml.safe_load(open(path, encoding="utf-8")) or {}
    return {str(k): dict(v) for k, v in (data.get("instruments") or {}).items()}


def eodhd_symbols_for(isins: list[str]) -> dict[str, str]:
    """{eodhd_symbol: isin}: the ``instruments`` referential first, then the YAML map."""
    out: dict[str, str] = {}
    try:
        from sqlalchemy import select

        from app.db.models import Instrument
        from app.db.session import db_session

        with db_session() as s:
            for inst in s.scalars(select(Instrument).where(Instrument.isin.in_(isins))):
                if inst.ticker_eodhd:
                    out[inst.ticker_eodhd] = inst.isin
    except Exception:  # noqa: BLE001 — referential not migrated yet
        pass
    m = _load()
    for i in isins:
        if i in m and m[i].get("eodhd") and i not in out.values():
            out[m[i]["eodhd"]] = i
    return out


def name_of(isin: str) -> str:
    try:
        from app.db.models import Instrument
        from app.db.session import db_session

        with db_session() as s:
            inst = s.get(Instrument, isin)
            if inst is not None:
                return inst.name
    except Exception:  # noqa: BLE001
        pass
    return _load().get(isin, {}).get("name", isin)
