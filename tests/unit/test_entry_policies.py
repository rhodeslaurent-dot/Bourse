"""docs/34 §34.10 — entry policies on hand-built bars: orders, fills, exits, statistics (pure)."""

import math
import random

import pytest

from app.domain import entry_policies as ep
from app.domain.indicators import Bar

PP = ep.PolicyParams(slippage_pct=0.0, min_reward_risk=2.0, expiry_sessions=3)


def B(o, h, lo, c, v=1000):  # noqa: N802
    return Bar(o, h, lo, c, v)


def SV(**kw):  # noqa: N802
    base = dict(close=100.0, high=101.0, atr=2.0, mm20=99.0, high_52w=None, trigger=101.2, p_max_technical=101.8,
                stop=95.0, targets=(110.0, 120.0), fee_fraction=0.0)  # fmt: skip
    base.update(kw)
    return ep.SetupView(**base)


def test_p0_limit_at_the_maximum_price_and_skipped_under_the_stop():
    o = ep.plan("P0", SV(), PP)
    # P_max = (110 + 2 × 95) / 3 = 100, under the detector's cap 101.8
    assert o.kind == ep.OrderKind.LIMIT and o.level == pytest.approx(100.0) and o.valid == 3 and o.target == 110.0
    assert ep.plan("P0", SV(p_max_technical=94.0), PP) == "limite_sous_stop"  # detector cap under the stop
    assert ep.plan("P0", SV(targets=()), PP) == "sans_objectif"


def test_limit_touched_fills_at_min_open_limit_and_never_claims_the_fill_session_high():
    o = ep.plan("P0", SV(), PP)  # limit 100
    # session 1: opens above, low touches the limit exactly → fill at 100; its high (111) may precede the fill
    bars = [B(101, 111, 100.0, 104), B(104, 105, 103, 104)] + [B(104, 105, 103, 104)] * 30
    out = ep.simulate(o, bars, PP, hold=5)
    assert out.fill.price == 100.0 and out.fill.index == 0 and not out.fill.at_open
    assert out.exit.reason == "time"  # the target of the fill session is not counted
    # a gap under the limit fills at the open, and that session's high then counts
    out2 = ep.simulate(o, [B(99, 111, 98, 104)] + bars, PP, hold=5)
    assert out2.fill.price == 99 and out2.fill.at_open and out2.exit.reason == "target" and out2.exit.index == 0


def test_stop_and_target_in_one_session_the_stop_comes_first():
    o = ep.plan("P1", SV(), PP)
    bars = [B(100.5, 100.8, 100.2, 100.6), B(100.6, 111, 94, 100)]
    out = ep.simulate(o, bars, ep.PolicyParams(0.01, 2.0, 3), hold=10)
    assert out.exit.reason == "stop" and out.exit.index == 1
    assert out.exit.gross_return == pytest.approx(95 * 0.99 / 100.5 - 1)


def test_next_open_within_the_cap_fills_above_it_does_not_and_under_the_stop_is_invalid():
    o = ep.plan("P1", SV(), PP)  # cap = close + 1 ATR = 102
    assert ep.simulate(o, [B(101.5, 103, 101, 102)] * 3, PP, 2).fill.price == 101.5
    out = ep.simulate(o, [B(102.5, 104, 102, 103)] * 3, PP, 2)
    assert not out.filled and out.no_fill == "ecart_au_dessus"
    assert ep.simulate(o, [B(94.0, 96, 93, 95)] * 3, PP, 2).no_fill == "invalide"
    assert ep.simulate(o, [B(101.5, 103, 101, 102, 0)] * 3, PP, 2).no_fill == "sans_volume"


def test_buy_stop_gap_above_the_maximum_price_never_fills():
    o = ep.plan("P2", SV(), PP)  # level = max(101.2, 101) + 0.01; max price = level × (1 + 0.5 × 2 / 100)
    assert o.level == pytest.approx(101.21) and o.max_price == pytest.approx(101.21 * 1.01)
    gap = ep.simulate(o, [B(103.0, 104, 102.5, 103.5)] + [B(101, 102, 100.5, 101)] * 5, PP, 5)
    assert not gap.filled and gap.no_fill == "ecart_au_dessus"
    rising = ep.simulate(o, [B(100.5, 110.5, 100.2, 110)] + [B(110, 110.5, 109, 110)] * 5, PP, 5)
    assert rising.fill.price == pytest.approx(101.21) and rising.fill.rising
    assert rising.exit.reason == "target" and rising.exit.index == 0  # the high comes after a buy-stop fill
    at_open = ep.simulate(o, [B(101.5, 102, 101, 101.8)] * 6, PP, 5)
    assert at_open.fill.price == 101.5 and at_open.fill.at_open


def test_p2_prime_is_the_j1_stop_limit_of_docs_06():
    sv = SV(
        pivot=100.5, targets=(112.0,), stop=97.0, p_max_technical=103.0
    )  # P_max = (112 + 194) / 3 = 102 > threshold 100.5 × 1.003
    o = ep.plan("P2'", sv, PP)
    assert (
        o.kind == ep.OrderKind.BUY_STOP
        and o.level == pytest.approx(100.5 * 1.003)
        and o.max_price == pytest.approx(102)
    )
    assert o.valid == 1
    assert ep.simulate(o, [B(100, 100.6, 99.8, 100.2), B(100, 110, 99.9, 105)], PP, 5).no_fill == "jamais_atteint"
    assert ep.plan("P2'", SV(pivot=None, trigger=101.0), PP) == "limite_sous_seuil"  # P_max 100 under 101.3


def test_p3_sells_half_at_target_1_and_aims_at_the_far_target():
    o = ep.plan("P3", SV(targets=(110.0,), high_52w=130.0, p_max_technical=101.0), PP)
    assert o.target == 130.0 and o.partial_target == 110.0 and o.level == pytest.approx(101.0)
    bars = [B(100.5, 101, 100, 100.8), B(101, 111, 100.5, 110), B(110, 112, 94, 100)]
    out = ep.simulate(o, bars, PP, hold=10)
    assert out.exit.reason == "stop" and out.exit.partial
    assert out.exit.gross_return == pytest.approx(0.5 * (110 / 100.5 - 1) + 0.5 * (95 / 100.5 - 1))


def test_p4_pullback_to_the_mm20_and_p5_frames():
    assert ep.plan("P4", SV(mm20=99.5), PP).level == 99.5  # between close − 0.5 ATR (99) and the close
    assert ep.plan("P4", SV(mm20=97.0), PP).level == 99.0
    p5 = ep.plan("P5", SV(), PP)
    out = ep.simulate(p5, [B(101, 101.5, 100.5, 101)] * 25, PP, 20)
    assert (out.stop, out.target) == (pytest.approx(97.0), 110.0)  # max(target 1, 101 + 6)
    p5b = ep.plan("P5'", SV(), PP)
    out = ep.simulate(p5b, [B(101, 140, 100.5, 101 + k) for k in range(30)], PP, hold=60)
    assert math.isinf(out.target) and out.exit.reason == "time" and out.exit.sessions == 10


def test_unknown_outcome_is_not_counted_unless_a_running_proposal_is_asked():
    o = ep.plan("P1", SV(), PP)
    bars = [B(101, 102, 100.5, 101.5)] * 3
    assert ep.simulate(o, bars, PP, hold=10).exit is None
    run = ep.simulate(o, bars, PP, hold=10, open_ok=True).exit
    assert run.reason == "en_cours" and not run.closed and run.sessions == 3


def test_counterfactual_from_the_setup_close_counts_the_first_gap():
    o = ep.plan("P0", SV(), PP)
    cf = ep.counterfactual(o, 100.0, [B(94, 95, 93, 94)], PP, 5)
    assert cf.reason == "gap_stop" and cf.gross_return == pytest.approx(-0.06)


def test_weekly_statistics_newey_west_bootstrap_folds_and_masking():
    rng = random.Random(1)
    weeks = {w: [rng.gauss(0.1, 1.0) * 3, 3.0] for w in range(200)}
    st = ep.weekly_stats(weeks, lag=4, seed=7, min_n=20)
    assert st["n"] == 600 and st["t_nw"] is not None and len(st["plis"]) == 5
    lo, hi = st["ic95"]
    assert lo < st["moyenne"] < hi
    assert ep.weekly_stats(weeks, lag=4, seed=7, min_n=20) == st  # fixed seed: reproducible
    assert ep.weekly_stats({1: [1.0, 5.0]}, 4, 7, 20) == {"n": 5, "masque": True}
    c = ep.CellStats()
    for _ in range(5):
        c.add_trade(ep.Exit(0, "time", 0.01, 0.05, False, 3), 0.2, None, 0.002, 1)
    assert c.as_dict()["r_net_cto"] is None and c.as_dict()["masque"]


def test_paired_delta_and_per_signal_r_in_a_cell():
    c = ep.CellStats()
    for w in range(30):
        c.add_signal(0.5, w)
        c.add_delta(0.2, w)
    d = c.as_dict(min_n=20)
    assert d["r_par_signal"]["moyenne"] == 0.5 and d["delta_vs_p0"]["moyenne"] == 0.2
    assert d["delta_vs_p0"]["plis_positifs"] == 5


def test_standing_orders_cap_risk_lines_and_one_order_per_value():
    spans = [ep.OrderSpan(v, 10, 12, None, None, None, t) for v, t in ((1, 0.1), (2, 0.2), (3, 0.3))]
    assert ep.standing_orders_replay(spans, 2, 6) == [True, True, False]
    pos = [ep.OrderSpan(1, 10, 10, 10, 30, 1.0, 0.1), ep.OrderSpan(2, 11, 11, None, None, None, 0.2),
           ep.OrderSpan(1, 12, 12, 12, 15, 1.0, 0.3)]  # fmt: skip
    assert ep.standing_orders_replay(pos, 4, 1) == [True, False, False]  # one risk line held by the position
    assert ep.standing_orders_replay(pos, 4, 6) == [True, True, False]  # value 1 still held
    p = ep.pace(pos, 4, 6, months=2.0, min_n=1)
    assert p["ordres_places"] == 2 and p["trades"] == 1 and p["ordres_par_mois"] == 1.0
