"""ISIN ↔ provider symbol mapping: the ``instruments`` referential first (sub-lot C, docs/05 §5.5),
then ``config/instruments_map.yaml`` if present. Also resolves broker instruments (Saxo ``Uic`` +
``Symbol``) to ISINs, since Saxo OpenAPI never returns ISINs (ADR-006)."""

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


def resolve_broker_isin(provider: str, uic: str, symbol: str) -> str | None:
    """Saxo ``Uic`` + ``Symbol`` (``VK:xpar``) → ISIN, in this order: persisted alias
    ``instrument_aliases(provider="<provider>_uic", alias=uic)``, ``instruments.ticker_saxo``, ticker + MIC
    parsed from the symbol against ``instruments.ticker_local``/``mic``, then the YAML map (``saxo:`` key, or
    ``eodhd`` ticker + ``mic``). A hit is persisted as alias (and ``ticker_saxo``). ``None`` otherwise."""
    from app.data.providers.saxo import parse_saxo_symbol

    parsed = parse_saxo_symbol(symbol)
    alias_provider = f"{provider}_uic"
    isin: str | None = None
    try:
        from sqlalchemy import select

        from app.db.models import Instrument, InstrumentAlias
        from app.db.session import db_session

        with db_session() as s:
            row = s.scalar(
                select(InstrumentAlias).where(InstrumentAlias.provider == alias_provider, InstrumentAlias.alias == uic)
            )
            if row is not None:
                return row.isin
            inst = s.scalar(select(Instrument).where(Instrument.ticker_saxo == symbol)) if symbol else None
            if inst is None and parsed is not None:
                ticker, mic = parsed
                cands = list(
                    s.scalars(select(Instrument).where(Instrument.ticker_local == ticker, Instrument.mic == mic))
                )
                inst = cands[0] if len(cands) == 1 else None  # ambiguity → unresolved, never a guess
            if inst is None:
                isin = _isin_from_map(symbol, parsed, uic)
                inst = s.get(Instrument, isin) if isin else None
            if inst is not None:
                isin = inst.isin
                if symbol and not inst.ticker_saxo:
                    inst.ticker_saxo = symbol
            if isin and uic:
                s.add(InstrumentAlias(isin=isin, provider=alias_provider, alias=uic))
            return isin
    except Exception:  # noqa: BLE001 — referential not migrated yet: YAML map only
        return isin or _isin_from_map(symbol, parsed, uic)


def _isin_from_map(symbol: str, parsed: tuple[str, str] | None, uic: str = "") -> str | None:
    m = _load()
    for isin, entry in m.items():
        if symbol and entry.get("saxo") == symbol:
            return isin
        if uic and str(entry.get("saxo_uic", "")) == uic:
            return isin
    if parsed is not None:
        ticker, mic = parsed
        hits = [
            isin
            for isin, entry in m.items()
            if str(entry.get("mic", "")).upper() == mic and str(entry.get("eodhd", "")).split(".")[0].upper() == ticker
        ]
        if len(hits) == 1:
            return hits[0]
    return None
