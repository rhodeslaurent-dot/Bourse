"""docs/08 §8.3 example: notional 4 891 €, 15 days, TTF due → ≈ 44 €, ≈ 8 % of gain at target 1."""

from app.domain.accounts import SrdCostParams, srd_trade_cost, ttf_applicable
from app.domain.risk import FeeSchedule


def test_srd_cost_example(config):
    s = config.params.srd
    p = SrdCostParams(
        s.crd_daily_rate, s.prorogation_rate, s.prorogation_min_eur, config.params.risk.srd_max_cost_pct_of_target1
    )
    fees = FeeSchedule(
        "saxo", config.params.brokers["saxo"]["commission_pct"], config.params.brokers["saxo"]["commission_min_eur"]
    )
    c = srd_trade_cost(4891, 15, 0, fees, p, 0.004, gain_at_target1_eur=543)
    assert round(c.commission_eur, 1) == 7.8 and round(c.crd_eur, 1) == 16.9 and round(c.ttf_eur, 1) == 19.6
    assert 43.5 <= c.total_eur <= 44.5 and round(c.pct_of_gain_t1, 2) == 0.08 and c.blocked is None
    blocked = srd_trade_cost(4891, 15, 2, fees, p, 0.004, gain_at_target1_eur=100)
    assert blocked.prorogations_eur == 2 * max(0.002 * 4891, 10) and blocked.blocked.startswith("BLOQUÉ")


def test_ttf_applicable():
    assert ttf_applicable("FR", 2e9, 1e9) and not ttf_applicable("FR", 5e8, 1e9) and not ttf_applicable("NL", 5e10, 1e9)
