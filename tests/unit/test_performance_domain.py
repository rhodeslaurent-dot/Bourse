"""docs/17 §17.5: realized P&L by FIFO, R-multiples, quality statistics and drawdown."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.domain.performance import (
    ClosedTrade,
    Leg,
    by_month,
    group_total,
    latent_pnl,
    max_drawdown,
    quality,
    r_multiple,
    realize_fifo,
)

T = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


def _t(day: int) -> datetime:
    return T.replace(day=day)


def test_fifo_with_fees_and_partial_exit():
    legs = [
        Leg("buy", 100, 40.0, 10.0, _t(1)),  # unit cost 40.10
        Leg("buy", 50, 44.0, 5.0, _t(2)),  # unit cost 44.10
        Leg("sell", 120, 46.0, 12.0, _t(5)),
    ]
    r = realize_fifo(legs)
    # 100 × 40.10 + 20 × 44.10 = 4892 ; proceeds 120 × 46 − 12 = 5508
    assert r.realized_pnl == pytest.approx(5508 - 4892)
    assert r.closed_qty == 120 and r.open_qty == 30 and r.open_cost == pytest.approx(30 * 44.10)
    assert r.fees == pytest.approx(27.0) and r.first_buy == _t(1) and r.last_sell == _t(5)


def test_orphan_sale_is_ignored_for_pnl():
    r = realize_fifo([Leg("sell", 10, 50.0, 1.0, _t(3))])
    assert r.realized_pnl == 0 and r.closed_qty == 0 and r.sells == []


def test_r_multiple_and_latent():
    assert r_multiple(684.0, 342.0) == 2.0
    assert r_multiple(100.0, None) is None and r_multiple(100.0, 0) is None
    assert latent_pnl(10, 15.62, 16.0, 1.0) == pytest.approx(2.8)
    assert latent_pnl(10, 15.62, None) is None  # never estimated


def test_quality_reliability_and_profit_factor():
    trades = [
        ClosedTrade("A", "pea", date(2026, 9, 3), 300.0, 2.0, 5),
        ClosedTrade("B", "pea", date(2026, 9, 20), -150.0, -1.0, 3),
        ClosedTrade("C", "cto_srd", date(2026, 10, 2), 150.0, None, 8, mode="srd"),
    ]
    q = quality(trades)
    assert q.n == 3 and q.wins == 2 and q.win_rate == pytest.approx(2 / 3)
    assert q.profit_factor == pytest.approx(3.0) and q.total == 300.0
    assert q.expectancy_r == pytest.approx(0.5)  # only trades with a known R
    assert q.reliability == "indicatif (< 30 trades)"
    assert quality([]).reliability == "aucun trade" and quality([]).win_rate is None
    assert by_month(trades) == {"2026-09": 150.0, "2026-10": 150.0}
    assert group_total(trades, "account") == {"pea": 150.0, "cto_srd": 150.0}


def test_max_drawdown():
    assert max_drawdown([100, 120, 90, 130, 117]) == pytest.approx(0.25)
    assert max_drawdown([]) is None
