"""Lot M1 (docs/39, ADR-018) — the active part against the core from stored data: executions (a declaration matched by
a confirmed fill counted once), the core pocket excluded, the core's own closes or the declared proxy (URTH in USD
converted with the stored EUR/USD rate), the review's « Cap » line, the read-only ``core-compare`` command."""

from __future__ import annotations

import copy
from datetime import UTC, date, datetime

import pytest

from app.db.models import FxRate, Instrument, MacroPoint, PriceEod
from app.db.session import db_session
from app.services import allocation
from app.services.portfolio import declare_execution, ensure_accounts

CORE, STOCK = "LU0000000CW8", "FR0000000ST1"
D1, END = date(2026, 3, 2), date(2026, 10, 30)
T1 = datetime(2026, 3, 2, 10, 0, tzinfo=UTC)


def _bar(isin: str, day: date, close: float) -> PriceEod:
    return PriceEod(isin=isin, day=day, open=close, high=close, low=close, close=close, volume=1, source="fixture",
                    received_at=datetime(2026, 10, 30, tzinfo=UTC))  # fmt: skip


def _seed(s, core_bars: bool = True, core_trade: bool = True) -> None:
    s.add(Instrument(isin=CORE, name="ETF Monde fictif", mic="XPAR", currency="EUR", kind="etf"))
    s.add(Instrument(isin=STOCK, name="Société fictive", mic="XPAR", currency="EUR"))
    s.flush()
    if core_bars:
        s.add_all([_bar(CORE, D1, 500.0), _bar(CORE, END, 550.0)])
    s.add_all([_bar(STOCK, D1, 20.0), _bar(STOCK, END, 21.0)])
    accs = ensure_accounts(s)
    declare_execution(s, accs["pea"].id, STOCK, "buy", 50, 20.0, T1, "web")  # 1 000 € in the active part
    if core_trade:  # the core pocket: never in the comparison
        declare_execution(s, accs["pea"].id, CORE, "buy", 2, 500.0, T1, "web")
    s.flush()


def _cfg(config, with_core: bool = True):
    c = copy.deepcopy(config)
    c.params.pockets["dca"]["isins"] = [CORE] if with_core else []
    return c


def test_the_active_part_against_the_core_etf(config, db_engine):
    with db_session() as s:
        _seed(s)
        res, series = allocation.compare(s, _cfg(config), END)
    assert res.ok and res.flows == 1 and series is not None and not series.proxy and CORE in series.label
    assert res.contributions_eur == pytest.approx(1000.0) and res.nav_eur == pytest.approx(1050.0)
    assert res.core_eur == pytest.approx(1000 * 0.995 * 1.1)  # core_cost_pct 0.5 % in params.example.yaml
    line = allocation.line(res, series)
    assert "fait moins bien que" in line and "ETF Monde fictif" in line and "1 ordre(s)" in line


def test_without_core_prices_the_declared_proxy_is_used(config, db_engine):
    with db_session() as s:
        _seed(s, core_bars=False, core_trade=False)
        for day, usd in ((D1, 100.0), (END, 120.0)):
            s.add(MacroPoint(indicator="msci_world", day=day, value=usd, source="yfinance", symbol="URTH",
                             received_at=datetime(2026, 10, 30, tzinfo=UTC), data_status="eod"))  # fmt: skip
            s.add(FxRate(pair="EURUSD", day=day, rate=1.25 if day == D1 else 1.20, source="ecb"))
        s.flush()
        res, series = allocation.compare(s, _cfg(config, with_core=False), END)
    assert series is not None and series.proxy and "proxy déclaré" in series.label and "hors dividendes" in series.label
    growth = (120 / 1.20) / (100 / 1.25)  # the core in EUR
    assert res.ok and res.flows == 1 and res.core_eur == pytest.approx(1000 * 0.995 * growth)


def test_no_core_price_at_all_is_unavailable_and_said(config, db_engine):
    with db_session() as s:
        _seed(s, core_bars=False)
        res, series = allocation.compare(s, _cfg(config, with_core=False), END)
    assert not res.ok and series is None
    assert allocation.line(res, series).startswith("Partie active contre le cœur : indisponible (aucun cours du cœur")


def test_the_review_shows_the_cap_and_the_command_prints_the_detail(config, db_engine, monkeypatch, capsys):
    from app import cli
    from app.services import daily_review

    with db_session() as s:
        _seed(s)
        r = daily_review.build(s, _cfg(config), END)
    assert r["cap"].startswith("Partie active contre le cœur depuis le 02/03/2026")
    assert "Cap" not in daily_review.as_text(r) and r["cap"] in daily_review.as_text(r)
    monkeypatch.setattr(cli, "load_params", lambda _p: _cfg(config))
    assert cli.main(["core-compare", "--to", END.isoformat()]) == 0
    out = capsys.readouterr().out
    assert "achats de la partie active : 1 000,00 €" in out and "Kaplan & Schoar 2005" in out


def test_without_the_allocation_group_nothing_is_shown(config, db_engine):
    from app.services import daily_review

    c = _cfg(config)
    c.params.allocation["enabled"] = False
    with db_session() as s:
        _seed(s)
        assert daily_review.build(s, c, END)["cap"] is None


def test_first_session_of_the_month_brings_the_core_purchase_and_its_band(config, db_engine):
    """docs/39 §5 (lot M2): on the first session of November at Euronext Paris, the core's monthly purchase; with a
    target the user set, the core's weight against its band — new money to the core, never a sale."""
    from app.services import daily_review

    c = _cfg(config)
    c.params.allocation["core_target_pct"] = 0.70
    with db_session() as s:
        _seed(s)
        asks = [a["texte"] for a in daily_review.build(s, c, date(2026, 11, 2))["attentes"]]
        assert any(a.startswith("Achat mensuel du cœur (R17) : ETF Monde fictif") for a in asks)
        # core 2 × 550 = 1 100 €, active part 50 × 21 = 1 050 €: 51 % for 70 %
        assert any("Le cœur pèse 51% pour une cible de 70%" in a and "vers le cœur" in a for a in asks)
        later = [a["texte"] for a in daily_review.build(s, c, date(2026, 11, 3))["attentes"]]
        assert not any("cœur" in a for a in later)  # once a month
        assert not any(
            "cœur" in a["texte"] for a in daily_review.build(s, _cfg(config, False), date(2026, 11, 2))["attentes"]
        )
