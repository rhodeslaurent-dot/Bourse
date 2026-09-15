"""A1.7 / A1.2 end to end with the alert service: P1 for unprotected quantity in *absent* mode,
incident grouping with 15-min reminders, S1 threshold → SELL at market after 5 min."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.data.dto import DataStatus, MarketPerimeter, PriceType, Quote
from app.db.models import Alert
from app.db.session import db_session
from app.domain.calendar import MarketCalendars
from app.notify.base import Notifier
from app.scheduler.jobs.position_monitor import monitor_once
from app.scheduler.runner import JobContext
from app.services.alerts import AlertService
from app.services.portfolio import declare_execution, declare_stop, ensure_accounts

T = datetime(2026, 9, 15, 8, 0, tzinfo=UTC)  # 10:00 Paris, session open


def _quote(isin_symbol: str, last: float, at: datetime) -> Quote:
    return Quote(
        source="test",
        market_perimeter=MarketPerimeter.PRIMARY,
        received_at=at,
        processed_at=at,
        data_status=DataStatus.REALTIME,
        symbol=isin_symbol,
        market_timestamp=at,
        price_type=PriceType.LAST,
        last=last,
    )


@pytest.fixture
def ctx(config, calendars: MarketCalendars, db_engine):
    sent: list = []
    notifier = Notifier(telegram_send=lambda ev: sent.append(ev))
    c = JobContext(config, calendars, T.date(), ("XPAR",), "normal")
    c.alerts = AlertService(notifier, config.params.notifications.p1_repeat_minutes)
    c.mode = "absent"
    c.sent = sent  # type: ignore[attr-defined]
    return c


def test_unprotected_fill_is_p1_in_absent_mode_and_grouped(ctx):
    with db_session() as s:
        acc = ensure_accounts(s)["cto_srd"]
        r = declare_execution(s, acc.id, "FR0000120271", "buy", 100, 42.4, T, "telegram", mode="srd")
        pid = r.position_id
    assert monitor_once(ctx, {}, T) == 1
    assert ctx.sent[0].level == "P1" and ctx.sent[0].incident_id == f"unprotected:{pid}"
    assert monitor_once(ctx, {}, T + timedelta(minutes=5)) == 0  # same incident, reminder not due
    assert monitor_once(ctx, {}, T + timedelta(minutes=15)) == 1  # reminder every 15 min
    with db_session() as s:
        rows = s.scalars(select(Alert).where(Alert.incident_id == f"unprotected:{pid}")).all()
        assert [r.repeat_count for r in rows if r.sent_at] == [0, 1]
        declare_stop(s, pid, 40.6, "seuil", 100, "initial", "telegram")
    assert monitor_once(ctx, {}, T + timedelta(minutes=30)) == 0
    with db_session() as s:
        assert all(
            r.seen_at is not None for r in s.scalars(select(Alert).where(Alert.incident_id == f"unprotected:{pid}"))
        )


def test_threshold_crossed_then_sell_market_same_incident(ctx):
    with db_session() as s:
        acc = ensure_accounts(s)["cto_cash"]
        r = declare_execution(s, acc.id, "FR0000120271", "buy", 100, 42.4, T, "web")
        declare_stop(s, r.position_id, 40.6, "seuil", 100, "initial", "web")
        pid = r.position_id
    assert monitor_once(ctx, {"FR0000120271": _quote("TTE.PA", 41.0, T)}, T) == 0
    assert (
        monitor_once(ctx, {"FR0000120271": _quote("TTE.PA", 40.5, T + timedelta(minutes=1))}, T + timedelta(minutes=1))
        == 1
    )
    first = ctx.sent[-1]
    assert "seuil de stop franchi" in first.title and first.incident_id == f"stop_crossed:{pid}"
    assert (
        monitor_once(ctx, {"FR0000120271": _quote("TTE.PA", 40.3, T + timedelta(minutes=3))}, T + timedelta(minutes=3))
        == 0
    )
    n = monitor_once(
        ctx, {"FR0000120271": _quote("TTE.PA", 40.3, T + timedelta(minutes=16))}, T + timedelta(minutes=16)
    )
    assert n == 1 and "SELL au marché" in ctx.sent[-1].title and ctx.sent[-1].incident_id == f"stop_crossed:{pid}"
    # price recovers → incident closed, no more alert
    assert (
        monitor_once(
            ctx, {"FR0000120271": _quote("TTE.PA", 41.2, T + timedelta(minutes=40))}, T + timedelta(minutes=40)
        )
        == 0
    )
    with db_session() as s:
        assert all(
            r.seen_at is not None for r in s.scalars(select(Alert).where(Alert.incident_id == f"stop_crossed:{pid}"))
        )


def test_distinct_incidents_are_never_merged(ctx):
    with db_session() as s:
        acc = ensure_accounts(s)["cto_cash"]
        r1 = declare_execution(s, acc.id, "FR0000120271", "buy", 10, 42.4, T, "web")
        r2 = declare_execution(s, acc.id, "FR0000120073", "buy", 5, 171.0, T, "web")
    assert monitor_once(ctx, {}, T) == 2
    assert {e.incident_id for e in ctx.sent} == {f"unprotected:{r1.position_id}", f"unprotected:{r2.position_id}"}
