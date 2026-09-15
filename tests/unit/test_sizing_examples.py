"""A1.4 reference example (docs/09 §9.1): limit order at 42.90, stop 40.60 à seuil, Saxo fees,
TTF due → 114 shares, R 2.381 €, risk if filled at 42.40 = 214 €, fees ≈ 27 €."""

import pytest

from app.domain.risk import (
    EntryOrderKind,
    FeeSchedule,
    SizingInput,
    post_fill_adjustment_needed,
    risk_if_filled,
    size_position,
)

SAXO = FeeSchedule("saxo", 0.0008, 2.0)


def _inp(config, **kw):
    r = config.params.risk
    base = dict(
        capital_eur=config.params.capital.capital_pilote_eur,
        max_risk_pct=r.max_risk_per_trade_pct,
        entry_level=42.90,
        stop_initial=40.60,
        order_kind=EntryOrderKind.LIMIT,
        slippage_pct=r.slippage_pct,
        fees=SAXO,
        ttf_rate=config.params.tax[0].ttf_rate if config.params.tax else 0.004,
        max_position_pct_of_capital=r.max_position_pct_of_capital,
        max_position_pct_of_adv=r.max_position_pct_of_adv,
    )
    base.update(kw)
    return SizingInput(**base)


def test_reference_example_114_shares(config):
    res = size_position(_inp(config))
    assert res.ok
    assert res.price_max == 42.90
    assert round(res.stop_exec, 3) == 40.519
    assert round(res.r_per_share_eur, 3) == 2.381
    assert res.risk_budget_eur == 300
    assert res.shares == 114
    assert round(res.notional_max_eur) == 4891
    assert 27 <= res.fees_estimated_eur <= 28  # 2 × 3.91 + 19.56
    assert res.iterations <= 4
    real = risk_if_filled(114, 42.40, res.stop_exec)
    assert round(real) == 214
    assert not post_fill_adjustment_needed(real, res.risk_if_filled_at_max_eur, 1.10)
    assert post_fill_adjustment_needed(real * 1.2, real, 1.10)


def test_fx_haircut_reduces_quantity_not_budget(config):
    eur = size_position(_inp(config))
    sek = size_position(_inp(config, fx_rate=1.0, fx_haircut=config.params.risk.fx_haircut))
    assert sek.risk_budget_eur == eur.risk_budget_eur == 300
    assert sek.shares < eur.shares and round(sek.r_per_share_eur, 4) == round(eur.r_per_share_eur * 1.10, 4)


def test_market_order_adds_entry_slippage(config):
    lim = size_position(_inp(config))
    mkt = size_position(_inp(config, order_kind=EntryOrderKind.MARKET))
    assert mkt.price_max > lim.price_max and mkt.shares <= lim.shares
    assert size_position(_inp(config, order_kind=EntryOrderKind.STOP_LIMIT)).price_max == 42.90


def test_multipliers_and_caps(config):
    half = size_position(_inp(config, size_multiplier=0.5))
    assert half.risk_budget_eur == 150 and half.shares == 57  # (150 − 13.8 € de frais) / 2.381
    adv = size_position(_inp(config, adv20_eur=50_000))
    assert adv.shares == 58 and any("ADV20" in w for w in adv.warnings)
    cap = size_position(_inp(config, stop_initial=42.80))  # tiny R → notional would exceed 20 % capital
    assert cap.notional_max_eur <= 8000 + 42.9 and any("capital" in w for w in cap.warnings)


def test_blocked_when_stop_above_price_and_unknown_fees(config):
    bad = size_position(_inp(config, stop_initial=43.0))
    assert bad.shares == 0 and bad.blocked and bad.blocked[0].startswith("BLOQUÉ")
    unknown = size_position(_inp(config, fees=FeeSchedule("boursobank", None, 0.0)))
    assert unknown.ok and any("non renseignés" in w for w in unknown.warnings)
    assert unknown.shares == 117  # only TTF counted


@pytest.mark.parametrize(
    "pct,min_eur,cap,notional,expected",
    [(0.0008, 2.0, None, 1000, 2.0), (0.0008, 2.0, None, 4891, 3.9128), (0.005, 1.0, 0.005, 200, 1.0)],
)
def test_fee_schedule(pct, min_eur, cap, notional, expected):
    assert round(FeeSchedule("x", pct, min_eur, cap).commission(notional), 4) == expected
