"""Build the portfolio risk view from the database and params (docs/07 §7.8, page ``/``)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import LoadedConfig
from app.db.models import Account, Order, PortfolioState, PositionRow
from app.domain.monitor import is_state_fresh
from app.domain.portfolio import PositionValue, account_equity, srd_coverage
from app.domain.risk import FeeSchedule, OpenPosition, PendingEntry, PortfolioRisk, RiskParams, portfolio_risk
from app.domain.timeutil import from_store
from app.services.instruments import _load as instruments_map
from app.services.market_data import fx_to_eur  # noqa: F401


def risk_params_from(config: LoadedConfig) -> RiskParams:
    r = config.params.risk
    return RiskParams(
        capital_eur=config.params.capital.capital_pilote_eur,
        max_open_risk_pct=r.max_open_risk_pct,
        max_sector_risk_pct=r.max_sector_risk_pct,
        max_factor_risk_pct=r.max_factor_risk_pct,
        factor_groups=r.factor_groups,
        gap_scenario_pct=r.gap_scenario_pct,
        positions_max=r.positions_max,
        exposure_caps=r.exposure,
        max_positions_per_sector=r.max_positions_per_sector,
        max_positions_per_sector_green_high_score=r.max_positions_per_sector_green_high_score,
        sector_third_position_min_ratio=r.sector_third_position_min_ratio,
        max_fx_exposure_pct=r.max_fx_exposure_pct,
        max_exposure_per_non_paris_market_pct=r.max_exposure_per_non_paris_market_pct,
        srd_max_leverage=r.srd_max_leverage,
        srd_cash_reserve_pct=r.srd_cash_reserve_pct,
        srd_coverage_buffer_alert=r.srd_coverage_buffer_alert,
        srd_coverage_buffer_block=r.srd_coverage_buffer_block,
        diversification_hints=r.diversification_hints,
    )


def fee_schedule_for(config: LoadedConfig, broker: str) -> FeeSchedule:
    b = (config.params.brokers or {}).get(broker, {})
    pct = b.get("commission_pct")
    return FeeSchedule(
        broker,
        float(pct) if isinstance(pct, int | float) else None,
        float(b.get("commission_min_eur", 0) or 0),
        b.get("commission_cap_pct"),
    )


def _stub(r: PositionRow) -> OpenPosition:
    return OpenPosition(
        r.isin, "", r.qty_held, r.qty_protected, r.avg_price, r.avg_price, r.stop_current, r.stop_initial
    )


def build_risk_view(s: Session, config: LoadedConfig, now: datetime | None = None) -> dict[str, object]:
    now = now or datetime.now(UTC)
    accounts = {a.id: a for a in s.scalars(select(Account))}
    states = {st.account_id: st for st in s.scalars(select(PortfolioState))}
    imap = instruments_map()
    positions: list[OpenPosition] = []
    values_by_account: dict[str, list[PositionValue]] = {"pea": [], "cto_cash": [], "cto_srd": []}
    missing_price: list[str] = []  # M12: no price → excluded from the aggregates, view marked incomplete
    missing_fx: list[str] = []  # C4: FX unknown → excluded, never 1.0
    price_status: dict[str, tuple[str, datetime | None]] = {}
    for r in s.scalars(select(PositionRow).where(PositionRow.closed_at.is_(None), PositionRow.qty_held > 0)):
        acc = accounts[r.account_id]
        meta = imap.get(r.isin, {})
        if r.last_price is None:
            missing_price.append(r.isin)
            continue
        fx = fx_to_eur(s, r.currency or "EUR")
        if fx is None:
            missing_fx.append(f"{r.isin} ({r.currency})")
            continue
        price = r.last_price
        price_status[r.isin] = (
            r.last_price_status or "indisponible",
            from_store(r.last_price_at) if r.last_price_at else None,
        )
        fees = fee_schedule_for(config, acc.broker)
        exit_fees = fees.commission(r.qty_held * price * fx)
        positions.append(
            OpenPosition(
                r.isin,
                acc.type,
                r.qty_held,
                r.qty_protected,
                r.avg_price,
                price,
                r.stop_current,
                r.stop_initial,
                fx,
                r.currency or "EUR",
                str(meta.get("sector", "inconnu")),
                str(meta.get("mic", "XPAR")),
                r.mode,
                exit_fees,
                0.0,
                config.params.risk.slippage_pct,
            )
        )
        values_by_account.setdefault(acc.type, []).append(
            PositionValue(r.isin, r.qty_held, r.avg_price, price, fx, r.mode, 0.0, r.currency or "EUR")
        )
    pending: list[PendingEntry] = []
    reserved_unknown: list[str] = []  # M13: no known stop → reserved at the full per-trade budget, flagged
    budget = config.params.capital.capital_pilote_eur * config.params.risk.max_risk_per_trade_pct
    for o in s.scalars(select(Order).where(Order.side == "buy", Order.state.in_(("entered", "partially_filled")))):
        from app.db.models import Proposal

        price_max = o.limit_price or o.trigger_price or 0.0
        acc = accounts[o.account_id]
        qty = o.qty - o.qty_filled
        fees_eur = fee_schedule_for(config, acc.broker).commission(qty * price_max)
        sector = str(imap.get(o.isin, {}).get("sector", "inconnu"))
        prop = s.get(Proposal, o.proposal_id) if o.proposal_id else None
        stop = prop.stop if prop and prop.stop else None
        if stop is None or price_max <= 0:
            reserved_unknown.append(o.isin)
            pending.append(PendingEntry(o.isin, acc.type, 1, budget, 0.0, fees_eur, sector=sector))
            continue
        pending.append(
            PendingEntry(
                o.isin, acc.type, qty, price_max, stop * (1 - config.params.risk.slippage_pct), fees_eur, sector=sector
            )
        )
    cto_cash = sum((a.cash or 0.0) for a in accounts.values() if a.broker == "saxo") / max(
        1, sum(1 for a in accounts.values() if a.broker == "saxo")
    )
    cto_eq = account_equity(cto_cash, values_by_account.get("cto_cash", []) + values_by_account.get("cto_srd", []))
    srd_params = config.params.srd
    coverage = None
    if srd_params and cto_eq.srd_notional > 0:
        raw = config.raw.get("srd", {})
        _, _, coverage = srd_coverage(
            cto_cash,
            cto_eq.cash_positions_value,
            cto_eq.srd_notional,
            dict(raw.get("coverage_rates", {})),
            srd_params.coverage_rate_broker,
        )
    pr: PortfolioRisk = portfolio_risk(
        positions,
        pending,
        risk_params_from(config),
        cto_equity_eur=cto_eq.equity if cto_eq.equity > 0 else None,
        srd_coverage_ratio=coverage,
    )
    pea_acc = next((a for a in accounts.values() if a.type == "pea"), None)
    pea_eq = account_equity(pea_acc.cash or 0.0 if pea_acc else 0.0, values_by_account.get("pea", []))
    max_age = config.params.accounts.portfolio_state_max_age_minutes
    freshness = {}
    for acc in accounts.values():
        st = states.get(acc.id)
        synced = from_store(st.synced_at) if st and st.synced_at else None
        freshness[acc.type] = {
            "synced_at": synced,
            "fresh": is_state_fresh(
                synced, now, max_age if acc.broker == "saxo" else config.params.accounts.pea.state_max_age_hours * 60
            ),
            "declarative": st.declarative if st else True,
        }
    return {
        "risk": pr,
        "capital": config.params.capital.capital_pilote_eur,
        "cto": cto_eq,
        "pea": pea_eq,
        "coverage_ratio": coverage,
        "unprotected": [p for p in positions if p.qty > p.qty_protected]
        + [
            _stub(r)
            for r in s.scalars(
                select(PositionRow).where(
                    PositionRow.closed_at.is_(None), PositionRow.qty_held > PositionRow.qty_protected
                )
            )
            if r.isin in missing_price
        ],
        "missing_price": missing_price,
        "missing_fx": missing_fx,  # noqa: F821
        "reserved_unknown": reserved_unknown,  # noqa: F821
        "incomplete": bool(missing_price or missing_fx or reserved_unknown),  # noqa: F821
        "price_status": price_status,  # noqa: F821
        "freshness": freshness,
        "hints": config.params.risk.diversification_hints,
        "max_open_risk_pct": config.params.risk.max_open_risk_pct,
    }
