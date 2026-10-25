"""docs/07 §7.3 — circuit breaker on the week's and month's losses (realised + change of latent), in
nominal R. Baselines are the consolidated equity snapshots (``kpi_daily``) of the last day before the
period; without a baseline the measure is « indisponible » and shown as such, never estimated."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import LoadedConfig
from app.db.models import KpiDaily, Trade
from app.domain.risk import CircuitBreakerParams, RollingParams, circuit_breaker, period_pnl_r, rolling_breaker
from app.domain.timeutil import PARIS


@dataclass
class CircuitView:
    tripped: bool = False
    reason: str = ""
    size_multiplier: float = 1.0
    week_r: float | None = None
    month_r: float | None = None
    r_unit_eur: float = 0.0
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "declenche": self.tripped,
            "motif": self.reason,
            "multiplicateur": self.size_multiplier,
            "semaine_r": None if self.week_r is None else round(self.week_r, 2),
            "mois_r": None if self.month_r is None else round(self.month_r, 2),
            "r_nominal_eur": round(self.r_unit_eur, 2),
            "notes": self.notes,
        }


def _baseline(s: Session, before: date) -> KpiDaily | None:
    return s.scalar(
        select(KpiDaily)
        .where(KpiDaily.account_id.is_(None), KpiDaily.day < before)
        .order_by(KpiDaily.day.desc())
        .limit(1)
    )


def _trades_before(s: Session, start: date) -> bool:
    ts = datetime.combine(start, datetime.min.time(), tzinfo=PARIS).astimezone(UTC)
    return s.scalar(select(Trade.id).where(Trade.deleted_at.is_(None), Trade.ts < ts).limit(1)) is not None


def _start_values(s: Session, start: date) -> tuple[float, float] | None:
    row = _baseline(s, start)
    if row is not None and row.latent_pnl is not None and row.realized_pnl_cum is not None:
        return float(row.realized_pnl_cum), float(row.latent_pnl)
    if not _trades_before(s, start):
        return 0.0, 0.0  # nothing held before the period: the baseline is exactly zero
    return None


def _loss(s: Session, start: date, end: date, r_unit: float) -> float | None:
    """Result in R between the snapshot before ``start`` and the last snapshot before ``end``."""
    a, b = _start_values(s, start), _baseline(s, end)
    if a is None or b is None or b.latent_pnl is None or b.realized_pnl_cum is None or b.day < start:
        return None
    return period_pnl_r(float(b.realized_pnl_cum), float(b.latent_pnl), a[0], a[1], r_unit)


def circuit_state(
    s: Session, config: LoadedConfig, now: datetime | None = None, current: tuple[float, float] | None = None
) -> CircuitView:
    """``current`` = (realised cumulative, latent) now; computed from the positions when omitted."""
    p = config.params
    cb = p.risk.circuit_breaker
    params = CircuitBreakerParams(float(cb.weekly_loss_r), float(cb.monthly_loss_r), float(cb.resume_size_multiplier))
    r_unit = float(p.capital.capital_pilote_eur) * float(p.risk.max_risk_per_trade_pct)
    view = CircuitView(r_unit_eur=r_unit)
    today = (now or datetime.now(UTC)).astimezone(PARIS).date()
    week0 = today - timedelta(days=today.weekday())
    month0 = today.replace(day=1)
    if current is None:
        from app.services.performance import build_performance

        v = build_performance(s, config)["v"]
        current = (v.realized_total, v.latent_total)
        if v.incomplete:
            view.notes.append("latent partiel : " + " ; ".join(v.incomplete[:3]))
    sw, sm = _start_values(s, week0), _start_values(s, month0)
    if sw is not None:
        view.week_r = period_pnl_r(current[0], current[1], sw[0], sw[1], r_unit)
    if sm is not None:
        view.month_r = period_pnl_r(current[0], current[1], sm[0], sm[1], r_unit)
    if sw is None or sm is None:
        view.notes.append("mesure indisponible : pas d'instantané d'équité avant la période (job kpi_snapshot)")
    prev_week = _loss(s, week0 - timedelta(days=7), week0, r_unit)
    prev_tripped = prev_week is not None and prev_week <= -params.weekly_loss_r
    if today.day <= 7:  # first week of a month after a monthly trip is also a resume week
        prev_month0 = (month0 - timedelta(days=1)).replace(day=1)
        pm = _loss(s, prev_month0, month0, r_unit)
        prev_tripped = prev_tripped or (pm is not None and pm <= -params.monthly_loss_r)
    st = circuit_breaker(view.week_r or 0.0, 0.0, view.month_r or 0.0, 0.0, params, prev_tripped)
    view.tripped, view.reason, view.size_multiplier = st.tripped, st.reason, st.size_multiplier
    extra = cb.model_extra or {}
    rp = RollingParams(**{k: float(extra[k]) for k in RollingParams.__dataclass_fields__ if k in extra})
    rows = list(
        s.scalars(
            select(KpiDaily)
            .where(KpiDaily.account_id.is_(None), KpiDaily.day < today)
            .order_by(KpiDaily.day.desc())
            .limit(21)
        )
    )[::-1]
    totals = [
        float(r.realized_pnl_cum) + float(r.latent_pnl)
        for r in rows
        if r.realized_pnl_cum is not None and r.latent_pnl is not None
    ]
    tripped, mult, why = rolling_breaker(totals, current[0] + current[1], r_unit, rp)
    if not view.tripped and tripped:
        view.tripped, view.reason, view.size_multiplier = True, why, 0.0
    elif not view.tripped and mult < view.size_multiplier:
        view.size_multiplier, view.reason = mult, why
    if not view.tripped and (sw is None or sm is None):  # a guard never fails open: half size until measured
        miss = float(extra.get("missing_baseline_multiplier", 0.5))
        if miss < view.size_multiplier:
            view.size_multiplier = miss
            view.reason = f"mesure du coupe-circuit indisponible : tailles × {miss:g} jusqu'au prochain instantané"
    return view
