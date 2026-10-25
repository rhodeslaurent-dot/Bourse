"""BoursoBank formulas: tiered grid with the PEA cap (0,5 %), monthly conditions, recommendation."""

import pytest

from app.domain.broker_plan import Formula, compare, month_cost
from app.domain.risk import FeeSchedule

CAP = 0.005


def _f(name, bands, **kw):
    return Formula(name, name, FeeSchedule("boursobank", None, 0.0, CAP, bands), **kw)


DEC = _f("decouverte", ((500, 1.99, 0, 0), (None, 0, 0.006, 0)))
TRA = _f("trader", ((7750, 16.65, 0, 0), (None, 0, 0.0022, 0)), fee_if_no_order=5.95)
ULT = _f("ultimate_trader", ((10000, 9.90, 0, 0), (None, 0, 0.0012, 0)), min_orders=30, fee_if_below=119)


def test_grid_and_pea_cap():
    assert DEC.fees.commission(5000) == pytest.approx(25.0)  # 0,6 % = 30 € capped at 0,5 % on a PEA
    assert DEC.fees.commission(450) == pytest.approx(1.99)
    assert DEC.fees.commission(200) == pytest.approx(1.0)  # 1,99 € > 0,5 % of 200 €: capped
    assert TRA.fees.commission(5000) == pytest.approx(16.65) and TRA.fees.commission(2000) == pytest.approx(10.0)
    assert ULT.fees.commission(20000) == pytest.approx(24.0)


def test_monthly_conditions():
    assert month_cost(TRA, []) == pytest.approx(5.95)
    assert month_cost(ULT, [5000] * 10) == pytest.approx(10 * 9.90 + 119)


def test_recommendation_follows_the_activity():
    busy = compare([DEC, TRA, ULT], [[5000] * 6] * 3, "decouverte")
    assert busy.best == "trader" and busy.saving_per_month == pytest.approx(6 * (25 - 16.65))
    assert "Passer de decouverte à trader" in busy.recommendation
    small = compare([DEC, TRA, ULT], [[2000] * 4] * 3, "decouverte")  # both capped at 0,5 %: no gain
    assert small.best == "decouverte" and small.recommendation.startswith("Garder")
