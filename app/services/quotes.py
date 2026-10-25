"""Quote at each analysis (docs/07 §7.9.6, docs/22 R19): the price is read *when* an analysis runs
(proposal, thesis, letters review, assistant, portfolio review), from the best available source:
Saxo when the instrument's Uic is known (realtime if the venue subscription is active, its
``DelayedByMinutes`` otherwise), else EODHD REST (delayed 15 min). The status always tells which.

Plausibility: a quote more than ``data.plausibility_max_gap_pct`` away from the last official
close, with no news on the instrument in the last 24 h, is flagged « à vérifier » — never used to
fund a verdict (framework §11.2: stale aggregator prices, typing errors).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data.dto import Quote
from app.db.models import Instrument, NewsItem
from app.services.market_data import load_bars

log = logging.getLogger("bourse.quotes")


@dataclass
class CheckedQuote:
    isin: str
    quote: Quote | None
    last_close: float | None
    gap_vs_close: float | None
    plausible: bool
    note: str

    def as_fact(self) -> dict[str, Any]:
        q = self.quote
        return {
            "cours": q.last if q else None,
            "statut": q.data_status.value if q else "unavailable",
            "source": q.source if q else None,
            "horodatage_marche": q.market_timestamp.isoformat() if q and q.market_timestamp else None,
            "ecart_derniere_cloture": self.gap_vs_close,
            "vraisemblable": self.plausible,
            "note": self.note,
        }


def saxo_quotes(s: Session, isins: list[str]) -> dict[str, Quote]:
    if not (os.environ.get("SAXO_TOKEN_KEY") or os.environ.get("SAXO_ACCESS_TOKEN")):
        return {}
    uics = {
        inst.saxo_uic: inst.isin
        for inst in s.scalars(select(Instrument).where(Instrument.isin.in_(isins)))
        if inst.saxo_uic
    }
    if not uics:
        return {}
    try:
        from app.data.providers.saxo import SaxoClient, SaxoPriceProvider

        out = {}
        for q in SaxoPriceProvider(SaxoClient()).fetch_quotes(list(uics)):
            if q.symbol in uics and q.last is not None:
                out[uics[q.symbol]] = q
        return out
    except Exception as exc:  # noqa: BLE001 — EODHD is tried next; the source is always shown
        log.warning("cours Saxo indisponibles : %s", exc)
        return {}


def check(s: Session, isin: str, q: Quote | None, max_gap: float, now: datetime) -> CheckedQuote:
    bars = load_bars(s, isin, limit=1)
    close = bars[-1].close if bars else None
    if q is None or q.last is None:
        return CheckedQuote(isin, q, close, None, False, "cours indisponible")
    if close is None:
        return CheckedQuote(isin, q, None, None, True, "pas de clôture de référence")
    gap = q.last / close - 1
    if abs(gap) <= max_gap:
        return CheckedQuote(isin, q, close, gap, True, "")
    news = None
    for n in s.scalars(select(NewsItem).where(NewsItem.fetched_at >= now - timedelta(hours=24))):
        if isin in (n.isins or []):  # JSON column: filtered in Python, portable across SQLite / Postgres
            news = n.id
            break
    if news is not None:
        return CheckedQuote(isin, q, close, gap, True, f"écart {gap:+.1%} expliqué par une nouvelle du jour")
    return CheckedQuote(
        isin, q, close, gap, False, f"à vérifier : écart {gap:+.1%} avec la dernière clôture, sans nouvelle"
    )


def quote_at_analysis(
    s: Session, isins: list[str], config: Any, fallback: Any | None = None, now: datetime | None = None
) -> dict[str, CheckedQuote]:
    """``fallback(isins) -> dict[isin, Quote]`` defaults to the EODHD REST quotes."""
    now = now or datetime.now(UTC)
    if not isins:
        return {}
    max_gap = float(getattr(config.params.data, "plausibility_max_gap_pct", None) or 0.05)
    got = saxo_quotes(s, isins)
    rest = [i for i in isins if i not in got]
    if rest:
        if fallback is None:
            from app.scheduler.jobs.position_monitor import quotes_for

            fallback = lambda xs: quotes_for(xs, config)  # noqa: E731
        try:
            got.update(fallback(rest))
        except Exception as exc:  # noqa: BLE001 — no quote is better than a guessed one
            log.warning("cours EODHD indisponibles : %s", exc)
    return {i: check(s, i, got.get(i), max_gap, now) for i in isins}


def plausible_quotes(
    s: Session, isins: list[str], config: Any, fallback: Any | None = None, now: datetime | None = None
) -> dict[str, Quote]:
    """Quotes usable by an analysis: implausible ones are left out (« à vérifier »), never used."""
    out: dict[str, Quote] = {}
    for isin, c in quote_at_analysis(s, isins, config, fallback, now=now).items():
        if c.quote is not None and c.plausible:
            out[isin] = c.quote
        elif c.quote is not None:
            log.warning("cours %s écarté de l'analyse : %s", isin, c.note)
    return out
