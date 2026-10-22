"""Free fundamentals and analyst consensus (phase 3, docs/22 §22.7): weekly refresh from Yahoo
(throttled, source and date kept), facts for block F of the score (sector median PER computed
here from the stored values) and the analyst target shown — never computed — on the card."""

from __future__ import annotations

import logging
import statistics
import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data.base import ProviderUnavailable
from app.data.providers.yahoo_fundamentals import fetch, yahoo_symbol
from app.db.models import Fundamentals, Instrument, PositionRow
from app.domain.scoring import FundamentalFacts

log = logging.getLogger("bourse.fundamentals")


def refresh(
    s: Session,
    day: date,
    max_items: int = 800,
    min_interval_s: float = 1.0,
    info_fn: Callable[[str], dict[str, Any]] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, int]:
    held = {p.isin for p in s.scalars(select(PositionRow).where(PositionRow.closed_at.is_(None)))}
    insts = list(s.scalars(select(Instrument).where((Instrument.in_universe.is_(True)) | (Instrument.isin.in_(held)))))
    rep = {"ok": 0, "absent": 0, "erreur": 0, "deja": 0}
    for k, inst in enumerate(insts[:max_items]):
        if s.scalar(select(Fundamentals.id).where(Fundamentals.isin == inst.isin, Fundamentals.day == day)):
            rep["deja"] += 1
            continue
        sym = yahoo_symbol(inst.ticker_eodhd)
        if not sym:
            rep["absent"] += 1
            continue
        if k:
            sleep(min_interval_s)
        try:
            data = fetch(sym, info_fn)
        except ProviderUnavailable as exc:
            rep["erreur"] += 1
            log.info("fondamentaux %s indisponibles : %s", sym, exc)
            continue
        if not data:
            rep["absent"] += 1
            continue
        s.add(
            Fundamentals(isin=inst.isin, day=day, source="yahoo", symbol=sym, data=data, received_at=datetime.now(UTC))
        )
        rep["ok"] += 1
    s.flush()
    return rep


def latest(s: Session, isin: str) -> Fundamentals | None:
    return s.scalar(select(Fundamentals).where(Fundamentals.isin == isin).order_by(Fundamentals.day.desc()))


def sector_median_pe(s: Session, sector: str | None) -> float | None:
    if not sector:
        return None
    pes = []
    for inst in s.scalars(select(Instrument).where(Instrument.sector == sector)):
        f = latest(s, inst.isin)
        pe = (f.data or {}).get("trailingPE") if f else None
        if isinstance(pe, int | float) and 0 < pe < 200:
            pes.append(float(pe))
    return statistics.median(pes) if len(pes) >= 5 else None


def facts(s: Session, inst: Instrument) -> tuple[FundamentalFacts | None, dict[str, Any]]:
    """(block-F facts, display dict with source, date and analyst consensus)."""
    f = latest(s, inst.isin)
    tv = tradingview_consensus(s, inst.isin)
    if (f is None or not f.data) and tv is None:
        return None, {}
    if f is None or not f.data:  # TradingView only (docs/23 S4): consensus shown, no block-F facts
        return None, with_tradingview({"source": "tradingview", "jour": tv.day.isoformat()}, tv)
    d = f.data
    debt = d.get("totalDebt")
    ebitda = d.get("ebitda")
    ff = FundamentalFacts(
        revenue_growth=d.get("revenueGrowth"),
        operating_margin=d.get("operatingMargins"),
        debt_to_ebitda=(debt / ebitda)
        if isinstance(debt, int | float) and isinstance(ebitda, int | float) and ebitda > 0
        else None,
        pe=d.get("trailingPE"),
        sector_median_pe=sector_median_pe(s, inst.sector),
    )
    shown = {
        "source": f.source,
        "jour": f.day.isoformat(),
        "devise": d.get("financialCurrency") or d.get("currency"),
        "objectif_analystes": d.get("targetMeanPrice"),
        "objectif_bas": d.get("targetLowPrice"),
        "objectif_haut": d.get("targetHighPrice"),
        "nb_analystes": int(d["numberOfAnalystOpinions"]) if d.get("numberOfAnalystOpinions") else None,
        "recommandation": d.get("recommendationKey"),
    }
    return ff, with_tradingview(shown, tv) if tv is not None else shown


MAX_SOURCE_GAP = 0.15  # docs/23 S4: Yahoo and TradingView targets more than 15 % apart → neither is used


def tradingview_consensus(s: Session, isin: str):  # type: ignore[no-untyped-def]
    from app.db.models import AnalystConsensus

    return s.scalar(
        select(AnalystConsensus)
        .where(AnalystConsensus.isin == isin, AnalystConsensus.source == "tradingview")
        .order_by(AnalystConsensus.day.desc())
    )


def with_tradingview(shown: dict[str, Any], tv) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    """Cross-check (docs/23 S4): TradingView fills a missing Yahoo consensus; when both exist and their
    targets diverge by more than 15 %, the gap is shown and no analyst target is retained."""
    d = tv.data or {}
    out = {**shown, "tradingview": {**d, "jour": tv.day.isoformat()}}
    t_tv, n_tv = d.get("objectif_moyen"), d.get("nb_analystes")
    if out.get("objectif_analystes") is None and t_tv is not None:
        out.update(
            {
                "objectif_analystes": t_tv,
                "nb_analystes": int(n_tv) if n_tv else None,
                "devise": d.get("devise"),
                "source": "tradingview",
            }
        )
    elif out.get("objectif_analystes") and t_tv:
        gap = float(t_tv) / float(out["objectif_analystes"]) - 1
        out["ecart_sources_pct"] = round(gap, 4)
        out["sources_divergentes"] = abs(gap) > MAX_SOURCE_GAP
    return out


def analyst_target(shown: dict[str, Any], min_analysts: int, quote_currency: str) -> float | None:
    """The consensus target is usable as a target only when enough analysts publish it and its
    currency is the quote currency (a target in another currency is shown, never converted)."""
    t, n = shown.get("objectif_analystes"), shown.get("nb_analystes")
    if t is None or not n or n < min_analysts or shown.get("sources_divergentes"):
        return None
    if shown.get("devise") and shown["devise"] != quote_currency:
        return None
    return float(t)
