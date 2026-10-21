"""docs/17 §17.5 (A17.3): realized and latent with status, quality with reliability, equity from kpi_daily."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import Account, KpiDaily, PriceEod
from app.db.session import db_session
from app.main import create_app
from app.services.performance import build_performance, snapshot_equity
from app.services.portfolio import declare_execution, declare_stop, ensure_accounts

T = datetime(2026, 10, 1, 7, 30, tzinfo=UTC)


@pytest.fixture
def client(config, calendars, db_engine, monkeypatch):
    monkeypatch.setenv("ALLOW_UNAUTHENTICATED_WRITES", "1")
    app = create_app(config, calendars, start_scheduler=False, start_telegram=False)
    with TestClient(app) as c:
        yield c


def _seed():
    with db_session() as s:
        accs = ensure_accounts(s)
        r = declare_execution(s, accs["pea"].id, "FR0000120073", "buy", 10, 170.0, T, "web")
        declare_stop(s, r.position_id, 160.0, "seuil", 10, "initial", "web")
        declare_execution(s, accs["pea"].id, "FR0000120073", "sell", 10, 190.0, T.replace(day=8), "web")
        declare_execution(s, accs["cto_cash"].id, "FR0000120271", "buy", 100, 55.0, T.replace(day=9), "web")
        s.add(
            PriceEod(
                isin="FR0000120271",
                day=date(2026, 10, 20),
                open=56,
                high=57,
                low=55.5,
                close=56.5,
                volume=1,
                source="eodhd",
                received_at=T,
                data_status="eod",
            )
        )
        accs["pea"].cash = 1000.0
        accs["cto_cash"].cash = 2000.0


def test_build_performance_realized_latent_and_quality(db_engine, config):
    _seed()
    with db_session() as s:
        d = build_performance(s, config)
    v, q = d["v"], d["q"]
    assert len(v.closed) == 1 and 0 < v.closed[0].pnl <= 200.0  # (190 − 170) × 10, minus fees
    assert v.closed[0].r == pytest.approx(v.closed[0].pnl / 100.0)  # risk (170 − 160) × 10
    line = v.open_lines[0]
    assert line.isin == "FR0000120271" and line.status == "eod" and line.last_price == 56.5
    assert q.n == 1 and q.reliability == "indicatif (< 30 trades)"


def test_snapshot_equity_flags_unknown_cash_and_is_idempotent(db_engine, config):
    _seed()
    with db_session() as s:
        snapshot_equity(s, config, date(2026, 10, 20))
        snapshot_equity(s, config, date(2026, 10, 20))
        rows = list(s.scalars(select(KpiDaily).where(KpiDaily.day == date(2026, 10, 20))))
        accounts = list(s.scalars(select(Account)))
        assert len(rows) == len(accounts) + 1  # one per account + consolidated
        cons = next(r for r in rows if r.account_id is None)
        assert cons.incomplete is True  # cto_srd cash unknown → never estimated
        cto = next(r for r in rows if r.account_id == next(a.id for a in accounts if a.type == "cto_cash"))
        assert cto.equity == pytest.approx(2000.0 + 100 * 56.5) and cto.incomplete is False


def test_performance_page_renders(client):
    _seed()
    html = client.get("/performance").text
    assert "Performance" in html and "Réalisé (positions closes)" in html and "indicatif (&lt; 30 trades)" in html
    assert "Clôture" in html and "56.500 €" in html and "La courbe apparaîtra" in html
    with db_session() as s:
        snapshot_equity(s, client.app.state.config, date(2026, 10, 19))
        snapshot_equity(s, client.app.state.config, date(2026, 10, 20))
    html = client.get("/performance").text
    assert "<polyline" in html and "La courbe apparaîtra" not in html
