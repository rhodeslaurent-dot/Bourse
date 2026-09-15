"""A1.7: threshold crossed → P1 immediately; after 5 min without confirmed exit → SELL at market in
the same incident; stop executed is a distinct state. Unprotected quantity → P1 in every mode."""

from datetime import UTC, datetime, timedelta

from app.domain.alerts import allowed_in_mode, decide_p1
from app.domain.monitor import StopEvent, check_stop, check_unprotected, is_state_fresh
from app.domain.orders import BrokerStopStatus, Execution, Position, Side, Stop, StopKind, StopOrderType, TradeKind

T = datetime(2026, 9, 15, 8, 0, tzinfo=UTC)


def _pos(stop=40.60, qty_stop=100):
    p = Position(account_id=1, isin="X")
    p.apply_execution(Execution(1, "X", Side.BUY, 100, 42.4, T, TradeKind.CONFIRMED, broker_fill_id="f"))
    if stop:
        p.apply_stop(Stop(stop, StopOrderType.SEUIL, qty_stop, StopKind.INITIAL, T, BrokerStopStatus.ACTIVE))
    return p


def test_threshold_crossed_then_sell_market_same_incident():
    p = _pos()
    r0 = check_stop(p, 7, 41.0, T, None, False, 5)
    assert r0.event == StopEvent.NONE
    r1 = check_stop(p, 7, 40.50, T, None, False, 5)
    assert r1.event == StopEvent.THRESHOLD_CROSSED and r1.incident_id == "stop_crossed:7"
    r2 = check_stop(p, 7, 40.30, T + timedelta(minutes=3), T, False, 5)
    assert r2.event == StopEvent.THRESHOLD_CROSSED
    r3 = check_stop(p, 7, 40.30, T + timedelta(minutes=5), T, False, 5)
    assert r3.event == StopEvent.SELL_MARKET_PROPOSED and r3.incident_id == r1.incident_id
    r4 = check_stop(p, 7, 40.30, T + timedelta(minutes=6), T, True, 5)
    assert r4.event == StopEvent.STOP_EXECUTED


def test_no_sell_proposal_outside_session():
    p = _pos()
    r = check_stop(p, 7, 40.30, T + timedelta(minutes=30), T, False, 5, session_open=False)
    assert r.event == StopEvent.THRESHOLD_CROSSED


def test_unprotected_quantity_is_p1_in_all_modes():
    p = _pos(stop=None)
    u = check_unprotected(p, 7)
    assert u.incident_id == "unprotected:7" and u.qty_unprotected == 100
    assert all(allowed_in_mode("P1", m, True) for m in ("disponible", "reunion", "absent"))
    assert not allowed_in_mode("P2", "reunion", True) and allowed_in_mode("P2", "reunion", False)
    assert not allowed_in_mode("P2", "absent", False) and allowed_in_mode("P2", "disponible", True)
    assert check_unprotected(_pos(), 7).incident_id is None


def test_p1_grouping_by_incident_with_repeat():
    assert decide_p1(None, 0, T, 15).send
    d = decide_p1(T, 0, T + timedelta(minutes=10), 15)
    assert not d.send
    d = decide_p1(T, 0, T + timedelta(minutes=15), 15)
    assert d.send and d.repeat_count == 1


def test_portfolio_state_freshness():
    assert is_state_fresh(T - timedelta(minutes=9), T, 10)
    assert not is_state_fresh(T - timedelta(minutes=11), T, 10)
    assert not is_state_fresh(None, T, 10)
