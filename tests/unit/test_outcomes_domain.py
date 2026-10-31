"""docs/18 §18.4 (A18.3) — simulated outcome of a proposal on daily bars."""

from datetime import date, timedelta

import pytest

from app.domain.outcomes import DayBar, OutcomeInput, evaluate

D0 = date(2026, 10, 1)  # creation day (Thursday)


def sessions(n: int) -> list[date]:
    out, d = [D0], D0
    while len(out) < n + 1:
        d += timedelta(days=1)
        if d.weekday() < 5:
            out.append(d)
    return out


def bars_from(rows: list[tuple[float, float, float, float]], start_idx: int = 1) -> dict[date, DayBar]:
    ss = sessions(len(rows) + start_idx)
    return {ss[start_idx + i]: DayBar(ss[start_idx + i], *r) for i, r in enumerate(rows)}


def inp(**kw) -> OutcomeInput:
    base = dict(
        created_day=D0,
        created_before_open=False,
        entry_price=100.0,
        stop=95.0,
        target=110.0,
        slippage_pct=0.0,
        validity_sessions=5,
        horizon_sessions=20,
    )
    base.update(kw)
    return OutcomeInput(**base)


def test_limit_entry_then_target():
    b = bars_from([(103, 104, 101, 103), (102, 102.5, 99.5, 101), (101, 106, 100.5, 105), (106, 111, 105, 110)])
    o = evaluate(inp(), sessions(4), b)
    assert o.status == "target" and o.final
    assert o.entry_day == sessions(4)[2] and o.entry_price == 100.0
    assert o.exit_price == 110.0 and o.r_gross == pytest.approx(2.0)
    assert o.mfe_r == pytest.approx(2.2) and o.mae_r == pytest.approx(-0.1)


def test_gap_down_fills_at_open_and_stop_first_on_mixed_bar():
    b = bars_from([(98, 99, 97, 98.5), (99, 111, 94, 100)])
    o = evaluate(inp(slippage_pct=0.01), sessions(2), b)
    assert o.entry_price == 98.0  # open below the limit
    assert o.status == "stopped" and o.exit_price == pytest.approx(95 * 0.99)  # stop first although target touched
    risk = 98 - 95 * 0.99
    assert o.r_gross == pytest.approx((95 * 0.99 - 98) / risk)


def test_stop_on_fill_bar_counts():
    b = bars_from([(101, 101, 94, 96)])
    o = evaluate(inp(), sessions(1), b)
    assert o.status == "stopped" and o.exit_day == sessions(1)[1]


def test_not_filled_after_validity_and_running_before():
    b = bars_from([(105, 106, 101, 105)] * 5)
    assert evaluate(inp(), sessions(5), b).status == "not_filled"
    assert evaluate(inp(), sessions(3), dict(list(b.items())[:3])).status == "running"


def test_horizon_exit_and_costs():
    rows = [(100, 101, 99.5, 100)] + [(100, 102, 99, 101)] * 20
    o = evaluate(inp(target=None, cost_per_share=0.5), sessions(21), bars_from(rows))
    assert o.status == "horizon" and o.exit_price == 101 and o.costs_counted
    assert o.r_gross == pytest.approx(0.2) and o.r_net == pytest.approx(0.1)


def test_creation_session_used_only_before_open():
    ss = sessions(1)
    b = {ss[0]: DayBar(ss[0], 100, 100, 90, 92), ss[1]: DayBar(ss[1], 103, 104, 102, 103)}
    assert evaluate(inp(), ss, b).entry_day is None  # issued in session: the creation day is ignored
    assert evaluate(inp(created_before_open=True), ss, b).status == "stopped"


def test_missing_session_is_unavailable_never_estimated():
    ss = sessions(3)
    b = {ss[1]: DayBar(ss[1], 103, 104, 102, 103), ss[3]: DayBar(ss[3], 100, 101, 90, 95)}
    o = evaluate(inp(), ss, b)
    assert o.status == "unavailable" and not o.final and "manquante" in o.note


def test_watch_without_entry_level_forward_returns():
    rows = [(50, 51, 49, 50)] + [(50, 53, 48, 52)] * 19
    o = evaluate(inp(entry_price=None, stop=None, target=None), sessions(20), bars_from(rows))
    assert o.ref_price == 50 and o.status == "horizon" and o.final
    assert o.ret_1d == pytest.approx(0.0) and o.ret_20d == pytest.approx(0.04) and o.r_gross is None
    assert o.mfe_pct == pytest.approx(0.06) and o.mae_pct == pytest.approx(-0.04)


def test_pending_without_session_after_creation():
    assert evaluate(inp(), [D0], {}).status == "pending"


def test_latest_close_not_loaded_yet_is_running_not_unavailable():
    ss = sessions(2)
    b = {ss[1]: DayBar(ss[1], 103, 104, 102, 103)}
    o = evaluate(inp(), ss, b)
    assert o.status == "running" and "pas encore chargée" in o.note
    assert evaluate(inp(), ss[:2], {}).status == "running"


def test_a_never_filled_order_keeps_its_ghost_until_the_horizon():
    """docs/33 (user decision of 27/10/2026): the order is not filled, the value is still followed from the first
    open after the proposal to the horizon close — stored, never shown; final only then."""
    rows = [(105, 106, 101, 105)] * 5 + [(106, 108, 105, 107)] * 15
    b = bars_from(rows)
    early = evaluate(inp(), sessions(5), dict(list(b.items())[:5]))
    assert early.status == "not_filled" and not early.final and early.ghost_ret_pct is None
    assert early.ghost_ref_price == 105 and early.ghost_gap_pct == pytest.approx(0.05)  # open 5 % above the limit
    done = evaluate(inp(), sessions(20), b)
    assert done.status == "not_filled" and done.final
    assert done.ghost_ret_pct == pytest.approx(107 / 105 - 1) and done.r_gross is None  # no trade, no R


def test_a_d15_card_is_entered_by_its_plage_and_exits_at_its_stop_or_by_time():
    """Review of 31/10/2026: a D15 card (d7 speculative, plan n° 4 P2) was measured as a limit exiting at target 1. It
    is now entered by the plage stored on its proposal (seuil, limit, validity, cancellation at the structural stop)
    and exits at its stop k × ATR under the fill or by time (fill session first), never at target 1 (rule 10: only
    what the proposal stored)."""
    from app.db.models import Proposal
    from app.domain.outcomes import PlageEntry
    from app.services.outcomes import d15_plage

    ez = {"type": "ordre à plage de déclenchement", "seuil": 101.0, "limite": 102.0, "validite_seances": 3,
          "politique": "P2 (plan n° 4)", "annulation_ouverture_au_stop_structurel": 95.0, "stop_atr": 2.5,
          "sortie_au_temps_seances": 3}  # fmt: skip
    pl = d15_plage(Proposal(entry_zone=ez, stop=96.0))  # stop of a fill at the seuil: 101 − 2.5 × ATR 2
    assert pl == PlageEntry(101.0, 5.0, 3, 95.0)
    assert d15_plage(Proposal(entry_zone={**ez, "politique": None}, stop=96.0)) is None  # a docs/06 plage: unchanged
    d15 = inp(entry_price=102.0, stop=96.0, target=104.0, validity_sessions=3, horizon_sessions=3, plage=pl)
    # J+1 under the seuil (a limit at 102 would fill there); J+2 crosses it, its high above target 1; time exit at J+4
    rows = [(100, 100.8, 99, 100.5), (100.6, 106, 100.2, 105), (105, 107, 103, 106), (106, 108, 104, 107)]
    o = evaluate(d15, sessions(4), bars_from(rows))
    assert evaluate(inp(entry_price=102.0, stop=96.0, target=104.0), sessions(4), bars_from(rows)).status == "target"
    assert (o.status, o.exit_reason, o.entry_day, o.entry_price) == ("horizon", "horizon", sessions(4)[2], 101.0)
    assert o.exit_day == sessions(4)[4] and o.exit_price == 107 and o.r_gross == pytest.approx(6 / 5)
    assert o.mfe_pct == pytest.approx(108 / 101 - 1)  # highs above target 1 (104) never exited the trade
    # an open inside the plage fills there; the stop sits k × ATR under that fill (96.5, not the stored 96)
    o = evaluate(d15, sessions(3), bars_from([(101.5, 103, 101.2, 102), (102, 102.5, 96.4, 97)]))
    assert o.entry_price == 101.5 and o.status == "stopped" and o.exit_price == pytest.approx(96.5)
    # an open above the limit, or at the structural stop, cancels the plage (never chased)
    o = evaluate(d15, sessions(3), bars_from([(102.5, 104, 102.1, 103)] * 3))
    assert o.status == "not_filled" and o.final and "au-dessus de sa limite" in o.note and o.entry_day is None
    o = evaluate(d15, sessions(1), bars_from([(94.0, 103, 93, 102)]))
    assert o.status == "not_filled" and "stop structurel" in o.note
