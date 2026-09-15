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
    """{eodhd_symbol: isin} for the ISINs known in the map."""
    m = _load()
    return {m[i]["eodhd"]: i for i in isins if i in m and m[i].get("eodhd")}


def name_of(isin: str) -> str:
    return _load().get(isin, {}).get("name", isin)
