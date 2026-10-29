# ruff: noqa: E501
"""Registration n° 4 (docs/34 §34.11): entry policies paired per signal — buy stop-limit fills on hand-built bars,
the orders of each policy, the value per signal (no trade = 0), the paired statistics, the production setups
(d7x and the placebo geometry), letters with an explicit trigger, the frozen plan and its run end to end."""

from __future__ import annotations

import dataclasses
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pytest
import yaml

from app.domain.admission import p_max_rr
from app.domain.research import entry_policies as E
from app.domain.research import triggers as T
from app.domain.research.exits import (
    CANCELLED,
    CLOSED,
    INVALID,
    STOP,
    TARGET,
    UNFILLED,
    UNKNOWN,
    ExitSpec,
    Orders,
    simulate,
)
from app.domain.research.panel import LetterLevels
from app.domain.research.pipeline import Trades, prepare_group, signal_rows
from app.domain.research.prereg import Cell, PreregError, check_run_start, file_sha256, parse_prereg
from app.domain.research.registration import run_registration
from app.domain.research.report import assert_no_price_fields, build_report, render_fr
from tests.unit.test_research import _bars, bdays, settings, synthetic_panel

ROOT = Path(__file__).resolve().parents[2]
REG4 = ROOT / "research" / "prereg" / "2026-10-30_reg4.yaml"
REG4_SHA256 = "86ac714f71fd502b1debbaf24e750264ba8bebd6928330ed01920b0dc5784f36"  # registered on 30/10/2026 (docs/34 §34.11, docs/15): the file must never change
PROD = {"zones": {"lookback_sessions": 250, "pivot_window": 5, "merge_atr_mult": 0.5, "min_touches": 2},
        "wick_buffer_pct": 0.005, "stop_buffer_atr_mult": 0.25, "min_reward_risk": 2.0, "expiry_sessions": 3}  # fmt: skip


def _load(path: Path = REG4):
    b = path.read_bytes()
    return parse_prereg(yaml.safe_load(b), file_sha256(b))


@pytest.fixture(scope="module")
def reg4():
    return _load()


def _light(p):
    return dataclasses.replace(p, inference={**p.inference, "bootstrap_draws": 200, "power_reps": 5})


def _trending_panel(n_inst=40, n_days=1600, seed=3, start="2016-01-04"):
    extra = np.zeros((n_days, n_inst))
    extra[:, : n_inst // 2] = 0.0012  # half the values trend up: d7 sees leaders pulling back to their MM50
    return synthetic_panel(n_inst=n_inst, n_days=n_days, seed=seed, lr_extra=extra, start=start)


@pytest.fixture(scope="module")
def group():
    return prepare_group(0, _trending_panel(), settings())


# --- 1. the engine: buy stop-limit fills, the fill session, the frame at the fill ----------------------------------


def _stop_limit(level, max_price, stop, target=np.nan, cancel=np.nan, validity=3):
    lv = np.asarray(level, dtype=np.float64)
    n = lv.size
    return Orders(np.arange(n), np.zeros(n, dtype=np.int64), np.broadcast_to(np.asarray(max_price, dtype=np.float64), (n,)).copy(),
                  np.broadcast_to(np.asarray(cancel, dtype=np.float64), (n,)).copy(), np.zeros(n), -np.broadcast_to(np.asarray(stop, dtype=np.float64), (n,)).copy(),
                  np.zeros(n), np.broadcast_to(np.asarray(target, dtype=np.float64), (n,)).copy(), np.full(n, np.nan), validity, lv)  # fmt: skip


def test_buy_stop_limit_fills_at_the_open_in_its_range_at_the_level_on_the_way_up_and_never_above_its_maximum():
    # columns: 0 open inside [level, max] · 1 open under the level, high reaches it · 2 open above the maximum
    # (cancelled, even if the price comes back) · 3 open at the invalidation level · 4 never reaches the level
    o = [
        [100, 100, 100, 100, 100],
        [101.0, 99.0, 103.0, 95.0, 99.0],
        [101, 101, 101, 101, 99.0],
        [101, 101, 101.5, 101, 99.0],
    ]
    h = [[100.5] * 5, [101.5, 100.5, 103.5, 99.0, 99.5], [102, 102, 101.8, 102, 99.5], [102, 102, 101.8, 102, 99.6]]
    lo = [[99.5] * 5, [100.5, 98.8, 101.2, 94.0, 98.5], [100, 100, 100, 100, 98.5], [100, 100, 100, 100, 98.6]]
    c = [[100] * 5, [101.2, 100.2, 101.5, 98.0, 99.2], [101, 101, 101, 101, 99.0], [101, 101, 101, 101, 99.0]]
    b = _bars(o, h, lo, c)
    od = _stop_limit([100.0] * 5, 102.0, 90.0, cancel=96.0)
    res = simulate(b, od, ExitSpec("X", 2), 0.002)
    assert res.fill_pos[0] == 1 and res.entry[0] == 101.0 and res.at_open[0] and not res.rising[0]
    assert res.fill_pos[1] == 1 and res.entry[1] == 100.0 and not res.at_open[1] and res.rising[1]
    assert res.status[2] == CANCELLED and res.fill_pos[2] == -1  # gap above the maximum price: not chased
    assert res.status[3] == CANCELLED  # opened at the invalidation level before any fill
    assert res.status[4] == UNFILLED and res.fill_pos[4] == -1
    # a NaN level is a plain limit order (P0 conventions unchanged): fills at the open when it is under the limit
    plain = dataclasses.replace(od, stop_level=np.full(5, np.nan), limit=np.full(5, 101.6))
    r2 = simulate(b, plain, ExitSpec("X", 2), 0.002)
    assert r2.entry[0] == 101.0 and r2.at_open[0] and r2.entry[1] == 99.0 and not r2.rising.any()


def test_a_stop_limit_filled_on_the_way_up_claims_its_session_high_but_the_stop_still_comes_first():
    o = [[100, 100, 100], [99.0, 99.0, 100.0], [100, 100, 100]]
    h = [[100, 100, 100], [100.9, 100.9, 101.0], [101, 101, 101]]
    lo = [[100, 100, 100], [98.9, 94.0, 98.5], [99, 99, 99]]
    c = [[100, 100, 100], [100.5, 100.5, 100.2], [100, 100, 100]]
    b = _bars(o, h, lo, c)
    # 0: rising fill at 100, target 100.8 inside the same session → target · 1: the same with the stop (95) also
    # touched in the session → stop first under every path rule
    od = _stop_limit([100.0, 100.0], 101.0, 95.0, target=100.8)
    for rule in ("stop_first", "ohlc", "target_first"):
        res = simulate(
            _bars(np.array(o)[:, :2], np.array(h)[:, :2], np.array(lo)[:, :2], np.array(c)[:, :2]),
            od,
            ExitSpec("X", 5),
            0.002,
            rule,
        )
        assert res.reason[0] == TARGET and res.exit_px[0] == 100.8 and res.exit_pos[0] == 1
        assert res.reason[1] == STOP and res.exit_pos[1] == 1
    # a limit filled during its session (open 100 above the limit 99) never claims that session's high
    lim = Orders(np.array([2]), np.zeros(1, dtype=np.int64), np.array([99.0]), np.array([np.nan]), np.zeros(1), np.array([-95.0]),
                 np.zeros(1), np.array([100.8]), np.array([np.nan]), 1)  # fmt: skip
    res = simulate(b, lim, ExitSpec("X", 5), 0.002)
    assert res.entry[0] == 99.0 and res.reason[0] == TARGET and res.exit_pos[0] == 2  # not on the fill session


def test_a_fill_outside_its_stop_target_frame_is_no_trade_under_the_production_exit():
    o = [[100, 100], [101.5, 100.2], [101, 101]]
    h = [[100, 100], [101.8, 100.6], [102, 102]]
    lo = [[100, 100], [101.2, 99.9], [100, 100]]
    c = [[100, 100], [101.6, 100.4], [101, 101]]
    od = _stop_limit([100.0, 100.0], 102.0, 95.0, target=101.0)
    res = simulate(_bars(o, h, lo, c), od, ExitSpec("XP", 5, bracket_at_fill=True), 0.002)
    assert res.status[0] == INVALID and res.fill_pos[0] == -1  # filled at 101.5, above the first target
    assert res.status[1] in (CLOSED, UNKNOWN) and res.entry[1] == 100.2


# --- 2. the orders of each policy ---------------------------------------------------------------------------------


def test_tick_and_the_vectorised_maximum_price_match_the_production_formulas():
    assert E.tick_size(np.array([50.0, 5.0, 500.0, 150.0])).tolist() == pytest.approx([0.01, 0.001, 0.1, 0.01])
    t, s, f = np.array([120.0, 80.0]), np.array([90.0, 60.0]), np.array([0.004, 0.0])
    assert E.p_max_rr_vec(t, s, f, 2.0).tolist() == pytest.approx(
        [p_max_rr(120, 90, 0.004, 2.0), p_max_rr(80, 60, 0.0, 2.0)]
    )


def _table(g, rows):
    """A setup table on chosen (t, col) with chosen levels: (t, col, trigger, p_max, stop, t1, t_far)."""
    arr = np.array([r[2:] + (0.004,) for r in rows], dtype=np.float64)
    return E.SetupTable(np.array([r[0] for r in rows], dtype=np.int64), np.array([r[1] for r in rows], dtype=np.int64),
                        *(arr[:, k] for k in range(6)))  # fmt: skip


def test_policy_orders_follow_their_definitions(group, reg4):
    g, ep = group, E.entry_params(reg4, PROD)
    t, col = 700, 3
    close, high = float(g.p.close[t, col]), float(g.p.high[t, col])
    atr, mm20 = float(g.f.atr14[t, col]), float(g.f.sma20[t, col])
    su = _table(
        g,
        [
            (t, col, high * 1.002, high * 1.008, close * 0.95, close * 1.10, close * 1.25),
            (t, col, high * 1.002, high * 1.008, close * 0.95, high * 1.0005, close * 1.25),  # trigger above target 1
            (t, col, high * 1.002, high * 1.008, close * 0.999, close * 1.001, close * 1.002),
        ],
    )  # R4 limit under the stop
    c1 = next(c for c in reg4.cells if c.id == "C1")
    p0 = E.policy_orders("P0", g, su, c1, ep)
    want = min(p_max_rr(close * 1.10, close * 0.95, 0.004, 2.0), high * 1.008)
    assert p0.limit[0] == pytest.approx(want) and p0.validity == 3 and p0.stop_level is None
    assert np.isnan(p0.limit[2]) and p0.cancel_below[0] == pytest.approx(close * 0.95)  # no order; R1 invalidation
    p3 = E.policy_orders("P3", g, su, c1, ep)
    assert p3.limit[0] == pytest.approx(min(p_max_rr(close * 1.25, close * 0.95, 0.004, 2.0), high * 1.008))
    p2 = E.policy_orders("P2", g, su, c1, ep)
    base = max(high * 1.002, high)
    lv = base + float(E.tick_size(np.array([base]))[0])
    assert p2.stop_level[0] == pytest.approx(lv) and p2.limit[0] == pytest.approx(lv * (1 + 0.5 * atr / close))
    assert np.isnan(p2.stop_level[1]) and np.isnan(p2.limit[1])  # a trigger at or above the first target: no order
    x5 = next(c for c in reg4.cells if c.id == "X5.d7x.validity5.confirm_cap_atr1")
    p2b = E.policy_orders("P2", g, su, x5, ep)
    assert p2b.validity == 5 and p2b.limit[0] == pytest.approx(lv * (1 + 1.0 * atr / close))
    p1 = E.policy_orders("P1", g, su, c1, ep)
    assert p1.limit[0] == pytest.approx(close + atr) and p1.validity == 1
    p4 = E.policy_orders("P4", g, su, c1, ep)
    lim4 = close - 0.5 * atr
    assert p4.limit[0] == pytest.approx(mm20 if lim4 < mm20 < close else lim4)
    # exits: X8 = 2.5 ATR under the fill, no target; XP = absolute R1 stop and target 1
    assert p0.stop_mult[0] == 1.0 and p0.stop_off[0] == pytest.approx(2.5 * atr) and np.isnan(p0.tgt_off[0])
    c2 = next(c for c in reg4.cells if c.id == "C2")
    xp = E.policy_orders("P2", g, su, c2, ep)
    assert (
        xp.stop_mult[0] == 0
        and xp.stop_off[0] == pytest.approx(-close * 0.95)
        and xp.tgt_off[0] == pytest.approx(close * 1.10)
    )
    assert E.exit_of(c2).spec.bracket_at_fill and E.exit_of(c1).spec.max_hold == 20


def test_the_value_per_signal_is_the_net_r_zero_without_a_trade_and_unknown_drops_the_pair(group, reg4):
    g, ep = group, E.entry_params(reg4, PROD)
    c1 = next(c for c in reg4.cells if c.id == "C1")
    ts = np.arange(400, 1400, 9, dtype=np.int64)
    cols = (np.arange(ts.size) % 40).astype(np.int64)
    close = g.p.close[ts, cols]
    su = E.SetupTable(ts, cols, g.p.high[ts, cols] * 1.002, g.p.high[ts, cols] * 1.008, close * 0.94, close * 1.12,
                      close * 1.3, np.full(ts.size, 0.004))  # fmt: skip
    for pol in ("P0", "P2"):
        tr = E.arm(g, c1, su, pol, settings(), ep)
        st_ = tr["status"]
        assert np.allclose(tr["value"][tr["closed"]], tr["r"][tr["closed"]])
        assert (tr["value"][np.isin(st_, (UNFILLED, CANCELLED, INVALID))] == 0).all()
        assert np.isnan(tr["value"][st_ == UNKNOWN]).all()
        assert (tr["filled"] == np.isin(st_, (CLOSED, 3, 4, 6))).all() and (tr["delay"][tr["filled"]] >= 1).all()


# --- 3. paired statistics ------------------------------------------------------------------------------------------


def _pair(delta, n=3000, seed=0):
    rng = np.random.default_rng(seed)
    base = rng.normal(0, 1, n)
    day = (
        np.sort(rng.integers(date(2017, 1, 2).toordinal(), date(2026, 7, 31).toordinal(), n))
        - date(1970, 1, 1).toordinal()
    )
    common = {"week": (day + 3) // 7, "day": day, "hold": np.full(n, 20), "closed": np.ones(n, dtype=bool)}
    b = Trades({**common, "value": base})
    a = Trades({**common, "value": base + delta + rng.normal(0, 0.3, n)})
    return a, b


def test_paired_statistics_find_a_planted_difference_and_not_a_null_one(reg4):
    c1 = next(c for c in reg4.cells if c.id == "C1")
    a, b = _pair(0.2)
    s = E.paired(a, b, c1, _light(reg4), 7)
    assert s["n"] == 3000 and s["delta_moyen"] == pytest.approx(0.2, abs=0.03) and s["t_newey_west"] > 6
    assert s["ic"][0] > 0.1 and s["plis_positifs"] == 5 and s["inference"]["bloc_bootstrap_semaines"] == 4
    a0, b0 = _pair(0.0, seed=1)
    s0 = E.paired(a0, b0, c1, _light(reg4), 7)
    assert abs(s0["t_newey_west"]) < 3 and s0["ic"][0] < 0 < s0["ic"][1]
    b0.cols["value"][:100] = np.nan  # an unknown outcome on either side drops the pair
    assert E.paired(a0, b0, c1, _light(reg4), 7)["n"] == 2900
    crit = E.entry_criteria(
        {**s, "bras": {"politique": {"taux_execution": 0.7}}, "p_holm": 0.001}, reg4.thresholds, True
    )
    assert all(crit.values())
    assert not E.entry_criteria(
        {**s, "bras": {"politique": {"taux_execution": 0.4}}, "p_holm": 0.001}, reg4.thresholds, True
    )["execution"]
    assert not E.entry_criteria(
        {**s, "bras": {"politique": {"taux_execution": 0.7}}, "p_romano_wolf": 0.2}, reg4.thresholds, False
    )["tests_multiples"]
    pw = E.power(0.02, 3.0, [0.10, 0.20])
    assert pw["+0.10"] == pytest.approx(0.977, abs=0.01) and pw["+0.20"] == 1.0


def test_unseal_verdict_of_a_paired_candidate(reg4):
    th = reg4.thresholds
    ok = {
        "n": 400,
        "delta_moyen": 0.04,
        "ic": [-0.02, 0.1],
        "bras": {
            "politique": {
                "tous": {"mean_r": 0.2, "mean_eur_300": 50.0, "profit_factor": 1.4, "mean_pct_notionnel": 0.01}
            }
        },
    }
    v = E.entry_unseal_verdict(ok, {"mean_excess": 0.08, "decision_b": {"applicable": True, "toutes": True}}, th)
    assert v["verdict_a"] and v["verdict_b"] and "(b)" in v["verdict"]
    assert not E.entry_unseal_verdict({**ok, "ic": [-0.06, 0.1]}, {"mean_excess": 0.08}, th)["verdict_a"]
    assert not E.entry_unseal_verdict({**ok, "delta_moyen": -0.01}, {"mean_excess": 0.08}, th)["verdict_a"]
    v2 = E.entry_unseal_verdict(ok, {"mean_excess": 0.08, "decision_b": {"applicable": False, "toutes": False}}, th)
    assert v2["verdict_a"] and not v2["verdict_b"]


# --- 4. setups: the real detectors, the placebo geometry, point in time ------------------------------------------------


def test_d7x_setups_are_the_detector_levels_and_the_placebo_geometry_equals_them_on_d7_days(group, reg4):
    g, st = group, settings()
    rows = signal_rows(g.p.days, date(2017, 1, 2), date(2026, 7, 31), 65)
    gs = E.GroupSetups(g, st, PROD, E.entry_params(reg4, PROD), rows)
    c1 = next(c for c in reg4.cells if c.id == "C1")
    tab = gs.table(c1)
    assert tab.size >= 30
    base = T.d7_base(g.p, g.f, st.detectors["d7_trend_quality"])
    assert base[tab.ts, tab.cols].all()  # the detector only keeps days of its vectorised conditions
    assert (tab.t1 > tab.p_max).all() and (tab.stop < tab.trigger).all() and (tab.t_far >= tab.t1).all()
    geo = E.detector_setups(g, st, PROD, "d7_trend_quality", tab.ts, tab.cols, geometry=E.d7_geometry)
    assert geo.size == tab.size
    for k in ("trigger", "p_max", "stop", "t1", "t_far", "fee"):
        assert np.allclose(getattr(geo, k), getattr(tab, k))
    sig = gs.signals(c1)
    assert sig.size <= tab.size and len(set(zip(sig.ts.tolist(), sig.cols.tolist(), strict=True))) == sig.size
    for col in np.unique(sig.cols).tolist():  # lockout of h own sessions per value
        pos = np.sort(g.f.pk.pos[sig.ts[sig.cols == col], col])
        assert (np.diff(pos) >= 20).all()
    # the placebo: random days at d7x's rate in this group, the d7 geometry of the drawn day, seeded
    x1 = next(c for c in reg4.cells if c.id == "X1.t00.exitX8")
    pl = gs.table(x1)
    base_n = int(E._in_rows(E.orderable(g), rows).sum())
    assert gs.rates[x1.id] == pytest.approx(tab.size / base_n) and pl.size > 0
    again = E.GroupSetups(g, st, PROD, E.entry_params(reg4, PROD), rows).table(x1)
    assert np.array_equal(again.ts, pl.ts) and np.array_equal(again.cols, pl.cols)


def test_setups_never_read_a_bar_after_their_signal_day(reg4):
    p = _trending_panel(n_inst=30, n_days=1300, seed=8)
    cut = 900
    poisoned = dataclasses.replace(p, close=p.close.copy(), adj_close=p.adj_close.copy(), high=p.high.copy(),
                                   low=p.low.copy(), open=p.open.copy(), volume=p.volume.copy())  # fmt: skip
    for k in ("close", "adj_close", "high", "low", "open"):
        getattr(poisoned, k)[cut + 1 :] *= 1.7
    poisoned.volume[cut + 1 :] *= 9.0
    st, ep = settings(), E.entry_params(reg4, PROD)
    c1 = next(c for c in reg4.cells if c.id == "C1")
    out = []
    for q in (p, poisoned):
        g = prepare_group(0, q, st)
        out.append(E.GroupSetups(g, st, PROD, ep, (300, cut)).table(c1))
    a, b = out
    assert a.size > 0 and np.array_equal(a.ts, b.ts) and np.array_equal(a.cols, b.cols)
    for k in ("trigger", "p_max", "stop", "t1", "t_far"):
        assert np.allclose(getattr(a, k), getattr(b, k))


# --- 5. letters with an explicit trigger --------------------------------------------------------------------------------


def test_letter_levels_need_a_trigger_a_stop_under_it_and_a_target():
    assert E.letter_config({"Déclencheur": 100, "Objectif 1": 110, "objectif 2": 120, "invalidation": 90}) == (
        100.0,
        90.0,
        (110.0, 120.0),
    )
    assert E.letter_config({"cassure": 100, "cible": 112, "stop loss": 92, "commentaire": "x"}) == (
        100.0,
        92.0,
        (112.0,),
    )
    assert E.letter_config({"achat": 100, "objectif": 110, "stop": 90}) is None  # no trigger: not a breakout letter
    assert E.letter_config({"declencheur": 100, "objectif": 110, "stop": 101}) is None  # stop above the trigger
    assert E.letter_config({"declencheur": 100, "stop": 90}) is None


def test_a_letter_is_known_at_the_close_of_its_day_unless_it_arrives_after_the_close():
    assert E.letter_known_day(datetime(2024, 3, 5, 14, 4, tzinfo=UTC)) == date(2024, 3, 5)  # 15:04 Paris
    assert E.letter_known_day(datetime(2024, 3, 5, 16, 45, tzinfo=UTC)) == date(2024, 3, 6)  # 17:45 Paris
    assert E.letter_known_day(datetime(2024, 7, 5, 15, 45)) == date(2024, 7, 6)  # naive = UTC: 17:45 Paris (summer)


def test_letter_signals_sit_on_the_first_own_session_known_with_history_and_a_valid_stop(reg4):
    p = _trending_panel(n_inst=6, n_days=900, seed=4)
    row = {str(d): k for k, d in enumerate(p.days)}
    sat = np.datetime64("2018-03-03", "D")  # a Saturday → Monday 5 March
    t_mon = row["2018-03-05"]
    c = float(p.close[t_mon, 0])
    letters = (
        LetterLevels(p.isins[0], sat, c * 1.03, c * 0.92, (c * 1.12, c * 1.2)),
        LetterLevels(
            p.isins[0], np.datetime64("2018-03-04", "D"), c * 1.03, c * 0.9, (c * 1.15,)
        ),  # same session: one signal
        LetterLevels(p.isins[1], np.datetime64("2016-02-01", "D"), 50.0, 40.0, (60.0,)),  # < 60 sessions of history
        LetterLevels(p.isins[2], np.datetime64("2018-03-05", "D"), 1e6, 1e5, (2e6,)),  # close under the letter's stop
        LetterLevels("ZZ0000000000", sat, 1.0, 0.5, (2.0,)),  # a value outside the panel
    )
    p = dataclasses.replace(p, letters=letters)
    assert p.select(np.array([0, 2])).letters is not None and len(p.select(np.array([0, 2])).letters) == 3
    st, ep = settings(), E.entry_params(reg4, PROD)
    g = prepare_group(0, p, st)
    tab = E.letter_setups(g, st, ep, (0, p.shape[0] - 1), 60)
    assert tab.ts.tolist() == [t_mon] and tab.cols.tolist() == [0]
    assert tab.trigger[0] == pytest.approx(c * 1.03) and tab.p_max[0] == pytest.approx(c * 1.03 * 1.01)
    assert tab.stop[0] == pytest.approx(c * 0.92) and tab.t1[0] == pytest.approx(c * 1.12)


# --- 6. the frozen plan --------------------------------------------------------------------------------------------------


def test_plan_4_is_the_registered_file(reg4):
    """The plan is frozen: its sha256 is the one registered in docs/34 §34.11 and docs/15 (30/10/2026)."""
    assert file_sha256(REG4.read_bytes()) == REG4_SHA256 == reg4.sha256
    assert REG4_SHA256 in (ROOT / "docs" / "34-hausses-manquees.md").read_text(encoding="utf-8")


def test_plan_4_parses_with_its_cells_families_and_sealed_replication(reg4):
    assert reg4.id == "2026-10-30_reg4" and reg4.seed == 20261030 and reg4.is_entry_plan
    assert reg4.counts() == {"confirmatoires": 2, "exploratoires_etape0": 22, "etape1_plafond": 0,
                             "diagnostics_f0": 0, "etape2_plafond": 3, "total": 27}  # fmt: skip
    c1, c2 = reg4.confirmatory
    assert (c1.trigger, c1.entry, c1.baseline_entry, c1.exit, c1.h, c1.validity) == ("d7x", "P2", "P0", "X8", 20, 3)
    assert (c2.exit, c2.h) == ("XP", 60)
    diag = sorted({c.family for c in reg4.cells if c.diagnostic})
    assert diag == ["X1", "X6", "X7", "X8"]
    tested = [c for c in reg4.exploratory if not c.diagnostic]
    assert len(tested) == 11 and {c.family for c in tested} == {"X2", "X3", "X4", "X5"}
    assert not any(c.entry == "P5" or c.baseline_entry == "P5" for c in reg4.cells)
    assert reg4.replication == (date(2007, 1, 2), date(2015, 9, 30)) and reg4.needs_letters and reg4.needs_earnings
    with pytest.raises(PreregError):
        check_run_start(reg4, date(2015, 9, 30))
    check_run_start(reg4, date(2016, 1, 4))
    raw = yaml.safe_load(REG4.read_bytes())
    raw["thresholds"].pop("paired")
    with pytest.raises(PreregError, match="appariés"):
        parse_prereg(raw, "x")
    raw = yaml.safe_load(REG4.read_bytes())
    raw["confirmatory"][0].pop("baseline_entry")
    with pytest.raises(PreregError, match="référence"):
        parse_prereg(raw, "x")
    raw = yaml.safe_load(REG4.read_bytes())
    raw["exploratory"][7]["baseline_entry"] = "P0"  # t14 has no setup: P0 impossible
    with pytest.raises(PreregError, match="t14"):
        parse_prereg(raw, "x")


# --- 7. the plan end to end on synthetic data ------------------------------------------------------------------------------


def test_plan_4_runs_end_to_end_placebo_first_and_reports_aggregates_only(reg4):
    p = _trending_panel()
    t = 900
    c = float(p.close[t, 2])
    p = dataclasses.replace(p, letters=(LetterLevels(p.isins[2], p.days[t], c * 1.02, c * 0.9, (c * 1.15,)),))
    st = dataclasses.replace(settings(), min_earnings_coverage=0.5)
    res = run_registration([p], _light(reg4), st, (), PROD, "all", None, None, False)
    cells = res["cellules"]
    assert set(cells) == {c.id for c in reg4.cells}
    order = res["politiques_entree"]["ordre_de_lecture"]
    assert (
        order[:2] == ["X1.t00.exitX8", "X1.t00.exitXP"]
        and order[2:4] == ["C1", "C2"]
        and order[-2:] == ["X7.t14.h10", "X7.t14.h20"]
    )
    for cid in ("C1", "C2"):
        s = cells[cid]
        assert s["n"] > 0 and s["p_holm"] is not None and s["niveau"] in ("CANDIDAT", "REJET")
        assert s["exces_sur_placebo"]["placebo"] == ("X1.t00.exitX8" if cid == "C1" else "X1.t00.exitXP")
        assert set(s["bras"]) == {"politique", "reference"} and "decision_b" in s
    assert cells["C2"]["decision_b"]["applicable"] and not cells["C1"]["decision_b"]["applicable"]
    assert all(cells[c.id]["niveau"] == "DIAGNOSTIC" for c in reg4.cells if c.diagnostic)
    assert cells["X2.d7x.entryP1"].get("p_romano_wolf") is not None and "p_romano_wolf" not in cells["X1.t00.exitX8"]
    assert cells["X6.letters_trigger.entryP2"]["signaux"] == 1  # the one letter
    assert "selection_adverse" in cells["X8.d7x.entryP2"] and set(res["etape2"]) == {"C1", "C2"}
    assert res["titre"] and set(res["verdicts_familles"]) >= {"C1", "C2", "X2", "X8"}
    rep = build_report(_light(reg4), res, {"limites": []})
    assert_no_price_fields(rep)
    assert rep["A_resume"]["titre"] == res["titre"] and set(rep["A_resume"]["confirmatoires"]) == {"C1", "C2"}
    txt = render_fr(rep)
    assert "P — Politiques d'entrée" in txt and txt.index("X1.t00.exitX8 [placebo") < txt.index("  C1 [confirmatoire]")


def test_the_unseal_of_plan_4_reads_the_replication_signals_only(reg4):
    days = bdays("2005-01-03", 3300)
    p = _trending_panel(n_inst=24, n_days=3300, seed=12)
    p = dataclasses.replace(p, days=days, index_cac=dataclasses.replace(p.index_cac, days=days),
                            index_stoxx=dataclasses.replace(p.index_stoxx, days=days))  # fmt: skip
    res = run_registration([p], _light(reg4), settings(), (), PROD, "0", ["C1"], None, False)
    assert res["segment"] == "scelle" and set(res["cellules"]) == {"C1"}
    g = prepare_group(0, p, settings())
    rows = signal_rows(p.days, date(2007, 1, 2), date(2015, 9, 30), 65)
    gs = E.GroupSetups(g, settings(), PROD, E.entry_params(reg4, PROD), rows)
    sig = gs.signals(reg4.confirmatory[0])
    inside = (p.days[sig.ts] >= np.datetime64("2007-01-02")) & (p.days[sig.ts] <= np.datetime64("2015-09-30"))
    assert sig.size > 0 and inside.all() and res["cellules"]["C1"]["signaux"] == sig.size


def test_an_entry_plan_holds_entry_cells_only():
    raw = yaml.safe_load(REG4.read_bytes())
    raw["exploratory"].append({"family": "Z", "trigger": "t05", "params": {"k_sigma": 2.0}, "grid": {"h": [5]}})
    raw["expected_total_cells"] = 28
    with pytest.raises(PreregError, match="cellules d'entrée"):
        parse_prereg(raw, "x")
    assert Cell(id="x", tier="exploratory", family="F", trigger="t05").is_entry_cell is False
