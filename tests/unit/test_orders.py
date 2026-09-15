"""A1.2: order ≠ position; double click = one declaration; declared+confirmed matched into one line;
partial fill → filled_partial + partially_protected; stop never goes down."""

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.orders import (
    BrokerStopStatus,
    Execution,
    ExecutionState,
    MatchTolerance,
    OrderState,
    Position,
    ProtectionState,
    Side,
    Stop,
    StopKind,
    StopOrderType,
    TradeKind,
    declared_uid,
    match_executions,
    merge_matched,
    order_state,
)

T = datetime(2026, 9, 15, 7, 30, tzinfo=UTC)


def _decl(qty=100, price=42.40, ts=T, uid=None):
    return Execution(
        1,
        "FR0000120271",
        Side.BUY,
        qty,
        price,
        ts,
        TradeKind.DECLARED,
        declared_uid=uid or declared_uid(1, "FR0000120271", ts, qty, price),
        source="telegram",
    )


def _conf(qty=100, price=42.40, ts=T, fid="saxo-1"):
    return Execution(
        1,
        "FR0000120271",
        Side.BUY,
        qty,
        price,
        ts,
        TradeKind.CONFIRMED,
        broker_fill_id=fid,
        fees=3.4,
        source="saxo_api",
    )


def test_double_click_gives_same_declared_uid():
    a = declared_uid(1, "FR0000120271", T, 100, 42.40)
    b = declared_uid(1, "FR0000120271", T + timedelta(seconds=20), 100, 42.40)
    assert a == b
    assert declared_uid(1, "FR0000120271", T, 100, 42.41) != a


def test_order_entered_is_not_a_position():
    p = Position(account_id=1, isin="FR0000120271", qty_ordered=100)
    assert p.execution_state == ExecutionState.ORDER_ENTERED and p.qty_held == 0
    assert order_state(100, 0) == OrderState.ENTERED
    assert order_state(100, 40) == OrderState.PARTIALLY_FILLED
    assert order_state(100, 100) == OrderState.FILLED
    assert order_state(100, 40, cancelled=True) == OrderState.CANCELLED


def test_partial_fill_and_partial_protection_are_coherent():
    p = Position(account_id=1, isin="FR0000120271", qty_ordered=100)
    p.apply_execution(_decl(qty=40))
    assert p.execution_state == ExecutionState.FILLED_PARTIAL and p.protection_state == ProtectionState.UNPROTECTED
    assert p.qty_unprotected == 40
    p.apply_stop(Stop(40.60, StopOrderType.SEUIL, 25, StopKind.INITIAL, T, BrokerStopStatus.ACTIVE))
    assert (
        p.protection_state == ProtectionState.PARTIALLY_PROTECTED and p.qty_protected == 25 and p.qty_unprotected == 15
    )
    p.apply_stop(Stop(40.60, StopOrderType.SEUIL, 15, StopKind.INITIAL, T, BrokerStopStatus.ACTIVE))
    assert p.protection_state == ProtectionState.PROTECTED and p.protected_at == T
    p.apply_execution(_decl(qty=60, ts=T + timedelta(minutes=3)))
    assert p.execution_state == ExecutionState.FILLED and p.protection_state == ProtectionState.PARTIALLY_PROTECTED
    assert p.qty_unprotected == 60


def test_stop_never_goes_down():
    p = Position(account_id=1, isin="X")
    p.apply_execution(_decl())
    p.apply_stop(Stop(40.60, StopOrderType.SEUIL, 100, StopKind.INITIAL, T, BrokerStopStatus.ACTIVE))
    with pytest.raises(ValueError):
        p.apply_stop(Stop(40.00, StopOrderType.SEUIL, 100, StopKind.TRAILING, T))
    p.apply_stop(Stop(41.50, StopOrderType.SEUIL, 100, StopKind.TRAILING, T, BrokerStopStatus.ACTIVE))
    assert p.stop_current == 41.50 and p.stop_initial == 40.60


def test_sale_closes_and_flags_stale_stop():
    p = Position(account_id=1, isin="X")
    p.apply_execution(_decl())
    p.apply_stop(Stop(40.60, StopOrderType.SEUIL, 100, StopKind.INITIAL, T, BrokerStopStatus.ACTIVE))
    p.apply_execution(
        Execution(1, "X", Side.SELL, 50, 45.0, T + timedelta(days=2), TradeKind.CONFIRMED, broker_fill_id="s2")
    )
    assert p.qty_held == 50 and len(p.stale_stops()) == 1
    with pytest.raises(ValueError):
        p.apply_execution(Execution(1, "X", Side.SELL, 500, 45.0, T, TradeKind.CONFIRMED))
    p.apply_execution(Execution(1, "X", Side.SELL, 50, 45.0, T + timedelta(days=3), TradeKind.CONFIRMED))
    assert p.execution_state == ExecutionState.CLOSED and p.closed_at is not None


def test_declared_then_imported_is_matched_into_one_line():
    tol = MatchTolerance(0.005, 10)
    d = _decl(price=42.40)
    c = _conf(price=42.55, ts=T + timedelta(minutes=4))  # +0.35 %, +4 min
    pairs, left_d, left_c = match_executions([d], [c], tol)
    assert len(pairs) == 1 and not left_d and not left_c
    merged = merge_matched(*pairs[0])
    assert (
        merged.declared_uid == d.declared_uid
        and merged.broker_fill_id == "saxo-1"
        and merged.price == 42.55
        and merged.fees == 3.4
    )


def test_matching_respects_tolerances_and_quantity():
    tol = MatchTolerance(0.005, 10)
    assert match_executions([_decl()], [_conf(price=42.70)], tol)[1]  # +0.7 % → no match
    assert match_executions([_decl()], [_conf(ts=T + timedelta(minutes=11))], tol)[1]
    assert match_executions([_decl(qty=100)], [_conf(qty=99)], tol)[1]
    pairs, _, left_c = match_executions([_decl()], [_conf(fid="a"), _conf(fid="b", ts=T + timedelta(minutes=1))], tol)
    assert len(pairs) == 1 and pairs[0][1].broker_fill_id == "a" and left_c[0].broker_fill_id == "b"  # one-to-one


def test_import_without_declaration_is_left_for_discretionary_tag():
    _, _, left_c = match_executions([], [_conf()], MatchTolerance())
    assert len(left_c) == 1
