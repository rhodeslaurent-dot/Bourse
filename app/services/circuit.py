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
    unavailable: bool = False  # no complete equity snapshot before the week or the month: the measure is missing
    blocks_when_unavailable: bool = True  # decision of 04/11/2026: a missing measure blocks every new entry

    def as_dict(self) -> dict[str, Any]:
        return {
            "declenche": self.tripped,
            "motif": self.reason,
            "multiplicateur": self.size_multiplier,
            "semaine_r": None if self.week_r is None else round(self.week_r, 2),
            "mois_r": None if self.month_r is None else round(self.month_r, 2),
            "r_nominal_eur": round(self.r_unit_eur, 2),
            "notes": self.notes,
            "indisponible": self.unavailable,
        }


def _active(row: KpiDaily) -> tuple[float, float]:
    """(realised cumulative, latent) of the active part: the consolidated row minus the core pocket (R17, docs/39 —
    a fall of the world index on the core never blocks the active part). Rows written before lot C1 hold NULL: 0."""
    return (
        float(row.realized_pnl_cum) - float(getattr(row, "core_realized_pnl_cum", None) or 0.0),
        float(row.latent_pnl) - float(getattr(row, "core_latent_pnl", None) or 0.0),
    )


def _pnl_complete(row: KpiDaily) -> bool:
    """The P&L of the snapshot is whole (18/11/2026): realised and latent known, no line without price or rate.
    Unknown cash (a declarative PEA never declared) leaves the P&L whole; a row written before migration 0039
    (``pnl_incomplete`` NULL) is read by its ``incomplete`` flag — never assumed complete."""
    if row.realized_pnl_cum is None or row.latent_pnl is None:
        return False
    flag = getattr(row, "pnl_incomplete", None)
    return not (row.incomplete if flag is None else flag)


def _rebase(config: LoadedConfig) -> date | None:
    """``risk.circuit_breaker.baseline_from`` (a user decision, traced): snapshots before this day are not read by
    the circuit breaker — they were valued with a line tied to another company's prices (18/11/2026)."""
    raw = (config.params.risk.circuit_breaker.model_extra or {}).get("baseline_from")
    return date.fromisoformat(str(raw)) if raw else None


def _baseline(s: Session, before: date, rebase: date | None = None) -> KpiDaily | None:
    q = select(KpiDaily).where(KpiDaily.account_id.is_(None), KpiDaily.day < before)
    if rebase is not None:
        q = q.where(KpiDaily.day >= rebase)
    return s.scalar(q.order_by(KpiDaily.day.desc()).limit(1))


def _first_after_rebase(s: Session, rebase: date) -> KpiDaily | None:
    """With a rebase, a period that began before it is measured from the first complete snapshot of the rebase."""
    rows = s.scalars(select(KpiDaily).where(KpiDaily.account_id.is_(None), KpiDaily.day >= rebase)
                     .order_by(KpiDaily.day))  # fmt: skip
    return next((r for r in rows if _pnl_complete(r)), None)


def _trades_before(s: Session, start: date) -> bool:
    ts = datetime.combine(start, datetime.min.time(), tzinfo=PARIS).astimezone(UTC)
    return s.scalar(select(Trade.id).where(Trade.deleted_at.is_(None), Trade.ts < ts).limit(1)) is not None


def _start_values(s: Session, start: date, rebase: date | None = None) -> tuple[float, float] | None:
    """Snapshot of the last session before ``start``: at most 4 days old and complete, else unavailable."""
    row = _baseline(s, start, rebase)
    if row is not None and _pnl_complete(row) and (start - row.day).days <= 4:
        return _active(row)
    if rebase is not None and start <= rebase:
        first = _first_after_rebase(s, rebase)
        return _active(first) if first is not None else None
    if not _trades_before(s, start):
        return 0.0, 0.0  # nothing held before the period: the baseline is exactly zero
    return None


def _worst(
    s: Session,
    start: date,
    end: date,
    r_unit: float,
    extra: tuple[float, float] | None = None,
    rebase: date | None = None,
) -> float | None:
    """Worst result in R of the period [start, end) over its daily snapshots (and ``extra`` = now): once the
    limit is passed, the period stays tripped even if the latent recovers (docs/07 §7.3 « jusqu'au lundi »)."""
    a = _start_values(s, start, rebase)
    if a is None:
        return None
    vals = [
        period_pnl_r(*_active(r), a[0], a[1], r_unit)
        for r in s.scalars(
            select(KpiDaily).where(KpiDaily.account_id.is_(None), KpiDaily.day >= start, KpiDaily.day < end)
        )
        if _pnl_complete(r) and (rebase is None or r.day >= rebase)
    ]
    if extra is not None:
        vals.append(period_pnl_r(extra[0], extra[1], a[0], a[1], r_unit))
    return min(vals) if vals else None


def _loss(s: Session, start: date, end: date, r_unit: float, rebase: date | None = None) -> float | None:
    return _worst(s, start, end, r_unit, None, rebase)


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
    rebase = _rebase(config)
    if rebase is not None and rebase > month0 - timedelta(days=31):
        view.notes.append(f"mesure rebasée au {rebase:%d/%m} (risk.circuit_breaker.baseline_from, décision de "
                          "l'utilisateur) : les instantanés d'avant ne sont pas lus")  # fmt: skip
    if current is None:
        from app.services.performance import build_performance

        v = build_performance(s, config)["v"]
        current = (v.realized_total - v.core_realized, v.latent_total - v.core_latent)  # R17: the active part
        if v.incomplete:
            view.notes.append("latent partiel : " + " ; ".join(v.incomplete[:3]))
    sw, sm = _start_values(s, week0, rebase), _start_values(s, month0, rebase)
    if sw is not None:
        view.week_r = period_pnl_r(current[0], current[1], sw[0], sw[1], r_unit)
    if sm is not None:
        view.month_r = period_pnl_r(current[0], current[1], sm[0], sm[1], r_unit)
    if sw is None or sm is None:
        view.unavailable = True
        view.notes.append(
            "mesure indisponible : pas d'instantané d'équité complet avant la "
            + ("semaine et le mois" if sw is None and sm is None else "semaine" if sw is None else "mois")
            + " (job kpi_snapshot ; un latent partiel le rend incomplet)"
        )
    prev_week = _loss(s, week0 - timedelta(days=7), week0, r_unit, rebase)
    prev_tripped = prev_week is not None and prev_week <= -params.weekly_loss_r
    if today.day <= 7:  # first week of a month after a monthly trip is also a resume week
        prev_month0 = (month0 - timedelta(days=1)).replace(day=1)
        pm = _loss(s, prev_month0, month0, r_unit, rebase)
        prev_tripped = prev_tripped or (pm is not None and pm <= -params.monthly_loss_r)
    worst_w = _worst(s, week0, today, r_unit, current, rebase)
    worst_m = _worst(s, month0, today, r_unit, current, rebase)
    st = circuit_breaker(worst_w or 0.0, 0.0, worst_m or 0.0, 0.0, params, prev_tripped)
    view.tripped, view.reason, view.size_multiplier = st.tripped, st.reason, st.size_multiplier
    extra = cb.model_extra or {}
    rp = RollingParams(**{k: float(extra[k]) for k in RollingParams.__dataclass_fields__ if k in extra})
    q_roll = select(KpiDaily).where(KpiDaily.account_id.is_(None), KpiDaily.day < today)
    if rebase is not None:
        q_roll = q_roll.where(KpiDaily.day >= rebase)
    rows = list(s.scalars(q_roll.order_by(KpiDaily.day.desc()).limit(21)))[::-1]
    totals = [sum(_active(r)) for r in rows if r.realized_pnl_cum is not None and r.latent_pnl is not None]
    tripped, mult, why = rolling_breaker(totals, current[0] + current[1], r_unit, rp)
    if not view.tripped and tripped:
        view.tripped, view.reason, view.size_multiplier = True, why, 0.0
    elif not view.tripped and mult < view.size_multiplier:
        view.size_multiplier, view.reason = mult, why
    view.blocks_when_unavailable = bool(extra.get("missing_baseline_blocks", True))
    if not view.tripped and view.unavailable and not view.blocks_when_unavailable:  # never fails open: reduced
        miss = float(extra.get("missing_baseline_multiplier", 0.75))
        if miss < view.size_multiplier:
            view.size_multiplier = miss
            view.reason = f"mesure du coupe-circuit indisponible : tailles × {miss:g} jusqu'au prochain instantané"
    return view


def trace(s: Session, config: LoadedConfig, now: datetime | None = None) -> list[str]:
    """18/11/2026 — every proposal of the week said « mesure indisponible » (the decision of 04/11/2026: a missing
    measure blocks every new entry). Why the measure is missing, from stored facts only: the baseline snapshots it
    needs (the last session before the week and before the month, complete, at most 4 days old), each consolidated
    snapshot since and what made it incomplete, and what is missing now. No amount is printed. Read only."""
    from app.db.models import Account
    from app.services.performance import build_performance

    today = (now or datetime.now(UTC)).astimezone(PARIS).date()
    week0 = today - timedelta(days=today.weekday())
    month0 = today.replace(day=1)
    view = circuit_state(s, config, now)
    blocking = "BLOQUANT — " if view.unavailable and view.blocks_when_unavailable else ""
    out = [f"coupe-circuit au {today:%d/%m} : {blocking}" + ("; ".join(view.notes) or "mesure disponible")]
    out.append(f"  semaine : {'—' if view.week_r is None else f'{view.week_r:+.2f} R'} · mois : "
               f"{'—' if view.month_r is None else f'{view.month_r:+.2f} R'} · déclenché : "
               f"{'oui — ' + view.reason if view.tripped else 'non'}")  # fmt: skip
    accounts = {a.id: a for a in s.scalars(select(Account))}

    def why(day: date) -> str:
        parts = []
        for r in s.scalars(select(KpiDaily).where(KpiDaily.day == day, KpiDaily.account_id.is_not(None))):
            a = accounts.get(int(r.account_id or 0))
            name = a.type if a is not None else f"compte {r.account_id}"
            if r.incomplete:
                parts.append(f"{name} : " + ("liquidités inconnues" if r.cash is None else "position sans cours"))
        return ", ".join(parts) or "latent partiel (taux de change ou cours d'une ligne)"

    rebase = _rebase(config)
    for label, start in (("semaine", week0), ("mois", month0)):
        b = _baseline(s, start, rebase)
        first = _first_after_rebase(s, rebase) if rebase is not None and start <= rebase else None
        if b is None and rebase is not None and start <= rebase:  # the period began before the rebase
            state = (f"{first.day:%d/%m} : premier instantané complet du rebasage" if first is not None
                     else "rebasage sans instantané complet encore")  # fmt: skip
        elif b is None:
            state = "aucun instantané avant" + ("" if _trades_before(s, start) else " (rien détenu : base zéro)")
        else:
            age = (start - b.day).days
            equity = "équité complète" if not b.incomplete else f"équité partielle ({why(b.day)})"
            pnl = "résultat complet" if _pnl_complete(b) else (
                "résultat INCOMPLET" + (" (instantané d'avant le 18/11/2026 : lu comme l'équité)"
                                       if getattr(b, "pnl_incomplete", None) is None else ""))  # fmt: skip
            state = f"{b.day:%d/%m} : {pnl}, {equity}" + (f", trop ancien ({age} j > 4)" if age > 4 else "")
        out.append(f"  référence {label} (avant le {start:%d/%m}) : {state}")
    since = min(week0, month0) - timedelta(days=10)
    rows = list(s.scalars(select(KpiDaily).where(KpiDaily.account_id.is_(None), KpiDaily.day >= since)
                          .order_by(KpiDaily.day)))  # fmt: skip
    bad = [r for r in rows if not _pnl_complete(r)]
    first_ok = next((f"{r.day:%d/%m}" for r in rows if r not in bad), "aucun")
    out.append(f"  instantanés consolidés depuis le {since:%d/%m} : {len(rows)}, dont {len(bad)} au résultat "
               f"incomplet — premier complet : {first_ok}")  # fmt: skip
    for r in bad[-12:]:
        out.append(f"    {r.day:%d/%m} : {why(r.day)}")
    # the active part's result day by day, in R from the first day shown (never an amount): a jump without a market
    # move is a valuation change (a line re-tied to its company, a price corrected)
    shown = [r for r in rows if r.realized_pnl_cum is not None and r.latent_pnl is not None][-10:]
    if shown and view.r_unit_eur:
        base = sum(_active(shown[0]))
        series = " · ".join(f"{r.day:%d/%m} {(sum(_active(r)) - base) / view.r_unit_eur:+.1f}" for r in shown)
        out.append(f"  résultat de la partie active, en R depuis le {shown[0].day:%d/%m} : {series}")
    if rebase is not None:
        out.append(f"  rebasage : instantanés d'avant le {rebase:%d/%m} ignorés (risk.circuit_breaker.baseline_from)")
    v = build_performance(s, config)["v"]
    out.append("  maintenant : " + ("; ".join(v.incomplete[:6]) or "aucun cours ni taux manquant"))
    nocash = [a.type for a in accounts.values() if a.cash is None]
    if nocash:
        out.append(
            "  liquidités jamais renseignées : " + ", ".join(nocash) + " (l'instantané du compte reste incomplet)"
        )
    return out
