"""BoursoBank formulas (params ``brokers.boursobank.formulas``): the fee schedule of the current formula
for the engine, and the comparison of the formulas on your real orders — or, before enough history, on the
engine's proposals — with a recommendation (retour d'expérience)."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import LoadedConfig
from app.db.models import Account, Proposal, Trade
from app.domain.broker_plan import Formula, PlanComparison, compare
from app.domain.risk import FeeSchedule
from app.domain.timeutil import PARIS


def _bb(config: LoadedConfig) -> dict[str, Any]:
    return (config.params.brokers or {}).get("boursobank") or {}


def _bands(raw: list[list[Any]]) -> tuple[tuple[float | None, float, float, float], ...]:
    out = []
    for b in raw or []:
        upper, fixed, pct = b[0], float(b[1] or 0), float(b[2] or 0)
        out.append((None if upper is None else float(upper), fixed, pct, float(b[3]) if len(b) > 3 and b[3] else 0.0))
    return tuple(out)


def formulas(config: LoadedConfig, pea: bool = True) -> list[Formula]:
    bb = _bb(config)
    cap = float(bb.get("pea_cap_pct", 0.005)) if pea else None
    out = []
    for name, f in (bb.get("formulas") or {}).items():
        avail = f.get("pea", True) if pea else f.get("cto", True)
        out.append(
            Formula(
                str(name),
                str(f.get("label", name)),
                FeeSchedule("boursobank", None, 0.0, cap, _bands(f.get("bands"))),
                float(f.get("fee_if_no_order", 0) or 0),
                int(f.get("min_orders", 0) or 0),
                float(f.get("fee_if_below", 0) or 0),
                bool(avail) if avail is not None else True,
                str(f.get("note", "") or ""),
            )
        )
    return out


def current_fees(config: LoadedConfig, pea: bool = True) -> FeeSchedule | None:
    """Fee schedule of the current formula, or None when the formulas are not configured (legacy keys)."""
    cur = _bb(config).get("current_formula")
    for f in formulas(config, pea):
        if f.name == cur:
            return f.fees
    return None


def _pea_months(s: Session, now: datetime, months: int) -> list[list[float]]:
    pea_ids = {a.id for a in s.scalars(select(Account).where(Account.type == "pea"))}
    start = (now.astimezone(PARIS).replace(day=1) - timedelta(days=31 * (months - 1))).replace(day=1)
    by: dict[str, list[float]] = defaultdict(list)
    for t in s.scalars(select(Trade).where(Trade.deleted_at.is_(None), Trade.kind == "confirmed")):
        if t.account_id not in pea_ids:
            continue
        ts = t.ts.replace(tzinfo=UTC) if t.ts.tzinfo is None else t.ts
        local = ts.astimezone(PARIS)
        if local >= start:
            by[local.strftime("%Y-%m")].append(t.qty * t.price * (t.fx_rate or 1.0))
    keys = []
    d = start
    for _ in range(months):
        keys.append(d.strftime("%Y-%m"))
        d = (d + timedelta(days=32)).replace(day=1)
    return [by.get(k, []) for k in keys]


def analysis(s: Session, config: LoadedConfig, now: datetime | None = None) -> dict[str, Any] | None:
    """Comparison on the real PEA orders of the last months; with fewer than ``min_real_orders`` orders, on
    the engine's PEA proposals of the last 30 days (each one = a buy and a sell, ``assumed_take_rate``
    of them taken). Shown on /performance and /apprentissage."""
    now = now or datetime.now(UTC)
    bb = _bb(config)
    fs = formulas(config, pea=True)
    if not fs:
        return None
    pp = bb.get("plan_review") or {}
    months_n = int(pp.get("months", 3))
    months = _pea_months(s, now, months_n)
    real = sum(len(m) for m in months)
    basis = "vos ordres réels sur le PEA"
    if real < int(pp.get("min_real_orders", 6)):
        sizes = []
        for p in s.scalars(select(Proposal).where(Proposal.created_at >= now - timedelta(days=30))):
            acc = ((p.details or {}).get("comptes") or {}).get("pea") or {}
            if p.action == "BUY" and p.account_recommended == "pea" and acc.get("montant_eur"):
                sizes.append(float(acc["montant_eur"]))
        if sizes:
            take = float(pp.get("assumed_take_rate", 0.5))
            per_month = max(1, round(2 * len(sizes) * take))
            months = [[median(sizes)] * per_month]
            basis = (
                f"projection : {len(sizes)} achat(s) PEA proposé(s) sur 30 jours, {take:.0%} pris, "
                f"achat + vente, montant médian {median(sizes):.0f} €"
            )
        elif real == 0:
            return {"basis": "aucun ordre ni proposition PEA pour l'instant", "comparison": None, "formulas": fs}
    cmp_: PlanComparison = compare(fs, months, str(bb.get("current_formula", "")), float(pp.get("min_saving_eur", 5)))
    typical = median([x for m in months for x in m]) if any(months) else None
    return {
        "basis": basis,
        "comparison": cmp_,
        "formulas": fs,
        "typical_order": typical,
        "per_order": {f.name: f.fees.commission(typical) for f in fs} if typical else {},
        "source": str(bb.get("formulas_source", "")),
    }
