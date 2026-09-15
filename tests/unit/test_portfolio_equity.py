"""docs/10 §10.3: equity = cash + cash positions + SRD unrealized P&L − CRD; SRD notional apart."""

from app.domain.portfolio import PositionValue, account_equity, srd_coverage, srd_leverage


def test_equity_with_srd_and_sek():
    positions = [
        PositionValue("FR1", 100, 42.4, 45.0, mode="comptant"),  # +260
        PositionValue("FR2", 200, 30.0, 31.0, mode="srd", crd_accrued=12.0),  # notional 6200, pnl +200
        PositionValue("SE1", 50, 20.0, 230.0, fx_rate=0.09, currency="SEK"),  # value 1035, pnl +35
    ]
    eq = account_equity(4000.0, positions)
    assert eq.cash_positions_value == 4500 + 1035
    assert eq.srd_unrealized_pnl == 200 and eq.srd_notional == 6200 and eq.crd_accrued == 12
    assert eq.equity == 4000 + 5535 + 200 - 12
    assert eq.exposure_total == 5535 + 6200
    assert eq.by_currency == {"EUR": 10700.0, "SEK": 1035.0} and eq.fx_exposure == 1035.0
    assert round(srd_leverage(6200, eq.equity), 3) == round(6200 / 9723, 3)
    assert srd_leverage(6200, 0) is None


def test_srd_coverage_ratio():
    req, avail, ratio = srd_coverage(
        cash=2000,
        equities_value=5000,
        srd_notional=6200,
        coverage_rates={"cash": 0.2, "equities": 0.4},
        broker_rate=None,
    )
    assert req == 1240 and avail == 2000 + 3000 and round(ratio, 2) == 4.03
    assert srd_coverage(0, 0, 0, {"cash": 0.2}, 0.25)[2] is None
