"""Sector and ETF rotation (docs/22 §22.4 family C): relative strength of the sectors (median rank
of their stocks) and of the ETFs of the universe. Computed from stored ranks only; the strong
sectors add points to block M of the score, the strong ETFs go through the same detectors."""

from __future__ import annotations

import statistics
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import FeatureDaily, Instrument


def _latest_feats(s: Session, isins: list[str], day: date) -> dict[str, FeatureDaily]:
    out: dict[str, FeatureDaily] = {}
    if not isins:
        return out
    for f in s.scalars(
        select(FeatureDaily).where(FeatureDaily.isin.in_(isins), FeatureDaily.day <= day).order_by(FeatureDaily.day)
    ):
        out[f.isin] = f
    return out


def sector_strength(s: Session, day: date, min_members: int = 3) -> dict[str, dict[str, Any]]:
    """{sector: {"median_rang", "membres", "centile"}} — percentile of the sector's median rank
    among sectors (60 or more = sector « porteur », +3 in block M)."""
    stocks = list(s.scalars(select(Instrument).where(Instrument.in_universe.is_(True), Instrument.kind == "stock")))
    feats = _latest_feats(s, [i.isin for i in stocks], day)
    by: dict[str, list[float]] = {}
    for i in stocks:
        f = feats.get(i.isin)
        if i.sector and f is not None and f.rs_rank is not None:
            by.setdefault(i.sector, []).append(float(f.rs_rank))
    med = {k: statistics.median(v) for k, v in by.items() if len(v) >= min_members}
    ordered = sorted(med.values())
    out = {}
    for k, m in med.items():
        below = sum(1 for x in ordered if x < m)
        out[k] = {
            "median_rang": round(m, 1),
            "membres": len(by[k]),
            "centile": round(100 * below / max(1, len(ordered) - 1), 1),
        }
    return dict(sorted(out.items(), key=lambda kv: -kv[1]["median_rang"]))


def etf_ranking(s: Session, day: date, limit: int = 30) -> list[dict[str, Any]]:
    etfs = list(s.scalars(select(Instrument).where(Instrument.in_universe.is_(True), Instrument.kind == "etf")))
    feats = _latest_feats(s, [i.isin for i in etfs], day)
    rows = []
    for i in etfs:
        f = feats.get(i.isin)
        if f is None or f.rs_rank is None:
            continue
        rows.append(
            {
                "isin": i.isin,
                "nom": i.name,
                "rang": f.rs_rank,
                "perf_1m": f.rs_1m,
                "perf_3m": f.rs_3m,
                "perf_6m": f.rs_6m,
                "pea": i.pea_eligible,
                "pea_source": i.pea_source,
                "jour": f.day,
            }
        )
    rows.sort(key=lambda r: -float(r["rang"]))
    n = len(rows)
    for k, r in enumerate(rows):
        r["porteur"] = k < max(1, n // 5)
    return rows[:limit]
