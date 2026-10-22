"""Position review (docs/06 S1–S6, docs/22 §22.5) on the framework's cases."""

from app.domain.exits import ExitParams, PositionFacts, review_position
from app.domain.stops import Zone

P = ExitParams(
    reduce_at_target1_pct=0.5,
    time_stop_sessions=40,
    rs_rank_exit=50,
    thesis_mm20_days=2,
    earnings_blackout_hours=48,
    earnings_policy="reduce_50",
    wick_buffer_pct=0.005,
)


def facts(**kw):
    base = dict(entry_price=15.62, stop=17.60, stop_initial=14.80, close=18.815, closes_below_mm20=0, rs_rank=85,
                targets=[21.0], sessions_held=20, detector="d4_pullback", obstacles=[19.32],
                supports=[Zone(17.33, 17.71)])  # fmt: skip
    base.update(kw)
    return PositionFacts(**base)


def test_vallourec_hold_and_ratchet():
    rv = review_position(facts(), P)
    assert rv.verdict == "CONSERVER" and rv.stop.stop == 17.60 and not rv.stop.changed
    rv = review_position(facts(close=19.40), P)
    assert round(rv.stop.stop, 2) == 19.22 and rv.r_multiple > 1 and any("renforcement possible" in n for n in rv.notes)


def test_stop_crossed_thesis_and_rs():
    assert review_position(facts(close=17.50), P).verdict == "VENDRE"
    assert review_position(facts(closes_below_mm20=2), P).signals[0].startswith("S4")
    assert review_position(facts(rs_rank=40), P).verdict == "VENDRE"


def test_target_reached_reduce_and_breakeven():
    rv = review_position(facts(stop=15.0, close=21.2, supports=[], obstacles=[]), P)
    assert rv.verdict == "ALLÉGER" and rv.reduce_pct == 0.5 and rv.stop.stop == 15.62


def test_earnings_and_macro_event_before_the_event():
    rv = review_position(facts(close=16.0, stop=15.0, earnings_in_hours=30), P)
    assert rv.verdict == "ALLÉGER" and rv.signals[0].startswith("S6")
    seb = review_position(facts(entry_price=55.0, stop=56.81, stop_initial=53.0, close=57.05, supports=[],
                                obstacles=[], macro_event_in_hours=20, targets=[60.0]), P)  # fmt: skip
    assert seb.verdict == "VENDRE" and "avant l'événement" in seb.signals[0]


def test_time_stop_and_losing_line_never_reinforced():
    rv = review_position(facts(close=15.70, stop=15.0, sessions_held=45, supports=[]), P)
    assert rv.verdict == "VENDRE" and rv.signals[-1].startswith("S5")
    loser = review_position(facts(close=15.0, stop=14.5, supports=[]), P)
    assert "jamais renforcée" in loser.notes[-1]
