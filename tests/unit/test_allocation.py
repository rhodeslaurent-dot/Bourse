"""Lots M1 and M2 (docs/39, ADR-018) — the active part against the same money placed in the core (public market
equivalent, Kaplan & Schoar 2005): exact arithmetic on hand-made cases, the tax on gains realised on the securities
account only, losses carried forward, « indisponible » rather than an estimate; rebalancing bands; the first session
of the month."""

from __future__ import annotations

from datetime import date

import pytest

from app.domain.allocation import (
    Fill,
    Holding,
    compare_to_core,
    first_session_of_month,
    realized_gains,
    rebalance_check,
    tax_on_gains,
)

D1, D2, END = date(2026, 3, 2), date(2026, 6, 1), date(2026, 10, 30)
CORE = {D1: 100.0, D2: 105.0, END: 110.0}


def test_one_purchase_against_the_core():
    f = Fill(D1, "pea", "X", "buy", 10, 50.0, fees_eur=1.0)
    r = compare_to_core([f], [Holding("X", 10, 55.0)], CORE, END)
    assert r.ok and r.contributions_eur == 501.0 and r.nav_eur == 550.0
    assert r.core_eur == pytest.approx(501.0 * 110 / 100) and r.pme == pytest.approx(550 / 551.1)
    assert r.diff_eur == pytest.approx(550 - 551.1) and r.tax_eur == 0


def test_a_satellite_that_is_the_core_is_even():
    r = compare_to_core([Fill(D1, "pea", "C", "buy", 10, 100.0)], [Holding("C", 10, 110.0)], CORE, END)
    assert r.diff_eur == pytest.approx(0.0, abs=1e-9) and r.pme == pytest.approx(1.0)


def test_the_core_pays_its_buying_cost():
    r = compare_to_core([Fill(D1, "pea", "C", "buy", 10, 100.0)], [Holding("C", 10, 110.0)], CORE, END, 0.005)
    assert r.core_eur == pytest.approx(1000 * 0.995 * 1.1) and r.diff_eur == pytest.approx(5.5)


@pytest.mark.parametrize(("account", "tax"), [("cto_cash", 0.314 * 200), ("pea", 0.0)])
def test_a_winning_sale_is_taxed_on_the_securities_account_only(account, tax):
    fills = [Fill(D1, account, "X", "buy", 10, 100.0), Fill(D2, account, "X", "sell", 10, 120.0)]
    r = compare_to_core(fills, [], CORE, END, tax_rate=0.314)
    assert r.tax_eur == pytest.approx(tax) and r.distributions_eur == 1200.0
    assert r.satellite_eur == pytest.approx(1200 * 110 / 105 - tax) and r.core_eur == pytest.approx(1100.0)


def test_a_dollar_trade_is_converted_with_its_own_rate():
    f = Fill(D1, "cto_cash", "US", "buy", 10, 100.0, fx_rate=0.9, fees_eur=2.0)
    assert f.cash_eur == pytest.approx(902.0)


def test_average_cost_and_losses_carried_forward():
    fills = [
        Fill(date(2025, 1, 2), "cto_cash", "X", "buy", 10, 100.0, fees_eur=10.0),  # cost 1 010 → 101 per share
        Fill(date(2025, 5, 2), "cto_cash", "X", "sell", 5, 81.0),  # 405 − 505 = −100
        Fill(date(2026, 2, 2), "cto_cash", "X", "sell", 5, 131.0),  # 655 − 505 = +150
    ]
    by = realized_gains(fills)
    assert by == {2025: pytest.approx(-100.0), 2026: pytest.approx(150.0)}
    assert tax_on_gains(by, 0.314) == pytest.approx(0.314 * 50)
    assert tax_on_gains({2014: -100.0, 2026: 150.0}, 0.314) == pytest.approx(0.314 * 150)  # older than 10 years


@pytest.mark.parametrize(
    ("fills", "holdings", "reason"),
    [
        ([], [], "aucun ordre"),
        ([Fill(date(2026, 1, 5), "pea", "X", "buy", 1, 10.0)], [Holding("X", 1, 11.0)],
         "cours du cœur inconnu le 05/01"),
        ([Fill(D1, "pea", "X", "buy", 1, 10.0)], [Holding("X", 1, None)], "cours inconnu pour X"),
        ([Fill(D1, "cto_cash", "X", "sell", 1, 10.0)], [], "vente de 1 X"),
    ],
)  # fmt: skip
def test_never_an_estimate(fills, holdings, reason):
    r = compare_to_core(fills, holdings, CORE, END)
    assert r.status == "indisponible" and reason in (r.reason or "") and r.pme is None


def test_rebalancing_bands():
    assert rebalance_check({"coeur": 0.651}, {"coeur": 0.70}, 5) == []  # 4.9 points
    (d,) = rebalance_check({"coeur": 0.649}, {"coeur": 0.70}, 5)  # 5.1 points
    assert d.pocket == "coeur" and d.gap_pts == pytest.approx(-5.1)


def test_first_session_of_the_month():
    weekdays = lambda d: d.weekday() < 5  # noqa: E731
    assert first_session_of_month(date(2026, 11, 2), weekdays)  # Sunday 1st: Monday 2nd is the first session
    assert not first_session_of_month(date(2026, 11, 3), weekdays)
    assert not first_session_of_month(date(2026, 11, 1), weekdays)
