"""Portfolio risk caps of the management framework (docs/07 §7.9.3, docs/22 R8, R9, R21). Pure.

Convention B (governs the cap): Σ qty × max(0, PRU − stop), floored at zero per line, SRD lines
weighted (gap risk). Convention A (indicator): Σ qty × (price − stop) = gains still exposed.
The cap depends on the market gate; a line may not exceed a share of the cap; a reserved budget
stays out of reach of lines older than ``reserved_age_sessions`` so a fresh signal always fits.
Positions in the sanctuarised pocket or held for tax harvesting are excluded.
A cap may be exceeded only through an explicit, per-proposal user override (R21).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class HeatParams:
    capital_eur: float
    cap_pct_by_gate: dict[str, float]  # risk.max_open_risk_pct_by_gate
    line_pct_of_cap: float  # risk.max_line_risk_pct_of_cap
    reserved_pct: float  # risk.reserved_signal_budget_pct (of capital)
    reserved_age_sessions: int  # risk.reserved_budget_age_sessions
    srd_weight: float  # risk.srd_risk_weight
    heat_a_soft_cap_pct: float  # risk.heat_a_soft_cap_pct


@dataclass(frozen=True)
class HeatLine:
    isin: str
    qty: int
    cost_price: float  # PRU
    price: float
    stop: float | None
    fx_rate: float = 1.0
    srd: bool = False
    age_sessions: int = 0
    excluded: str | None = None  # "dca" | "tax_harvest" → outside the caps


def heat_b(line: HeatLine, srd_weight: float) -> float:
    if line.excluded:
        return 0.0
    if line.stop is None:
        return line.qty * line.price * line.fx_rate  # unprotected: total loss possible
    risk = line.qty * max(0.0, line.cost_price - line.stop) * line.fx_rate
    return risk * (srd_weight if line.srd else 1.0)


def heat_a(line: HeatLine) -> float:
    if line.excluded or line.stop is None:
        return 0.0
    return line.qty * (line.price - line.stop) * line.fx_rate


@dataclass
class HeatState:
    gate: str
    cap_eur: float
    heat_b_eur: float
    heat_b_old_eur: float
    reserved_eur: float  # pending entry orders
    heat_a_eur: float
    heat_a_soft_cap_eur: float
    room_eur: float
    alerts: list[str] = field(default_factory=list)


def heat_state(lines: list[HeatLine], pending_reserved_eur: float, gate: str, p: HeatParams) -> HeatState:
    cap = p.capital_eur * p.cap_pct_by_gate[gate]
    hb = sum(heat_b(x, p.srd_weight) for x in lines)
    hb_old = sum(heat_b(x, p.srd_weight) for x in lines if x.age_sessions > p.reserved_age_sessions)
    ha = sum(heat_a(x) for x in lines)
    soft = p.capital_eur * p.heat_a_soft_cap_pct
    st = HeatState(gate, cap, hb, hb_old, pending_reserved_eur, ha, soft, cap - hb - pending_reserved_eur)
    reserved_budget = p.capital_eur * p.reserved_pct
    if hb_old > cap - reserved_budget:
        st.alerts.append(
            f"lignes de plus de {p.reserved_age_sessions} séances : {hb_old:.0f} € empiètent sur le budget réservé "
            f"aux signaux frais ({reserved_budget:.0f} €)"
        )
    if ha > soft:
        st.alerts.append(f"convention A {ha:.0f} € > plafond souple {soft:.0f} € (gains exposés)")
    return st


def check_new_line(
    st: HeatState, candidate_risk_eur: float, p: HeatParams, adds_to_old_line: bool = False, srd: bool = False
) -> list[str]:
    """``BLOQUÉ : …`` reasons for a new entry (or an addition) of ``candidate_risk_eur``."""
    risk = candidate_risk_eur * (p.srd_weight if srd else 1.0)
    out: list[str] = []
    after = st.heat_b_eur + st.reserved_eur + risk
    if after > st.cap_eur:
        out.append(
            f"BLOQUÉ : risque ouvert après entrée {after:.0f} € > plafond {st.cap_eur:.0f} € "
            f"(portail {st.gate}, risk.max_open_risk_pct_by_gate) — dépassement {after - st.cap_eur:.0f} €"
        )
    line_cap = st.cap_eur * p.line_pct_of_cap
    if risk > line_cap:
        out.append(
            f"BLOQUÉ : risque de la ligne {risk:.0f} € > {line_cap:.0f} € (risk.max_line_risk_pct_of_cap) — "
            f"dépassement {risk - line_cap:.0f} €"
        )
    if adds_to_old_line:
        old_cap = st.cap_eur - p.capital_eur * p.reserved_pct
        if st.heat_b_old_eur + risk > old_cap:
            out.append(
                "BLOQUÉ : renfort d'une ligne ancienne dans le budget réservé aux signaux frais "
                "(risk.reserved_signal_budget_pct)"
            )
    return out


def check_notional(notional_eur: float, capital_eur: float, max_pct: float) -> list[str]:
    if notional_eur > capital_eur * max_pct:
        return [
            f"BLOQUÉ : montant engagé {notional_eur:.0f} € = {notional_eur / capital_eur:.1%} du capital > "
            f"{max_pct:.0%} (risk.max_position_pct_of_capital)"
        ]
    return []


# --- R21: override, only with the user's explicit agreement ------------------------------------


@dataclass(frozen=True)
class OverrideParams:
    min_score: float
    min_confidence: int
    min_net_rr: float
    max_multiplier: float


def override_eligible(
    rank: int, score: float, confidence: int, net_rr: float | None, p: OverrideParams
) -> tuple[bool, list[str]]:
    """Whether the user may be *offered* an override. Never applied without their agreement."""
    missing: list[str] = []
    if rank != 1:
        missing.append(f"rang {rank} (rang 1 exigé)")
    if score < p.min_score:
        missing.append(f"score {score:.0f} < {p.min_score:.0f}")
    if confidence < p.min_confidence:
        missing.append(f"confiance {confidence}/5 < {p.min_confidence}")
    if net_rr is None or net_rr < p.min_net_rr:
        missing.append(f"ratio net {net_rr if net_rr is not None else 'inconnu'} < {p.min_net_rr}")
    return not missing, missing


def override_fits(excess_eur: float, cap_eur: float, p: OverrideParams) -> bool:
    """The overridden cap is at most ``cap × max_multiplier``."""
    return excess_eur <= cap_eur * (p.max_multiplier - 1)
