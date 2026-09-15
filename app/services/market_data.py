"""EOD prices, features and FX persistence (docs/13 §13.2). Never invents a bar: gaps versus the
venue calendar are reported (``gap_in_data``) and re-fetched, never interpolated."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.data.dto import EodBar
from app.db.models import FeatureDaily, FxRate, Instrument, PriceEod
from app.domain.calendar import VenueCalendar
from app.domain.indicators import Bar, compute_daily_features, percentile_rank

log = logging.getLogger("bourse.market_data")


def upsert_eod(s: Session, bars: list[EodBar], isin_by_symbol: dict[str, str]) -> int:
    n = 0
    for b in bars:
        isin = b.isin or isin_by_symbol.get(b.symbol)
        if not isin:
            continue
        row = s.scalar(select(PriceEod).where(PriceEod.isin == isin, PriceEod.day == b.day))
        if row is None:
            s.add(
                PriceEod(
                    isin=isin,
                    day=b.day,
                    open=b.open,
                    high=b.high,
                    low=b.low,
                    close=b.close,
                    adj_close=b.adj_close,
                    volume=b.volume,
                    source=b.source,
                    market_perimeter=b.market_perimeter.value,
                    received_at=b.received_at,
                    data_status=b.data_status.value,
                    official=b.official,
                )
            )
            n += 1
        elif b.official and (not row.official or row.close != b.close or row.volume != b.volume):
            row.open, row.high, row.low, row.close, row.adj_close, row.volume = (
                b.open,
                b.high,
                b.low,
                b.close,
                b.adj_close,
                b.volume,
            )
            row.source, row.received_at, row.official, row.data_status = (
                b.source,
                b.received_at,
                True,
                b.data_status.value,
            )
            n += 1
    s.flush()
    return n


def load_bars(s: Session, isin: str, end: date | None = None, limit: int = 320) -> list[Bar]:
    q = select(PriceEod).where(PriceEod.isin == isin)
    if end:
        q = q.where(PriceEod.day <= end)
    rows = s.scalars(q.order_by(PriceEod.day.desc()).limit(limit)).all()
    rows.reverse()
    return [Bar(r.open, r.high, r.low, r.close, r.volume) for r in rows]


def last_eod_day(s: Session, isin: str) -> date | None:
    return s.scalar(select(func.max(PriceEod.day)).where(PriceEod.isin == isin))


@dataclass(frozen=True)
class GapReport:
    isin: str
    missing: list[date]


def detect_gaps(s: Session, isin: str, venue: VenueCalendar, start: date, end: date) -> GapReport:
    """Sessions of the venue calendar without an EOD row (holidays of *that* venue excluded)."""
    have = {
        r
        for r in s.scalars(
            select(PriceEod.day).where(PriceEod.isin == isin, PriceEod.day >= start, PriceEod.day <= end)
        )
    }
    expected = venue.sessions_between(start, end)
    return GapReport(isin, [d for d in expected if d not in have])


def adv_eur_20(bars: list[Bar], fx_rate: float = 1.0) -> float | None:
    if len(bars) < 20:
        return None
    return sum(b.close * b.volume for b in bars[-20:]) / 20 * fx_rate


def compute_features_for_day(s: Session, day: date, isins: list[str], gap_flags: dict[str, bool] | None = None) -> int:
    """Compute per-instrument features then the RS percentile rank across ``isins`` (the universe)."""
    feats: dict[str, object] = {}
    scores: list[float] = []
    for isin in isins:
        bars = load_bars(s, isin, end=day)
        if not bars:
            continue
        f = compute_daily_features(bars)
        feats[isin] = f
        if f.rs_score is not None:
            scores.append(f.rs_score)
    n = 0
    for isin, f in feats.items():
        rank = percentile_rank(f.rs_score, scores) if f.rs_score is not None and scores else None  # type: ignore[attr-defined]
        row = s.scalar(select(FeatureDaily).where(FeatureDaily.isin == isin, FeatureDaily.day == day))
        if row is None:
            row = FeatureDaily(isin=isin, day=day)
            s.add(row)
        for k in (
            "atr14",
            "adr20",
            "mm10",
            "mm20",
            "mm50",
            "mm200",
            "rvol",
            "rs_1m",
            "rs_3m",
            "rs_6m",
            "rs_12m",
            "rs_score",
            "pivot_60",
            "high_52w",
            "consolidation_20",
            "ti65",
            "rsi14",
            "sessions",
        ):
            setattr(row, k, getattr(f, k))
        row.rs_rank = rank
        row.gap_in_data = bool((gap_flags or {}).get(isin, False))
        row.computed_at = datetime.now(UTC)
        n += 1
    s.flush()
    return n


def latest_features(s: Session, isins: list[str] | None = None) -> dict[str, FeatureDaily]:
    out: dict[str, FeatureDaily] = {}
    q = select(FeatureDaily).order_by(FeatureDaily.day.desc(), FeatureDaily.id.desc())
    if isins is not None:
        q = q.where(FeatureDaily.isin.in_(isins))
    for r in s.scalars(q):
        out.setdefault(r.isin, r)
    return out


def upsert_fx(s: Session, pair: str, day: date, rate: float, source: str) -> None:
    row = s.scalar(select(FxRate).where(FxRate.pair == pair, FxRate.day == day))
    if row is None:
        s.add(FxRate(pair=pair, day=day, rate=rate, source=source))
    else:
        row.rate, row.source = rate, source


def fx_to_eur(s: Session, currency: str, day: date | None = None) -> float | None:
    """EUR per unit of ``currency``; ``None`` if unknown (never guessed)."""
    if currency == "EUR":
        return 1.0
    q = select(FxRate).where(FxRate.pair == f"EUR{currency}")
    if day:
        q = q.where(FxRate.day <= day)
    row = s.scalar(q.order_by(FxRate.day.desc()))
    return (1.0 / row.rate) if row and row.rate else None


def instruments_in_universe(s: Session) -> list[Instrument]:
    return list(s.scalars(select(Instrument).where(Instrument.in_universe.is_(True), Instrument.active.is_(True))))
