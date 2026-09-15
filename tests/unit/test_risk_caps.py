"""A1.3 / A1.4: cumulative, reserved, sector, factor, gap scenario, exposure, leverage, coverage on
a synthetic portfolio (SRD + cash + PEA, SEK); current risk floored at zero; BLOQUÉ with the rule."""

import pytest

from app.domain.risk import (
    Candidate,
    CircuitBreakerParams,
    OpenPosition,
    PendingEntry,
    RiskParams,
    check_candidate,
    circuit_breaker,
    current_risk_eur,
    gap_scenario_loss_eur,
    initial_risk_eur,
    portfolio_risk,
    reserved_risk_eur,
    twr_drawdown,
)


def _params(config, **kw) -> RiskParams:
    r = config.params.risk
    base = dict(
        capital_eur=config.params.capital.capital_pilote_eur,
        max_open_risk_pct=r.max_open_risk_pct,
        max_sector_risk_pct=r.max_sector_risk_pct,
        max_factor_risk_pct=r.max_factor_risk_pct,
        factor_groups={"defense": ["FR0000073272", "FR0000121329"]},
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
    base.update(kw)
    return RiskParams(**base)


SRD = OpenPosition(
    "FR0000120271", "cto_srd", 100, 100, 42.40, 43.00, 40.60, 40.60, sector="energie", mode="srd", exit_fees_eur=3.5
)
CASH = OpenPosition(
    "FR0000120073", "cto_cash", 12, 12, 171.45, 176.90, 165.00, 165.00, sector="chimie", exit_fees_eur=2.0
)
PEA_SEK = OpenPosition(
    "SE0000108656",
    "pea",
    50,
    30,
    200.0,
    230.0,
    190.0,
    190.0,
    fx_rate=0.09,
    currency="SEK",
    sector="industrie",
    mic="XSTO",
    exit_fees_eur=1.0,
)
WINNER = OpenPosition(
    "NL0010273215", "cto_cash", 3, 3, 600.0, 700.0, 650.0, 590.0, sector="tech", mic="XAMS", exit_fees_eur=2.0
)


def test_position_risk_definitions():
    # SRD : 100 × (43 − 40.519) + 3.5
    assert round(current_risk_eur(SRD), 2) == round(100 * (43 - 40.60 * 0.998) + 3.5, 2)
    assert round(initial_risk_eur(SRD), 2) == round(100 * (42.40 - 40.60 * 0.998) + 3.5, 2)
    # winner: formula of docs/07 §7.3 = qty × (cours − stop glissé) + frais, never negative
    assert round(current_risk_eur(WINNER), 2) == round(3 * (700 - 650 * 0.998) + 2.0, 2)
    locked = OpenPosition(
        "X", "pea", 3, 3, 600.0, 700.0, 705.0, 590.0, exit_fees_eur=2.0
    )  # stop_exec ≥ cours → 0 + frais
    assert current_risk_eur(locked) == 2.0
    # SEK partly unprotected: 30 covered + 20 uncovered = full loss on 20
    cov = 30 * (230 - 190 * 0.998) * 0.09
    unc = 20 * 230 * 0.09
    assert round(current_risk_eur(PEA_SEK), 4) == round(cov + unc + 1.0, 4)
    assert reserved_risk_eur(PendingEntry("X", "pea", 40, 42.9, 40.519, 5.0)) == pytest.approx(
        40 * (42.9 - 40.519) + 5.0
    )  # noqa: E501


def test_gap_scenario():
    loss = gap_scenario_loss_eur([SRD, CASH], -0.10)
    assert round(loss, 2) == round(100 * (43 - 40.60 * 0.9) + 12 * (176.90 - 165 * 0.9), 2)


def test_portfolio_risk_aggregates_and_alerts(config):
    params = _params(config)
    pending = [PendingEntry("FR0000121329", "cto_cash", 50, 100.0, 95.0, 4.0, sector="defense_sector")]
    pr = portfolio_risk([SRD, CASH, PEA_SEK, WINNER], pending, params, cto_equity_eur=10_000, srd_coverage_ratio=1.4)
    assert pr.positions_count == 4 and pr.srd_notional_eur == 4300 and round(pr.srd_leverage, 2) == 0.43
    assert pr.open_risk_reserved_eur == reserved_risk_eur(pending[0])
    assert round(pr.open_risk_total_eur, 2) == round(
        sum(current_risk_eur(p) for p in [SRD, CASH, PEA_SEK, WINNER]) + pr.open_risk_reserved_eur, 2
    )
    assert pr.by_sector_count == {"energie": 1, "chimie": 1, "industrie": 1, "tech": 1}
    assert "defense_sector" in pr.by_sector_risk_eur and pr.by_factor_risk_eur == {
        "defense": 254.0
    }  # pending order counts
    assert round(pr.fx_exposure_pct, 3) == round(1035 / pr.exposure_eur, 3)
    assert any("couverture SRD" in a for a in pr.alerts)
    assert any("scénario de gap" in a for a in pr.alerts) or pr.gap_scenario_loss_eur <= 2 * pr.open_risk_total_eur


def test_candidate_blocked_with_rule_names(config):
    params = _params(config)
    pr = portfolio_risk([SRD, CASH], [], params, cto_equity_eur=10_000, srd_coverage_ratio=1.2)
    # cumulative cap: 4 % of 40 000 = 1 600 €
    big = Candidate("FR0000000001", "cto_cash", "banque", "XPAR", "EUR", initial_risk_eur=1500, notional_eur=5000)
    blocked = check_candidate(pr, big, params, "green")
    assert any("risk.max_open_risk_pct" in b for b in blocked) and all(b.startswith("BLOQUÉ") for b in blocked)
    same_sector = Candidate("FR0000000002", "cto_cash", "energie", "XPAR", "EUR", 700, 3000)
    assert any("risk.max_sector_risk_pct" in b for b in check_candidate(pr, same_sector, params, "green"))
    srd_block = Candidate("FR0000000003", "cto_srd", "auto", "XPAR", "EUR", 100, 3000)
    assert any("srd_coverage_buffer_block" in b for b in check_candidate(pr, srd_block, params, "green"))
    assert any(
        "régime rouge" in b
        for b in check_candidate(pr, Candidate("X", "pea", "s", "XPAR", "EUR", 10, 100), params, "red")
    )
    ok = Candidate("FR0000000004", "pea", "sante", "XPAR", "EUR", 250, 4000)
    assert check_candidate(pr, ok, params, "green") == []


def test_sector_third_position_only_in_green_with_high_ratio(config):
    params = _params(config)
    two = [OpenPosition(f"FR{i}", "pea", 10, 10, 10, 11, 9, 9, sector="luxe") for i in range(2)]
    pr = portfolio_risk(two, [], params)
    c = Candidate("FR9", "pea", "luxe", "XPAR", "EUR", 50, 500, admission_ratio=0.85)
    assert check_candidate(pr, c, params, "green") == []
    assert any("max_positions_per_sector" in b for b in check_candidate(pr, c, params, "orange"))
    low = Candidate("FR9", "pea", "luxe", "XPAR", "EUR", 50, 500, admission_ratio=0.76)
    assert any("max_positions_per_sector" in b for b in check_candidate(pr, low, params, "green"))


def test_fx_and_venue_caps(config):
    params = _params(config)
    pr = portfolio_risk([PEA_SEK], [], params)
    big_sek = Candidate("SE2", "pea", "x", "XSTO", "SEK", 100, 20_000)
    blocked = check_candidate(pr, big_sek, params, "green")
    assert any("max_fx_exposure_pct" in b for b in blocked) and any("XSTO" in b for b in blocked)


def test_circuit_breaker(config):
    cb = config.params.risk.circuit_breaker
    p = CircuitBreakerParams(cb.weekly_loss_r, cb.monthly_loss_r, cb.resume_size_multiplier)
    assert not circuit_breaker(-1.0, -0.5, -2.0, -0.5, p).tripped
    assert circuit_breaker(-2.0, -1.2, -3.0, -0.2, p).tripped  # week −3.2 R
    assert "mois" in circuit_breaker(-1.0, 0.0, -5.0, -1.5, p).reason
    assert circuit_breaker(0.0, 0.0, 0.0, 0.0, p, previous_week_tripped=True).size_multiplier == 0.5


def test_twr_drawdown_neutralises_deposits():
    idx, dd, max_dd = twr_drawdown([100, 90, 190, 170], flows=[0, 0, 100, 0])
    assert [round(v, 3) for v in idx] == [1.0, 0.9, 0.9, 0.805]
    assert round(max_dd, 3) == -0.195 and round(dd, 3) == -0.195
    assert twr_drawdown([]) == ([], 0.0, 0.0)
