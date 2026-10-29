"""docs/20 §20.11 — pure helpers of the letters study: placement on the reaction session, forward returns, each
entry and exit on hand-built bars (stop first when both are touched), transposition, controls drawn with a seed,
levels under the closed list, aggregation and masking."""

from __future__ import annotations

import random
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from app.domain import letters_study as ls
from app.domain.indicators import Bar

PARIS = ZoneInfo("Europe/Paris")
SESSIONS = [date(2026, 10, 22), date(2026, 10, 23), date(2026, 10, 26), date(2026, 10, 27)]
OPEN, CLOSE = time(9, 0), time(17, 30)


def _at(d: date, hh: int, mm: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, hh, mm, tzinfo=PARIS)


def test_reaction_session_before_during_after_the_open_and_on_a_closed_day():
    f = ls.reaction_sessions
    assert f(_at(SESSIONS[1], 7, 30), OPEN, CLOSE, SESSIONS) == (1, 1)  # before the open: that session
    assert f(_at(SESSIONS[1], 9, 0), OPEN, CLOSE, SESSIONS) == (2, 1)  # at the open: too late for it
    assert f(_at(SESSIONS[1], 11, 0), OPEN, CLOSE, SESSIONS) == (2, 1)  # during: next open, that day's close
    assert f(_at(SESSIONS[1], 19, 0), OPEN, CLOSE, SESSIONS) == (2, 2)  # after the close: next session
    assert f(_at(date(2026, 10, 24), 10, 0), OPEN, CLOSE, SESSIONS) == (2, 2)  # Saturday → Monday
    assert f(_at(SESSIONS[3], 12, 0), OPEN, CLOSE, SESSIONS) is None  # the history ends first


def _b(o: float, h: float, lo: float, c: float) -> Bar:
    return Bar(o, h, lo, c, 1000)


def test_forward_returns_from_the_open_and_from_the_close():
    bars = [_b(10, 10.5, 9.5, 10), _b(11, 12, 10.5, 11.5), _b(11.5, 12.5, 11, 12), _b(12, 13, 11.5, 12.6)]
    assert abs(ls.forward_from_open(bars, 1, 1) - (11.5 / 11 - 1)) < 1e-12
    assert abs(ls.forward_from_open(bars, 1, 3) - (12.6 / 11 - 1)) < 1e-12
    assert ls.forward_from_open(bars, 1, 4) is None  # not enough sessions: unknown, never estimated
    assert abs(ls.forward_from_close(bars, 0, 2) - (12 / 10 - 1)) < 1e-12
    mfe, mae = ls.excursions_atr(bars, 1, 3, 0.5)
    assert abs(mfe - (13 - 11) / 0.5) < 1e-12 and abs(mae - (10.5 - 11) / 0.5) < 1e-12
    assert abs(ls.pre_move(bars, 3, False, 2) - (12 / 10 - 1)) < 1e-12
    assert abs(ls.pre_move(bars, 3, True, 2) - (12 / 10 - 1)) < 1e-12  # open of the session in progress


SLIP = 0.01


def test_e1_next_open_with_the_letters_stop_and_target():
    bars = [_b(100, 103, 99, 102), _b(102, 111, 101, 110)]
    t = ls.simulate(bars, ls.EntryRule("open"), ls.ExitRule(stop=95, target=110, max_hold=20), SLIP)
    assert isinstance(t, ls.Trade) and t.entry == 100 and t.reason == "target" and t.exit == 110
    assert abs(t.risk_fraction - (100 - 95 * 0.99) / 100) < 1e-12
    # an open at or below the letter's stop: no entry
    assert (
        ls.simulate([_b(94, 96, 93, 95)], ls.EntryRule("open"), ls.ExitRule(stop=95, target=110), SLIP)
        == ls.INVALIDATED
    )
    # the target already passed at the open: no trade
    assert (
        ls.simulate([_b(111, 112, 109, 110)], ls.EntryRule("open"), ls.ExitRule(stop=95, target=110), SLIP)
        == ls.INCOHERENT
    )


def test_stop_and_target_in_the_same_session_count_as_the_stop():
    bars = [_b(100, 101, 99, 100), _b(100, 112, 94, 100)]
    t = ls.simulate(bars, ls.EntryRule("open"), ls.ExitRule(stop=95, target=110), SLIP)
    assert isinstance(t, ls.Trade) and t.reason == "stop" and t.exit == 95 * 0.99 and t.exit_index == 1
    gap = ls.simulate(
        [_b(100, 101, 99, 100), _b(90, 92, 89, 91)], ls.EntryRule("open"), ls.ExitRule(stop=95, target=110), SLIP
    )
    assert isinstance(gap, ls.Trade) and gap.reason == "gap_stop" and gap.exit == 90 * 0.99


def test_e2_buy_stop_above_the_trigger_with_its_cap():
    rule = ls.EntryRule("buy_stop", level=50, cap=51, valid=3)
    ex = ls.ExitRule(stop=46, target=60)
    # a session crossing the trigger: filled at trigger × (1 + slippage), only the stop counts that session
    f = ls.fill([_b(49, 52, 48.5, 51)], rule, 46, SLIP)
    assert f == ls.Fill(0, 50 * 1.01, False)
    t = ls.simulate([_b(49, 61, 48.5, 51), _b(51, 52, 50, 51)] + [_b(51, 52, 50, 51)] * 20, rule, ex, SLIP)
    assert isinstance(t, ls.Trade) and t.reason == "time" and t.mfe_r < 1  # the 61 of the fill session is not counted
    # an open above the trigger and under the cap: filled at the open
    assert ls.fill([_b(50.5, 53, 50, 52)], rule, 46, SLIP) == ls.Fill(0, 50.5, True)
    # an open above the cap: a limit at the cap, filled when the session trades through it
    assert ls.fill([_b(52, 53, 50.8, 52)], rule, 46, SLIP) == ls.Fill(0, 51, False)
    assert ls.fill([_b(53, 54, 52, 53), _b(52, 53, 51.5, 52), _b(51, 52, 50.5, 51)], rule, 46, SLIP) == ls.Fill(
        2, 51, True
    )
    # never reached in 3 sessions
    assert ls.fill([_b(48, 49, 47, 48)] * 3, rule, 46, SLIP) == ls.NOT_FILLED
    assert ls.fill([_b(48, 49, 47, 48)] * 2, rule, 46, SLIP) == ls.UNKNOWN  # the history ends before the expiry


def test_e3_limit_at_the_letters_buy_level():
    rule = ls.EntryRule("limit", level=48, valid=3)
    assert ls.fill([_b(47.5, 49, 47, 48)], rule, 45, SLIP) == ls.Fill(0, 47.5, True)  # open under the limit
    assert ls.fill([_b(49, 50, 48.2, 49), _b(49, 49.5, 47.9, 49)], rule, 45, SLIP) == ls.Fill(1, 48, False)
    assert ls.fill([_b(49, 50, 48, 49)] * 3, rule, 45, SLIP) == ls.NOT_FILLED  # a touch is not a fill
    assert ls.fill([_b(49, 50, 48.5, 49), _b(44, 45, 43, 44)], rule, 45, SLIP) == ls.INVALIDATED
    # filled during the session that also breaks the stop: stopped the same session (prudent order)
    t = ls.simulate([_b(49, 50, 44, 45)] + [_b(45, 46, 44, 45)] * 20, rule, ls.ExitRule(stop=45.5, target=55), SLIP)
    assert isinstance(t, ls.Trade) and t.reason == "stop" and t.entry_index == 0 and t.exit_index == 0


def test_x3_atr_exits_and_time_exit():
    ex = ls.ExitRule(stop_atr=2, target_atr=3, atr=1.0, max_hold=3)
    up = ls.simulate([_b(100, 101, 99.5, 100.5), _b(101, 103.5, 100, 103)], ls.EntryRule("open"), ex, SLIP)
    assert isinstance(up, ls.Trade) and up.reason == "target" and up.exit == 103
    flat = [_b(100, 100.5, 99.5, 100.2)] * 3
    t = ls.simulate(flat, ls.EntryRule("open"), ex, SLIP)
    assert isinstance(t, ls.Trade) and t.reason == "time" and t.exit_index == 2 and t.exit == 100.2
    assert ls.simulate(flat[:2], ls.EntryRule("open"), ex, SLIP) == ls.UNKNOWN
    assert abs(t.net_r(0.002) - (100.2 / 100 - 1 - 0.002) / t.risk_fraction) < 1e-12


def test_levels_under_the_closed_list_and_inside_the_price_band():
    lv, dropped = ls.letter_levels(
        {"entrée": 52, "invalidation": "47,5", "objectif 1": 60, "objectif_2": 66, "cible": 900, "humeur": 3}, 50.0
    )
    assert (lv.buy, lv.stop, lv.target, lv.trigger) == (52, 47.5, 60, None)
    assert lv.kind == "recalculables" and "humeur" in dropped
    lv2, _ = ls.letter_levels({"déclencheur": 55, "objectif 2": 64}, 50.0)
    assert lv2.trigger == 55 and lv2.target == 64 and lv2.kind == "partiels"
    assert ls.letter_levels(None, 50.0)[0].kind == "aucun"
    assert ls.transpose(55, 50, 2, 20, 0.5) == 21.25 and ls.transpose(None, 50, 2, 20, 0.5) is None


def test_controls_are_drawn_with_a_seed_among_neighbouring_volatilities():
    cands = [ls.Candidate(f"v{i:02d}", 0.01 + i * 0.001, 1e6) for i in range(60)]
    a, wa = ls.pick_controls(cands, 0.03, 1e6, 20, random.Random("7|x"))
    b, _ = ls.pick_controls(list(reversed(cands)), 0.03, 1e6, 20, random.Random("7|x"))
    c, _ = ls.pick_controls(cands, 0.03, 1e6, 20, random.Random("8|x"))
    assert a == b and a != c and not wa and len(set(a)) == 20
    assert all(1 / 1.5 <= (0.01 + int(k[1:]) * 0.001) / 0.03 <= 1.5 for k in a)
    few, widened = ls.pick_controls(cands[:10], 0.03, 1e6, 5, random.Random(1))
    assert widened and few == ["v09", "v08", "v07", "v06", "v05"]  # the nearest in volatility
    thin = [ls.Candidate("illiquide", 0.03, 10.0)] + cands
    assert "illiquide" not in ls.pick_controls(thin, 0.03, 1e6, 20, random.Random(1))[0]


def _row(gross: float, ctrl: list[float], net: float | None, ctrl_r: list[float], source: str = "momentum_capital"):
    return ls.RecRow(
        isin="X", day=date(2026, 10, 22), source=source, direction="buy", origin="extraction", levels="aucun",
        group="P0", year=2026, pre_up=False, novelty="nouvelle",
        fwd={"ouverture": {h: gross for h in (1, 3, 5, 10, 20)}, "cloture": {}},
        bench={"ouverture": {h: 0.0 for h in (1, 3, 5, 10, 20)}, "cloture": {}},
        ctrl_fwd={"ouverture": {h: ctrl for h in (1, 3, 5, 10, 20)}, "cloture": {}}, cost=0.002,
        arms={"E1_X3": ls.ArmOutcome(ls.EXECUTED if net is not None else ls.NOT_FILLED, net, None, ctrl_r, 3)},
    )  # fmt: skip


def test_aggregation_pairs_each_recommendation_with_its_controls_and_masks_small_samples():
    p = ls.StudyParams()
    rows = [_row(0.02 + i * 0.001, [0.0, 0.01], 0.5, [0.1, 0.2, 0.3]) for i in range(8)]
    rep = ls.summarize(rows, p)
    o20 = rep["rendements"]["ouverture"]["20"]
    assert rep["n"] == 8 and not rep["seuil_recommandations_atteint"]
    assert abs(o20["exces_controles"]["moyenne"] - (0.0235 - 0.005)) < 1e-4
    assert abs(o20["net_cto"]["moyenne"] - (0.0235 - 0.002)) < 1e-4
    assert o20["part_positive_nette"]["k"] == 8
    arm = rep["bras"]["E1_X3"]
    assert arm["executes"] == 8 and abs(arm["exces_apparie"]["moyenne"] - 0.3) < 1e-9
    assert arm["controles"]["executes"] == 24 and not arm["seuils_atteints"]
    assert rep["bras"]["E1_X1"]["applicables"] == 0
    small = ls.summarize(rows[:3], p)
    assert small["rendements"]["ouverture"]["5"]["brut"] == {"n": 3, "masque": "moins de 5 observations"}
    by = ls.breakdowns(rows + [_row(0.01, [], None, [], "zonebourse")], p)
    assert set(by["source"]) == {"momentum_capital", "zonebourse"} and by["source"]["zonebourse"]["n"] == 1
    assert ls.novelty([10], 25, 20) == "repetee" and ls.novelty([10], 31, 20) == "nouvelle"
