"""docs/28 — reference risk (capital × % per trade, set in the interface) and the daily risk level:
recommended each morning by the engine from the market, the portfolio, the recent results, the measured
edge and the day's opportunities; applied by default; the user may override it for the day."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import LoadedConfig
from app.db.models import PositionRow, RiskDialDaily, RiskSetting
from app.domain.risk_dial import Dial, DialInput, DialParams, recommend


@dataclass(frozen=True)
class Reference:
    capital_eur: float
    risk_pct: float  # reference risk per trade (level × 1)
    source: str  # « interface (jj/mm/aaaa) » | « params.yaml »
    max_trade_pct: float  # never more per trade, whatever the level

    @property
    def r_eur(self) -> float:
        return self.capital_eur * self.risk_pct


def _raw(config: LoadedConfig) -> dict[str, Any]:
    return ((config.raw or {}).get("risk") or {}).get("risk_dial") or {}


def dial_params(config: LoadedConfig) -> DialParams:
    raw = _raw(config)
    base = DialParams()
    kw = {k: type(getattr(base, k))(raw[k]) for k in DialParams.__dataclass_fields__ if k in raw}
    return DialParams(**kw)


def bounds(config: LoadedConfig) -> tuple[float, float]:
    raw = _raw(config)
    return float(raw.get("reference_pct_min", 0.0025)), float(raw.get("reference_pct_max", 0.015))


def reference(s: Session, config: LoadedConfig, now: datetime | None = None) -> Reference:
    now = now or datetime.now(UTC)
    p = config.params
    raw = _raw(config)
    row = s.scalar(select(RiskSetting).where(RiskSetting.effective_at <= now).order_by(RiskSetting.effective_at.desc()))
    max_pct = float(raw.get("max_trade_risk_pct", 0.01))
    if row is not None:
        at = row.effective_at.replace(tzinfo=UTC) if row.effective_at.tzinfo is None else row.effective_at
        return Reference(row.capital_pilote_eur, row.reference_risk_pct, f"interface ({at:%d/%m/%Y})", max_pct)
    return Reference(float(p.capital.capital_pilote_eur), float(p.risk.max_risk_per_trade_pct), "params.yaml", max_pct)


def set_reference(
    s: Session,
    config: LoadedConfig,
    capital_eur: float,
    risk_pct: float,
    reason: str | None,
    now: datetime | None = None,
) -> RiskSetting:
    lo, hi = bounds(config)
    if not (lo <= risk_pct <= hi):
        raise ValueError(f"risque par trade hors bornes : {lo:.2%} à {hi:.2%}")
    if capital_eur <= 0:
        raise ValueError("capital pilote nul ou négatif")
    row = RiskSetting(
        effective_at=now or datetime.now(UTC), capital_pilote_eur=capital_eur, reference_risk_pct=risk_pct,
        reason=(reason or "")[:300] or None,
    )  # fmt: skip
    s.add(row)
    s.flush()
    return row


def today(s: Session, day: date) -> RiskDialDaily | None:
    return s.scalar(select(RiskDialDaily).where(RiskDialDaily.day == day))


def set_override(s: Session, config: LoadedConfig, day: date, level: float | None, reason: str | None) -> RiskDialDaily:
    p = dial_params(config)
    row = today(s, day)
    if row is None:
        row = RiskDialDaily(day=day, recommended=1.0, computed_at=datetime.now(UTC),
                            explanation=["Niveau conseillé pas encore calculé (moteur de 07:50)"])  # fmt: skip
        s.add(row)
    if level is not None and not (p.min_level <= level <= p.max_level):
        raise ValueError(f"niveau hors bornes : × {p.min_level:g} à × {p.max_level:g}")
    row.override, row.override_reason, row.override_at = level, (reason or "")[:300] or None, datetime.now(UTC)
    s.flush()
    return row


def open_lines(s: Session) -> int:
    return len(list(s.scalars(select(PositionRow.id).where(PositionRow.closed_at.is_(None), PositionRow.qty_held > 0))))


def store_recommendation(s: Session, day: date, dial: Dial, r_eur: float, now: datetime) -> RiskDialDaily:
    row = today(s, day) or RiskDialDaily(day=day, recommended=dial.level, computed_at=now)
    row.recommended, row.computed_at = dial.level, now
    row.components = [{"composante": c.label, "facteur": c.factor, "pourquoi": c.why} for c in dial.components]
    row.explanation = dial.explanation(r_eur)
    s.add(row)
    s.flush()
    return row


def compute(
    s: Session,
    config: LoadedConfig,
    day: date,
    regime: str | None,
    conjuncture: str | None,
    circuit: Any | None,
    strong_setups: int,
    edge_measured: bool,
    now: datetime,
) -> tuple[float, RiskDialDaily]:
    """Recommend, store, and return the level that applies (the user's override when there is one)."""
    mult, why = 1.0, ""
    if circuit is not None and not circuit.tripped and circuit.size_multiplier < 1:
        mult, why = float(circuit.size_multiplier), circuit.reason
    dial = recommend(
        DialInput(regime, conjuncture, open_lines(s), strong_setups, mult, why, edge_measured), dial_params(config)
    )
    row = store_recommendation(s, day, dial, reference(s, config, now).r_eur, now)
    return row.applied, row


def _rule_key(text: str) -> str:
    """« BLOQUÉ : coupe-circuit : −4.3 R … » → « BLOQUÉ : coupe-circuit » (the rule, not the numbers)."""
    import re

    head = re.split(r" — |\(|[−+-]?\d", text, maxsplit=1)[0]
    return head.strip(" :;,.")[:70]


def guard_costs(s: Session, days: int = 60, now: datetime | None = None) -> list[dict[str, Any]]:
    """docs/28 — what each blocking / WATCH rule costs: proposals it stopped and the simulated result of
    those proposals (docs/18 outcomes, measured whether taken or not). A rule that mostly stops winners
    is shown as such — the protection must not turn into immobility."""
    from datetime import timedelta

    from app.db.models import Proposal, SignalOutcome

    now = now or datetime.now(UTC)
    rows: dict[str, dict[str, Any]] = {}
    for p in s.scalars(select(Proposal).where(Proposal.created_at >= now - timedelta(days=days))):
        blocked = [b for b in (p.gates or {}).get("blocked", []) if b.startswith(("BLOQUÉ", "WATCH"))]
        if not blocked:
            continue
        o = s.scalar(select(SignalOutcome).where(SignalOutcome.proposal_id == p.id))
        for key in {_rule_key(b) for b in blocked}:
            r = rows.setdefault(key, {"regle": key, "propositions": 0, "issues": 0, "gagnantes": 0, "somme_r": 0.0})
            r["propositions"] += 1
            if o is not None and o.final and o.r_net is not None:
                r["issues"] += 1
                r["gagnantes"] += o.r_net > 0
                r["somme_r"] += o.r_net
    out = []
    for r in rows.values():
        n = r["issues"]
        r["r_moyen"] = round(r["somme_r"] / n, 3) if n else None
        r["part_gagnantes"] = round(r["gagnantes"] / n, 3) if n else None
        r["alerte"] = bool(n >= 10 and r["r_moyen"] is not None and r["r_moyen"] > 0.1)
        out.append(r)
    return sorted(out, key=lambda r: -r["propositions"])
