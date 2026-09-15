"""A1.2 on the persistence layer: double click, declared+import → one matched line, import without
declaration → discretionary?, order alone → no position, partial fill states, stop declarations."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db.models import PositionRow, Trade
from app.db.session import db_session
from app.domain.orders import Execution, MatchTolerance, Side, TradeKind
from app.services.portfolio import (
    declare_execution,
    declare_order,
    declare_stop,
    ensure_accounts,
    import_confirmed_fills,
    unmatched_declarations_older_than,
)

T = datetime(2026, 9, 15, 7, 30, tzinfo=UTC)
ISIN = "FR0000120271"


@pytest.fixture
def accounts(db_engine):
    with db_session() as s:
        accs = ensure_accounts(s)
        return {k: v.id for k, v in accs.items()}


def test_order_entered_creates_no_position(accounts):
    with db_session() as s:
        o = declare_order(s, accounts["cto_srd"], ISIN, "buy", 100, "plage", 42.90, 42.40, "day", "telegram")
        assert o.state == "entered"
        assert s.scalar(select(PositionRow)) is None


def test_double_click_creates_one_declaration(accounts):
    with db_session() as s:
        r1 = declare_execution(s, accounts["cto_srd"], ISIN, "buy", 100, 42.40, T, "telegram", mode="srd")
        r2 = declare_execution(
            s, accounts["cto_srd"], ISIN, "buy", 100, 42.40, T + timedelta(seconds=15), "web", mode="srd"
        )
        assert r1.created and not r2.created and r1.trade_id == r2.trade_id
        assert len(s.scalars(select(Trade)).all()) == 1
        pos = s.get(PositionRow, r1.position_id)
        assert pos.qty_held == 100 and pos.execution_state == "filled" and pos.protection_state == "unprotected"


def test_declared_then_imported_matched_into_one_line(accounts):
    with db_session() as s:
        r = declare_execution(s, accounts["cto_srd"], ISIN, "buy", 100, 42.40, T, "telegram", mode="srd")
        fill = Execution(
            accounts["cto_srd"],
            ISIN,
            Side.BUY,
            100,
            42.52,
            T + timedelta(minutes=3),
            TradeKind.CONFIRMED,
            broker_fill_id="saxo-f1",
            fees=3.4,
            source="saxo_api",
            mode="srd",
        )
        rep = import_confirmed_fills(s, [fill], MatchTolerance(0.005, 10))
        assert rep.matched == 1 and rep.discretionary == 0
        declared = s.get(Trade, r.trade_id)
        confirmed = s.get(Trade, declared.matched_trade_id)
        assert confirmed.broker_fill_id == "saxo-f1" and confirmed.matched_trade_id == declared.id
        pos = s.get(PositionRow, r.position_id)
        assert (
            pos.qty_held == 100 and abs(pos.avg_price - (100 * 42.52 + 3.4) / 100) < 1e-9
        )  # broker price prevails, no double count
        rep2 = import_confirmed_fills(s, [fill], MatchTolerance())
        assert rep2.duplicates_ignored == 1 and s.get(PositionRow, r.position_id).qty_held == 100


def test_import_without_declaration_is_discretionary(accounts):
    with db_session() as s:
        fill = Execution(
            accounts["cto_cash"],
            "NL0010273215",
            Side.BUY,
            3,
            640.5,
            T,
            TradeKind.CONFIRMED,
            broker_fill_id="saxo-f9",
            source="saxo_api",
        )
        rep = import_confirmed_fills(s, [fill], MatchTolerance())
        assert rep.discretionary == 1
        t = s.scalar(select(Trade).where(Trade.broker_fill_id == "saxo-f9"))
        assert t.tags == ["discretionary?"] and s.get(PositionRow, t.position_id).qty_held == 3


def test_partial_fill_states_and_stop_declaration(accounts):
    with db_session() as s:
        o = declare_order(s, accounts["pea"], ISIN, "buy", 100, "limite", 42.90, None, "day", "web")
        r = declare_execution(s, accounts["pea"], ISIN, "buy", 40, 42.40, T, "web", order_id=o.id)
        pos = s.get(PositionRow, r.position_id)
        assert (
            pos.execution_state == "filled_partial"
            and pos.protection_state == "unprotected"
            and o.state == "partially_filled"
        )
        declare_stop(s, pos.id, 40.60, "seuil", 25, "initial", "telegram")
        assert pos.protection_state == "partially_protected" and pos.qty_protected == 25 and pos.stop_current == 40.60
        with pytest.raises(ValueError):
            declare_stop(s, pos.id, 39.00, "seuil", 40, "trailing", "telegram")
        declare_stop(s, pos.id, 40.60, "seuil", 15, "initial", "telegram")
        assert pos.protection_state == "protected" and pos.protected_at is not None


def test_unmatched_declaration_older_than_24h(accounts):
    with db_session() as s:
        declare_execution(s, accounts["cto_cash"], ISIN, "buy", 10, 42.0, T - timedelta(hours=30), "telegram")
        declare_execution(s, accounts["cto_cash"], ISIN, "buy", 5, 42.0, T - timedelta(hours=1), "telegram")
        old = unmatched_declarations_older_than(s, 24, now=T)
        assert len(old) == 1 and old[0].qty == 10
