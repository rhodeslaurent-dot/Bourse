"""Risk engine (docs/07 §7.2–7.3, CLAUDE.md rule 7). Pure, deterministic, parameterised.

Sizing on the **maximum entry price** and the **slipped exit stop**; fees iterated to a fixed point;
FX haircut reduces the quantity, never the risk budget. Portfolio caps use deterministic
definitions of initial / current (floored at zero per position) / reserved risk. Every violated
rule is returned as ``BLOQUÉ : <règle>`` — displayed, never hidden.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum

# --- fees --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class FeeSchedule:
    """Commission per leg: ``pct`` of notional with a floor and an optional cap (PEA 0,5 %)."""

    broker: str
    commission_pct: float | None
    commission_min_eur: float
    commission_cap_pct: float | None = None

    @property
    def known(self) -> bool:
        return self.commission_pct is not None

    def commission(self, notional_eur: float) -> float:
        if self.commission_pct is None:
            return 0.0
        fee = max(notional_eur * self.commission_pct, self.commission_min_eur)
        if self.commission_cap_pct is not None:
            fee = min(fee, notional_eur * self.commission_cap_pct)
        return fee


def estimate_round_trip_fees(notional_eur: float, fees: FeeSchedule, ttf_rate: float) -> float:
    """Courtage aller + retour + TTF sur l'achat (docs/07 §7.2). CRD is an horizon cost (docs/08)."""
    return 2 * fees.commission(notional_eur) + notional_eur * ttf_rate


# --- sizing --------------------------------------------------------------------------------------


class EntryOrderKind(StrEnum):
    LIMIT = "limite"  # prix borné → prix_max = limite, pas de glissement d'entrée
    STOP_LIMIT = "plage"  # prix borné → prix_max = limite
    MARKET = "marche"  # non borné → prix_max = niveau × (1 + slippage)
    STOP = "seuil"  # non borné


@dataclass(frozen=True)
class SizingInput:
    capital_eur: float
    max_risk_pct: float  # risk.max_risk_per_trade_pct
    entry_level: float  # niveau de cassure / limite, devise de cotation
    stop_initial: float  # stop structurel, devise de cotation
    order_kind: EntryOrderKind
    slippage_pct: float  # risk.slippage_pct
    fees: FeeSchedule
    max_position_pct_of_capital: float  # risk.max_position_pct_of_capital (no default: params only)
    max_position_pct_of_adv: float  # risk.max_position_pct_of_adv
    ttf_rate: float = 0.0  # 0 si TTF non due
    fx_rate: float = 1.0  # devise → EUR
    fx_haircut: float = 0.0  # risk.fx_haircut si devise ≠ EUR, sinon 0
    adv20_eur: float | None = None
    size_multiplier: float = 1.0  # régime orange × 0,5, risque élevé × 0,5 (cumulables)


@dataclass
class SizingResult:
    shares: int
    price_max: float
    stop_exec: float
    r_per_share_eur: float
    risk_budget_eur: float
    fees_estimated_eur: float
    notional_max_eur: float
    risk_if_filled_at_max_eur: float
    blocked: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    iterations: int = 0

    @property
    def ok(self) -> bool:
        return not self.blocked and self.shares > 0


def price_max_for(entry_level: float, order_kind: EntryOrderKind, slippage_pct: float) -> float:
    """Bounded orders (limite / plage) have no entry slippage; market / stop orders do."""
    if order_kind in (EntryOrderKind.LIMIT, EntryOrderKind.STOP_LIMIT):
        return entry_level
    return entry_level * (1 + slippage_pct)


def stop_exec_for(stop_initial: float, slippage_pct: float) -> float:
    """Exit on a stop-market order executes below the threshold: stop × (1 − slippage)."""
    return stop_initial * (1 - slippage_pct)


def size_position(inp: SizingInput, max_iterations: int = 3) -> SizingResult:
    price_max = price_max_for(inp.entry_level, inp.order_kind, inp.slippage_pct)
    stop_exec = stop_exec_for(inp.stop_initial, inp.slippage_pct)
    budget = inp.capital_eur * inp.max_risk_pct * inp.size_multiplier
    r_ccy = price_max - stop_exec
    res = SizingResult(0, price_max, stop_exec, 0.0, budget, 0.0, 0.0, 0.0)
    if r_ccy <= 0:
        res.blocked.append("BLOQUÉ : stop au-dessus du prix maximal d'entrée")
        return res
    r_eur = r_ccy * inp.fx_rate * (1 + inp.fx_haircut)
    res.r_per_share_eur = r_eur
    if not inp.fees.known:
        res.warnings.append(f"frais {inp.fees.broker} non renseignés (brokers.*) : dimensionnement sans courtage")
    # fixed-point iteration fees → shares → fees
    shares = math.floor(budget / r_eur)
    fees = 0.0
    for i in range(1, max_iterations + 2):
        res.iterations = i
        notional = shares * price_max * inp.fx_rate
        fees = estimate_round_trip_fees(notional, inp.fees, inp.ttf_rate)
        new_shares = math.floor((budget - fees) / r_eur) if budget > fees else 0
        if new_shares == shares:
            break
        shares = new_shares
        if i > max_iterations:
            break
    # caps on the notional (docs/07 §7.2)
    cap_capital = inp.capital_eur * inp.max_position_pct_of_capital
    if shares * price_max * inp.fx_rate > cap_capital:
        shares = math.floor(cap_capital / (price_max * inp.fx_rate))
        res.warnings.append(f"taille réduite au plafond {inp.max_position_pct_of_capital:.0%} du capital")
    if inp.adv20_eur is not None:
        cap_adv = inp.adv20_eur * inp.max_position_pct_of_adv
        if shares * price_max * inp.fx_rate > cap_adv:
            shares = math.floor(cap_adv / (price_max * inp.fx_rate))
            res.warnings.append(f"taille réduite au plafond {inp.max_position_pct_of_adv:.0%} de l'ADV20")
    if shares <= 0:
        res.blocked.append("BLOQUÉ : budget de risque insuffisant pour une action après frais")
    res.shares = max(shares, 0)
    res.notional_max_eur = res.shares * price_max * inp.fx_rate
    res.fees_estimated_eur = estimate_round_trip_fees(res.notional_max_eur, inp.fees, inp.ttf_rate)
    res.risk_if_filled_at_max_eur = res.shares * r_eur
    return res


def risk_if_filled(
    shares: int, exec_price: float, stop_exec: float, fx_rate: float = 1.0, fx_haircut: float = 0.0
) -> float:
    """Recalculated on the real fill (docs/07 §7.2): qty × (prix_exécuté − stop glissé)."""
    return shares * (exec_price - stop_exec) * fx_rate * (1 + fx_haircut)


def post_fill_adjustment_needed(real_risk_eur: float, planned_risk_eur: float, tolerance: float) -> bool:
    return planned_risk_eur > 0 and real_risk_eur > planned_risk_eur * tolerance


# --- position risk definitions (docs/07 §7.3) ----------------------------------------------------


@dataclass(frozen=True)
class OpenPosition:
    isin: str
    account: str  # pea | cto_cash | cto_srd
    qty: int
    qty_protected: int
    entry_price: float  # prix exécuté moyen (devise)
    current_price: float  # dernière cotation valide ou clôture
    stop_current: float | None  # niveau du stop courant (devise)
    stop_initial: float | None
    fx_rate: float = 1.0
    currency: str = "EUR"
    sector: str = "inconnu"
    mic: str = "XPAR"
    mode: str = "comptant"
    exit_fees_eur: float = 0.0
    entry_fees_eur: float = 0.0
    slippage_pct: float = 0.002

    @property
    def stop_exec(self) -> float | None:
        return None if self.stop_current is None else self.stop_current * (1 - self.slippage_pct)

    @property
    def market_value_eur(self) -> float:
        return self.qty * self.current_price * self.fx_rate


def initial_risk_eur(p: OpenPosition) -> float | None:
    """Fixed at opening; used for realised R, never for caps."""
    if p.stop_initial is None:
        return None
    return (
        p.qty * (p.entry_price - p.stop_initial * (1 - p.slippage_pct)) * p.fx_rate + p.entry_fees_eur + p.exit_fees_eur
    )


def current_risk_eur(p: OpenPosition) -> float:
    """max(0, qty_protégée × (cours − stop glissé)) + frais de sortie ; quantité non couverte =
    perte totale possible (qty × cours). Never negative per position."""
    uncovered = max(0, p.qty - p.qty_protected)
    covered = p.qty - uncovered
    risk = 0.0
    if p.stop_exec is not None and covered > 0:
        risk += max(0.0, covered * (p.current_price - p.stop_exec) * p.fx_rate)
    risk += uncovered * p.current_price * p.fx_rate
    return risk + p.exit_fees_eur


@dataclass(frozen=True)
class PendingEntry:
    isin: str
    account: str
    qty_unfilled: int
    price_max: float
    stop_exec: float
    fees_eur: float
    fx_rate: float = 1.0
    sector: str = "inconnu"


def reserved_risk_eur(o: PendingEntry) -> float:
    return o.qty_unfilled * (o.price_max - o.stop_exec) * o.fx_rate + o.fees_eur


def gap_scenario_loss_eur(positions: list[OpenPosition], gap_pct: float) -> float:
    """Loss if every position opens ``gap_pct`` (e.g. −10 %) *below its stop* (docs/07 §7.3)."""
    loss = 0.0
    for p in positions:
        ref = p.stop_current if p.stop_current is not None else p.current_price
        open_price = ref * (1 + gap_pct)
        loss += max(0.0, p.qty * (p.current_price - open_price) * p.fx_rate)
    return loss


# --- portfolio caps ------------------------------------------------------------------------------


@dataclass(frozen=True)
class RiskParams:
    capital_eur: float
    max_open_risk_pct: float
    max_sector_risk_pct: float
    max_factor_risk_pct: float
    factor_groups: dict[str, list[str]]
    gap_scenario_pct: float
    positions_max: int
    exposure_caps: dict[str, float]  # green / orange / red
    max_positions_per_sector: int
    max_positions_per_sector_green_high_score: int
    sector_third_position_min_ratio: float
    max_fx_exposure_pct: float
    max_exposure_per_non_paris_market_pct: float
    srd_max_leverage: float
    srd_cash_reserve_pct: float
    srd_coverage_buffer_alert: float
    srd_coverage_buffer_block: float
    diversification_hints: dict[str, float]


@dataclass(frozen=True)
class Candidate:
    isin: str
    account: str
    sector: str
    mic: str
    currency: str
    initial_risk_eur: float
    notional_eur: float
    admission_ratio: float | None = None


@dataclass
class PortfolioRisk:
    open_risk_current_eur: float
    open_risk_reserved_eur: float
    open_risk_total_eur: float
    open_risk_pct: float
    gap_scenario_loss_eur: float
    exposure_eur: float
    exposure_pct: float
    fx_exposure_pct: float
    by_sector_risk_eur: dict[str, float]
    by_sector_count: dict[str, int]
    by_factor_risk_eur: dict[str, float]
    by_mic_exposure_pct: dict[str, float]
    positions_count: int
    srd_notional_eur: float
    srd_leverage: float | None
    srd_coverage_ratio: float | None
    alerts: list[str] = field(default_factory=list)


def portfolio_risk(
    positions: list[OpenPosition],
    pending: list[PendingEntry],
    params: RiskParams,
    cto_equity_eur: float | None = None,
    srd_coverage_ratio: float | None = None,
) -> PortfolioRisk:
    cur = {p.isin: current_risk_eur(p) for p in positions}
    res = {o.isin: reserved_risk_eur(o) for o in pending}
    total_cur = sum(cur.values())
    total_res = sum(res.values())
    exposure = sum(p.market_value_eur for p in positions)
    fx_exp = sum(p.market_value_eur for p in positions if p.currency != "EUR")
    by_sector: dict[str, float] = {}
    by_sector_n: dict[str, int] = {}
    for p in positions:
        by_sector[p.sector] = by_sector.get(p.sector, 0.0) + cur[p.isin]
        by_sector_n[p.sector] = by_sector_n.get(p.sector, 0) + 1
    for o in pending:
        by_sector[o.sector] = by_sector.get(o.sector, 0.0) + res[o.isin]
    by_factor: dict[str, float] = {}
    for name, isins in params.factor_groups.items():
        by_factor[name] = sum(cur.get(i, 0.0) + res.get(i, 0.0) for i in isins)
    by_mic: dict[str, float] = {}
    for p in positions:
        by_mic[p.mic] = by_mic.get(p.mic, 0.0) + p.market_value_eur
    srd_notional = sum(p.market_value_eur for p in positions if p.mode == "srd")
    leverage = None if not cto_equity_eur else srd_notional / cto_equity_eur
    gap = gap_scenario_loss_eur(positions, params.gap_scenario_pct)
    pr = PortfolioRisk(
        total_cur,
        total_res,
        total_cur + total_res,
        (total_cur + total_res) / params.capital_eur,
        gap,
        exposure,
        exposure / params.capital_eur,
        (fx_exp / exposure) if exposure else 0.0,
        by_sector,
        by_sector_n,
        by_factor,
        {m: v / params.capital_eur for m, v in by_mic.items()},
        len(positions),
        srd_notional,
        leverage,
        srd_coverage_ratio,
    )
    if gap > 2 * (total_cur + total_res) and gap > 0:
        pr.alerts.append(f"scénario de gap {params.gap_scenario_pct:+.0%} : perte {gap:.0f} € > 2 × risque ouvert")
    if srd_coverage_ratio is not None and srd_coverage_ratio < params.srd_coverage_buffer_alert:
        pr.alerts.append(f"couverture SRD {srd_coverage_ratio:.2f} × < {params.srd_coverage_buffer_alert} ×")
    return pr


def check_candidate(pr: PortfolioRisk, cand: Candidate, params: RiskParams, regime: str) -> list[str]:
    """Gates of docs/07 §7.3 for a new entry. Returns the list of ``BLOQUÉ : …`` reasons."""
    blocked: list[str] = []
    cap = params.capital_eur
    total_after = pr.open_risk_total_eur + cand.initial_risk_eur
    if total_after > params.max_open_risk_pct * cap:
        blocked.append(
            f"BLOQUÉ : risque courant + réservé après trade {total_after:.0f} € ({total_after / cap:.1%}) "
            f"> plafond {params.max_open_risk_pct:.0%} (risk.max_open_risk_pct)"
        )
    sector_after = pr.by_sector_risk_eur.get(cand.sector, 0.0) + cand.initial_risk_eur
    if sector_after > params.max_sector_risk_pct * cap:
        blocked.append(
            f"BLOQUÉ : risque secteur « {cand.sector} » {sector_after:.0f} € > {params.max_sector_risk_pct:.0%} (risk.max_sector_risk_pct)"  # noqa: E501
        )
    n_sector = pr.by_sector_count.get(cand.sector, 0)
    limit = params.max_positions_per_sector
    if (
        regime == "green"
        and cand.admission_ratio is not None
        and cand.admission_ratio >= params.sector_third_position_min_ratio
    ):
        limit = params.max_positions_per_sector_green_high_score
    if n_sector + 1 > limit:
        blocked.append(
            f"BLOQUÉ : {n_sector + 1} positions dans le secteur « {cand.sector} » > {limit} (risk.max_positions_per_sector)"  # noqa: E501
        )
    for name, isins in params.factor_groups.items():
        if cand.isin in isins:
            after = pr.by_factor_risk_eur.get(name, 0.0) + cand.initial_risk_eur
            if after > params.max_factor_risk_pct * cap:
                blocked.append(
                    f"BLOQUÉ : risque facteur « {name} » {after:.0f} € > {params.max_factor_risk_pct:.0%} (risk.max_factor_risk_pct)"  # noqa: E501
                )
    exposure_cap = params.exposure_caps.get(regime, 0.0)
    if regime == "red":
        blocked.append("BLOQUÉ : régime rouge, aucune nouvelle entrée (risk.exposure.red)")
    elif pr.exposure_eur + cand.notional_eur > exposure_cap * cap:
        blocked.append(
            f"BLOQUÉ : exposition après trade {(pr.exposure_eur + cand.notional_eur) / cap:.0%} > {exposure_cap:.0%} en régime {regime} (risk.exposure)"  # noqa: E501
        )
    if pr.positions_count + 1 > params.positions_max:
        blocked.append(f"BLOQUÉ : {pr.positions_count + 1} positions > {params.positions_max} (risk.positions_max)")
    if cand.currency != "EUR":
        fx_after = (pr.fx_exposure_pct * pr.exposure_eur + cand.notional_eur) / (pr.exposure_eur + cand.notional_eur)
        if fx_after > params.max_fx_exposure_pct:
            blocked.append(
                f"BLOQUÉ : exposition hors EUR {fx_after:.0%} > {params.max_fx_exposure_pct:.0%} (risk.max_fx_exposure_pct)"  # noqa: E501
            )
    if cand.mic != "XPAR":
        mic_after = pr.by_mic_exposure_pct.get(cand.mic, 0.0) + cand.notional_eur / cap
        if mic_after > params.max_exposure_per_non_paris_market_pct:
            blocked.append(
                f"BLOQUÉ : exposition {cand.mic} {mic_after:.0%} > {params.max_exposure_per_non_paris_market_pct:.0%} (risk.max_exposure_per_non_paris_market_pct)"  # noqa: E501
            )
    if cand.account == "cto_srd":
        if pr.srd_coverage_ratio is not None and pr.srd_coverage_ratio < params.srd_coverage_buffer_block:
            blocked.append(
                f"BLOQUÉ : couverture SRD {pr.srd_coverage_ratio:.2f} × < {params.srd_coverage_buffer_block} × (risk.srd_coverage_buffer_block)"  # noqa: E501
            )
        if pr.srd_leverage is not None and pr.srd_notional_eur > 0:
            lev_after = (pr.srd_notional_eur + cand.notional_eur) / (pr.srd_notional_eur / pr.srd_leverage)
            if lev_after > params.srd_max_leverage:
                blocked.append(
                    f"BLOQUÉ : levier SRD après trade {lev_after:.2f} > {params.srd_max_leverage} (risk.srd_max_leverage)"  # noqa: E501
                )
    return blocked


# --- circuit breaker and drawdown ----------------------------------------------------------------


@dataclass(frozen=True)
class CircuitBreakerParams:
    weekly_loss_r: float
    monthly_loss_r: float
    resume_size_multiplier: float


@dataclass(frozen=True)
class CircuitBreakerState:
    tripped: bool
    reason: str
    size_multiplier: float


def circuit_breaker(
    week_realized_r: float,
    week_latent_delta_r: float,
    month_realized_r: float,
    month_latent_delta_r: float,
    params: CircuitBreakerParams,
    previous_week_tripped: bool = False,
) -> CircuitBreakerState:
    """Period losses in R = realised R + change in latent R since period start (docs/07 §7.3 b)."""
    week = week_realized_r + week_latent_delta_r
    month = month_realized_r + month_latent_delta_r
    if month <= -params.monthly_loss_r:
        return CircuitBreakerState(
            True,
            f"coupe-circuit : {month:.1f} R sur le mois ≤ −{params.monthly_loss_r} R → pas de nouvelle entrée jusqu'au mois suivant",  # noqa: E501
            0.0,
        )
    if week <= -params.weekly_loss_r:
        return CircuitBreakerState(
            True,
            f"coupe-circuit : {week:.1f} R sur la semaine ≤ −{params.weekly_loss_r} R → pas de nouvelle entrée jusqu'à lundi",  # noqa: E501
            0.0,
        )
    if previous_week_tripped:
        return CircuitBreakerState(
            False, f"semaine de reprise : tailles × {params.resume_size_multiplier}", params.resume_size_multiplier
        )
    return CircuitBreakerState(False, "", 1.0)


def twr_drawdown(equity: list[float], flows: list[float] | None = None) -> tuple[list[float], float, float]:
    """Time-weighted equity index (deposits/withdrawals neutralised) → (index, current dd, max dd).
    ``flows[i]`` is the net flow received *during* period i (before equity[i] is measured)."""
    if not equity:
        return [], 0.0, 0.0
    flows = flows or [0.0] * len(equity)
    idx = [1.0]
    for i in range(1, len(equity)):
        prev = equity[i - 1]
        r = (equity[i] - flows[i]) / prev if prev else 1.0
        idx.append(idx[-1] * r)
    peak = idx[0]
    max_dd = 0.0
    for v in idx:
        peak = max(peak, v)
        max_dd = min(max_dd, v / peak - 1)
    return idx, idx[-1] / max(idx) - 1, max_dd
