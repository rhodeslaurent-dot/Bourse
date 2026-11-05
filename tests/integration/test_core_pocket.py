"""Lot C1 (docs/39 §5, decision D1) — the core ETF of ``pockets.dca.isins``: registered from the
provider's ETF list and its closes loaded (``core-setup``); its series chained to the declared proxy before its
first close; never a signal card, never a cap, never « sans protection », out of the circuit breaker (R17)."""

from __future__ import annotations

import copy
from datetime import UTC, date, datetime, timedelta

import pytest

from app.data.dto import DataStatus, EodBar, MarketPerimeter
from app.db.models import FxRate, Instrument, KpiDaily, MacroPoint, PositionRow, PriceEod
from app.db.session import db_session
from app.services import allocation
from app.services.portfolio import declare_execution, ensure_accounts

CORE = "FR0000000CR1"  # a synthetic core ETF (ISIN, name and prices are fixtures)
NOW = datetime(2026, 11, 5, 18, 0, tzinfo=UTC)


def _cfg(config):
    c = copy.deepcopy(config)
    c.params.pockets["dca"]["isins"] = [CORE]
    return c


class FakeEodhd:
    def __init__(self) -> None:
        self.asked: list[tuple[str, date]] = []

    def exchange_symbols(self, exchange, types):  # type: ignore[no-untyped-def]
        assert exchange == "PA" and types == ("etf",)
        return [{"Code": "CRWD", "Name": "ETF Monde fictif UCITS ETF Acc", "Isin": CORE,
                 "Currency": "EUR", "Type": "ETF", "Exchange": "PA"}]  # fmt: skip

    def fetch_eod(self, symbols, start, end):  # type: ignore[no-untyped-def]
        self.asked.append((symbols[0], start))
        days = [date(2025, 3, 4), date(2025, 3, 5), date(2026, 11, 4)]
        return [EodBar(source="eodhd", market_perimeter=MarketPerimeter.PRIMARY, received_at=NOW, processed_at=NOW,
                       data_status=DataStatus.EOD, symbol=symbols[0], day=d, open=5.0, high=5.0, low=5.0,
                       close=c, volume=1000) for d, c in zip(days, (5.0, 5.05, 6.0), strict=True)]  # fmt: skip


def test_core_setup_registers_the_etf_out_of_the_universe_and_loads_its_closes(config, db_engine):
    p = FakeEodhd()
    with db_session() as s:
        out = allocation.setup_core(s, p, _cfg(config), date(2016, 1, 1), date(2026, 11, 5))
        inst = s.get(Instrument, CORE)
        assert [r.status for r in out] == ["enregistré"] and out[0].bars == 3 and out[0].first == date(2025, 3, 4)
        assert inst.ticker_eodhd == "CRWD.PA" and inst.mic == "XPAR" and inst.kind == "etf"
        assert inst.in_universe is False and inst.active is True  # the daily EOD job loads every active instrument
        again = allocation.setup_core(s, p, _cfg(config), date(2016, 1, 1), date(2026, 11, 5))
        assert again[0].status == "déjà enregistré"
        c = _cfg(config)
        c.params.pockets["dca"]["isins"] = ["FR0000000XX0"]
        assert allocation.setup_core(s, p, c, date(2016, 1, 1), date(2026, 11, 5))[0].status == "introuvable"


def test_the_core_series_is_chained_to_the_proxy_before_the_etf_existed(config, db_engine):
    with db_session() as s:
        s.add(Instrument(isin=CORE, name="ETF Monde fictif", mic="XPAR", currency="EUR", kind="etf"))
        s.flush()
        for d, c in ((date(2025, 3, 4), 5.0), (date(2026, 10, 30), 6.0)):
            s.add(PriceEod(isin=CORE, day=d, open=c, high=c, low=c, close=c, volume=1, source="eodhd",
                           received_at=NOW, data_status="eod"))  # fmt: skip
        for d, usd in ((date(2023, 6, 1), 100.0), (date(2025, 3, 4), 150.0)):
            s.add(MacroPoint(indicator="msci_world", day=d, value=usd, source="yfinance", symbol="URTH",
                             received_at=NOW, data_status="eod"))  # fmt: skip
            s.add(FxRate(pair="EURUSD", day=d, rate=1.0, source="ecb"))
        s.flush()
        series = allocation.core_series(s, _cfg(config), date(2026, 10, 30))
    assert series is not None and series.proxy and "depuis le 04/03/2025" in series.label and "raccordé" in series.label
    p = series.prices
    assert p[date(2025, 3, 4)] == 5.0 and p[date(2023, 6, 1)] == pytest.approx(5.0 * 100 / 150)
    # an order of 2023 grows as the proxy until the ETF exists, then as the ETF: (150 / 100) × (6 / 5)
    assert p[date(2026, 10, 30)] / p[date(2023, 6, 1)] == pytest.approx(1.5 * 1.2)


def test_the_core_is_never_a_card_a_cap_an_unprotected_line_or_a_circuit_loss(config, calendars, db_engine):
    from app.scheduler.jobs.position_monitor import monitor_once
    from app.scheduler.runner import JobContext
    from app.services import engine
    from app.services.circuit import circuit_state
    from app.services.risk_view import build_risk_view

    c = _cfg(config)
    with db_session() as s:
        s.add(Instrument(isin=CORE, name="ETF Monde fictif", mic="XPAR", currency="EUR", kind="etf",
                         in_universe=True))  # fmt: skip
        s.flush()
        assert CORE not in {i.isin for i in engine._candidates(s, None, allocation.core_isins(c))}
        acc = ensure_accounts(s)["pea"]
        declare_execution(s, acc.id, CORE, "buy", 1000, 5.0, NOW - timedelta(days=3), "web")  # no stop, by design
        row = s.query(PositionRow).filter(PositionRow.isin == CORE).one()
        row.last_price, row.last_price_at, row.last_price_status = 4.5, NOW, "eod"
        s.flush()
        assert engine._book(s, allocation.core_isins(c)).lines == []
        view = build_risk_view(s, c, NOW)
        r = view["risk"]
        assert view["unprotected"] == [] and r.positions_count == 0 and r.exposure_eur == 0.0
        assert view["pea"].equity >= 4500  # its value stays in the PEA
        # the world falls 10 % this week: −500 € of latent on the core, nothing on the active part
        s.add(KpiDaily(day=date(2026, 10, 30), account_id=None, equity=10000, realized_pnl_cum=0.0, latent_pnl=0.0,
                       core_latent_pnl=0.0, core_realized_pnl_cum=0.0, computed_at=NOW))  # fmt: skip
        s.flush()
        v = circuit_state(s, c, NOW)
        assert v.week_r == pytest.approx(0.0) and not v.tripped
        plain = circuit_state(s, config, NOW)  # without the core pocket declared, the same fall is a loss
        assert plain.week_r is not None and plain.week_r < 0
    from app.notify.base import Notifier
    from app.services.alerts import AlertService

    sent: list = []
    for cfg_, want in ((config, 1), (c, 0)):  # the same line: P1 when it is not the core, never as the core
        ctx = JobContext(cfg_, calendars, NOW.date(), ("XPAR",), "normal")
        ctx.alerts = AlertService(Notifier(telegram_send=lambda ev: sent.append(ev)),
                                  config.params.notifications.p1_repeat_minutes)  # fmt: skip
        ctx.mode = "absent"
        assert monitor_once(ctx, {}, NOW) == want


def test_core_setup_says_where_to_put_the_isin(config, db_engine, monkeypatch, capsys):
    from app import cli

    monkeypatch.setattr(cli, "load_params", lambda _p: config)  # pockets.dca.isins empty in the example
    assert cli.main(["core-setup"]) == 2
    assert "pockets.dca.isins est vide" in capsys.readouterr().out
