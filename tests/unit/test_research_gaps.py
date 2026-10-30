# ruff: noqa: E501
"""Registration n° 7 (docs/34 §34.14, draft): the opening-gap-down trigger t18 on constructed bars (the causes study's
definition, point-in-time, one event per value every 10 sessions, splits), suspect prices skipped like a corporate
action, the XT exit (time exit sized on a never-executed 2.5 ATR stop), the draft plan and its confirmatory cells
declared for the sealed old replication period (once only, mechanical verdict, placebo first)."""

from __future__ import annotations

import dataclasses
import math
from datetime import date
from pathlib import Path

import numpy as np
import pytest
import yaml

from app.domain import move_causes as mc
from app.domain.indicators import Bar
from app.domain.moves import atr_series
from app.domain.research import triggers as T
from app.domain.research.entries import make_orders
from app.domain.research.exits import CLOSED, CORPORATE_ACTION, GAP_STOP, STOP, TIME, simulate
from app.domain.research.panel import suspect_price_sessions
from app.domain.research.pipeline import cell_model, cell_signals, prepare_group, primary_unit, trade_rows
from app.domain.research.prereg import PreregError, check_frozen, file_sha256, parse_prereg
from app.domain.research.registration import run_registration
from app.domain.research.sealed import (
    CONFIRMED,
    INCONCLUSIVE,
    NOT_CONFIRMED,
    check_sealed_once,
    dsr_trials,
    render_sealed,
    report_view,
    sealed_plan,
    sealed_verdicts,
)
from tests.unit.test_research import bdays, settings, synthetic_panel

ROOT = Path(__file__).resolve().parents[2]
REG3 = ROOT / "research" / "prereg" / "2026-10-28_reg3.yaml"
REG7 = ROOT / "research" / "prereg" / "2026-10-31_reg7.yaml"
GAP = {"gap_min": 0.03, "gap_atr": 1.5, "atr_sessions": 14, "dedup_sessions": 10, "dividend_neutral": True}


def _raw7() -> dict:
    return yaml.safe_load(REG7.read_bytes())


def _frozen7(raw: dict | None = None):
    """The draft with its ``draft`` key removed, as the freeze will do (another sha256: a test copy only)."""
    r = dict(raw or _raw7())
    r.pop("draft", None)
    return parse_prereg(r, "sha-test-reg7")


def _light(p, cells=None):
    inf = {**p.inference, "bootstrap_draws": 200, "permutation_draws": 200, "power_reps": 5}
    return dataclasses.replace(p, inference=inf, cells=tuple(cells) if cells is not None else p.cells)


def _quiet(n_inst=6, n_days=400, seed=3, start="2016-01-04", sigma=0.01):
    """Synthetic bars whose natural opening gaps stay far below 3 % (σ/3 noise on the open)."""
    return synthetic_panel(n_inst=n_inst, n_days=n_days, seed=seed, start=start, sigma=sigma)


def _gap(p, t: int, c: int, gap: float) -> None:
    """Open of row ``t`` at the previous close × (1 + gap), the session's range widened to hold it (in place)."""
    p.open[t, c] = p.close[t - 1, c] * (1 + gap)
    p.low[t, c] = min(p.low[t, c], p.open[t, c] * 0.999)
    p.high[t, c] = max(p.high[t, c], p.open[t, c])


def _feat(p):
    return prepare_group(0, p, settings()).f


def _t18(p, **kw):
    g = prepare_group(0, p, settings())
    return T.t18_gap_down(g.p, g.f, {**GAP, **kw})


# --- 1. the trigger -------------------------------------------------------------------------------------------------


def test_t18_base_events_are_the_causes_study_gaps_down_on_own_sessions():
    """Every gap down of the panel is the one of ``move_causes`` (opening_gap ≤ − gap_threshold with the study's
    simple ATR of the previous own session), raw prices, on the value's own sessions (a missing bar is skipped)."""
    p = _quiet(n_inst=5, n_days=500, seed=21, sigma=0.012)
    rng = np.random.default_rng(4)
    for _ in range(40):
        t, c = int(rng.integers(30, 490)), int(rng.integers(0, 5))
        _gap(p, t, c, float(rng.choice([-0.025, -0.035, -0.05, -0.09, 0.06])))
    for k in ("open", "high", "low", "close", "adj_close", "volume"):
        getattr(p, k)[200:203, 1] = np.nan  # value 1 suspended three sessions: its gaps span the hole
    f = _feat(p)
    ev, _ = T.gap_down_events(p, f, 0.03, 1.5, 14, dividend_neutral=False)
    cp = mc.CauseParams()
    for c in range(5):
        r = np.nonzero(np.isfinite(p.close[:, c]))[0]
        bars = [Bar(*(float(getattr(p, k)[i, c]) for k in ("open", "high", "low", "close")), 1) for i in r]
        atr = atr_series(bars)
        want = set()
        for k in range(1, len(bars)):
            thr = mc.gap_threshold(bars[k - 1].close, atr[k - 1], cp)
            g = mc.opening_gap(bars[k].open, bars[k - 1].close)
            if thr is not None and g is not None and g <= -thr:
                want.add(int(r[k]))
        assert set(np.nonzero(ev[:, c])[0].tolist()) == want
    assert ev.sum() > 10  # the planted gaps of 3.5 % and more (sometimes under 1.5 ATR) are there, never the gaps up


def test_t18_floor_atr_threshold_and_never_a_gap_up():
    p = _quiet(n_inst=3, n_days=300, seed=5, sigma=0.01)
    _gap(p, 150, 0, -0.025)  # under the 3 % floor
    _gap(p, 160, 0, -0.04)
    _gap(p, 180, 1, +0.08)  # a gap up
    q = _quiet(n_inst=3, n_days=300, seed=5, sigma=0.03)  # volatile: 1.5 × ATR ÷ close well above 3 %
    _gap(q, 150, 2, -0.04)
    _gap(q, 200, 2, -0.30)
    m, mq = _t18(p), _t18(q)
    assert not m[150, 0] and m[160, 0] and not m[:, 1].any()
    thr = 1.5 * T.simple_atr(q, _feat(q).pk, 14)[149, 2] / q.close[149, 2]
    assert thr > 0.04 and not mq[150, 2] and mq[200, 2]


def test_t18_signals_on_the_gap_session_and_the_order_fills_at_the_next_open():
    p = _quiet(n_inst=6, n_days=400, seed=7)
    _gap(p, 300, 2, -0.06)
    st = settings()
    g = prepare_group(0, p, st)
    c1 = _frozen7().confirmatory[0]
    assert (c1.trigger, c1.exit, c1.h, c1.unit) == ("t18", "XT", 10, "r_stop")
    sig = cell_signals(g, c1, st, (0, p.shape[0] - 1))
    assert list(zip(sig.ts.tolist(), sig.cols.tolist(), strict=True)) == [(300, 2)]
    model = cell_model(c1)
    od = make_orders(g.p, g.f, sig.ts, sig.cols, model, c1.validity, c1.entry_cap_atr)
    assert od.limit[0] == pytest.approx(p.close[300, 2] + g.f.atr14[300, 2])  # E_open(1): close of the gap day + 1 ATR
    sim = simulate(g.bars, od, model.spec, st.slippage)
    assert sim.status[0] == CLOSED and sim.fill_pos[0] == 301 and sim.entry[0] == pytest.approx(p.open[301, 2])
    assert sim.exit_pos[0] == 310 and sim.reason[0] == TIME  # the fill session counts: exit at the close of the 10th
    assert sim.exit_px[0] == pytest.approx(p.close[310, 2])


def test_t18_keeps_one_event_per_value_every_ten_sessions_counted_before_eligibility():
    p = _quiet(n_inst=4, n_days=400, seed=9)
    for t in (200, 205, 210, 214, 230):
        _gap(p, t, 0, -0.06)
    m = _t18(p)
    assert set(np.nonzero(m[:, 0])[0].tolist()) == {200, 210, 230}  # 205 within 10 of 200; 214 within 10 of 210
    p.volume[200, 0] = 0.0  # the first gap is not eligible (no volume): it still blocks the next 9 sessions
    m2 = _t18(p)
    assert not m2[200, 0] and not m2[205, 0] and m2[210, 0]


def test_t18_is_point_in_time():
    p = _quiet(n_inst=5, n_days=420, seed=11)
    for t, c in ((150, 0), (260, 1), (300, 3), (330, 4)):
        _gap(p, t, c, -0.07)
    m = _t18(p)
    cut = 301
    poisoned = dataclasses.replace(
        p, **{k: getattr(p, k).copy() for k in ("open", "high", "low", "close", "adj_close", "volume")}
    )
    for k in ("open", "high", "low", "close", "adj_close"):
        getattr(poisoned, k)[cut:] *= 0.5  # a crash after the gap of row 300: nothing before changes
    poisoned.volume[cut:] *= 40.0
    m2 = _t18(poisoned)
    assert (m2[:cut] == m[:cut]).all() and m[300, 3]


def test_t18_dividend_neutral_gap_ignores_a_detachment():
    p = _quiet(n_inst=3, n_days=320, seed=13)
    t = 250
    p.adj_close[:t, 1] *= 0.96  # a 4 % dividend detached on row t (total-return series)
    _gap(p, t, 1, -0.04)  # the raw open drops by the dividend
    _gap(p, 280, 2, -0.05)  # a real gap
    raw = _t18(p, dividend_neutral=False)
    neutral = _t18(p, dividend_neutral=True)
    assert raw[t, 1] and not neutral[t, 1]
    assert raw[280, 2] and neutral[280, 2]


def test_t18_splits_partition_the_events_and_the_earnings_split_needs_a_known_span():
    p = _quiet(n_inst=6, n_days=600, seed=17)
    sizes = {200: -0.035, 260: -0.06, 320: -0.12, 380: -0.045, 440: -0.08, 500: -0.2}
    for t, gp in sizes.items():
        for c in range(6):
            _gap(p, t, c, gp)
    base = _t18(p)
    parts = [_t18(p, size_from=0.0, size_to=0.05), _t18(p, size_from=0.05, size_to=0.10), _t18(p, size_from=0.10)]
    assert sum(x.sum() for x in parts) == base.sum() and not (parts[0] & parts[1]).any()
    assert set(np.nonzero(parts[2][:, 0])[0].tolist()) == {320, 500}
    small, large = _t18(p, cap_class="small"), _t18(p, cap_class="large")
    assert (small | large).sum() == base.sum() and not (small & large).any()
    assert small[:, p.small_cap].sum() == base[:, p.small_cap].sum() and not small[:, ~p.small_cap].any()
    # earnings: a reaction on the gap session (on), none within ± 2 sessions (off), unknown span (neither)
    react = np.zeros(p.shape, dtype=bool)
    known = np.ones(p.shape, dtype=bool)
    react[260, 0] = True  # the gap is the announcement's reaction
    react[322, 1] = True  # an announcement two sessions after the gap: not « without news »
    known[:, 5] = False  # value 5: no announcement history → unknown, never « no news »
    q = dataclasses.replace(p, earn_react=react, earn_known=known, earn_surprise_pos=np.full(p.shape, np.nan))
    on, off = _t18(q, earnings="on"), _t18(q, earnings="off")
    assert set(zip(*np.nonzero(on), strict=True)) == {(260, 0)}
    assert not off[260, 0] and not off[320, 1] and off[320, 0] and not off[:, 5].any()
    assert not _t18(p, earnings="on").any() and not _t18(p, earnings="off").any()  # no history at all: nothing


def test_t18_parameters_are_stated_by_the_plan():
    with pytest.raises(ValueError, match="absents"):
        T.check_gap_params({k: v for k, v in GAP.items() if k != "gap_atr"})
    with pytest.raises(ValueError, match="inconnus"):
        T.check_gap_params({**GAP, "cap": "small"})
    with pytest.raises(ValueError, match="découpage"):
        T.check_gap_params({**GAP, "earnings": "maybe"})
    with pytest.raises(ValueError, match="tranche"):
        T.check_gap_params({**GAP, "size_from": 0.1, "size_to": 0.05})
    assert T.cell_needs_earnings("t18", {**GAP, "earnings": "on"}) and not T.cell_needs_earnings("t18", GAP)
    assert T.cell_needs_earnings("t14", {})


# --- 2. suspect prices, the XT exit ----------------------------------------------------------------------------------


def test_suspect_prices_match_the_study_and_are_skipped_like_a_corporate_action():
    p = _quiet(n_inst=4, n_days=700, seed=23)
    for k in ("open", "close", "high", "low", "adj_close"):
        getattr(p, k)[300, 1] *= 5.0  # a bad tick (× 5 for one session, the factor unchanged)
        getattr(p, k)[500:, 2] /= 5.0  # a unit change (÷ 5 from row 500 on)
    sus = suspect_price_sessions(p.open, p.close, _feat(p).pk, 4.0)
    for c in range(4):
        r = np.nonzero(np.isfinite(p.close[:, c]))[0]
        want = {int(r[k]) for k in mc.suspect_sessions(p.open[r, c].tolist(), p.close[r, c].tolist(), 4.0)}
        assert set(np.nonzero(sus[:, c])[0].tolist()) == want
    assert set(np.nonzero(sus[:, 1])[0].tolist()) == {300, 301} and set(np.nonzero(sus[:, 2])[0].tolist()) == {500}
    plain = prepare_group(0, p, settings())  # every earlier plan: no check (no factor jump either)
    assert not plain.bars.ca[:, 1:3].any() and plain.f.elig[310:400, 1].all()
    st = dataclasses.replace(settings(), suspect_ratio=4.0)
    g = prepare_group(0, p, st)
    assert g.bars.ca[300, 1] and g.bars.ca[301, 1] and g.bars.ca[500, 2] and g.quality["seances_suspectes"] == 3
    assert not g.f.elig[300:552, 1].any() and not g.f.elig[500:752, 2].any() and g.f.elig[560:, 1].any()
    # a trade held across a suspect session is excluded (status « opération sur titres »), counted
    c1 = _frozen7().confirmatory[0]
    model = cell_model(c1)
    ts, cols = np.array([294, 250], dtype=np.int64), np.array([1, 0], dtype=np.int64)
    sim = simulate(g.bars, make_orders(g.p, g.f, ts, cols, model, 1, 1.0), model.spec, st.slippage)
    assert sim.status[0] == CORPORATE_ACTION and sim.status[1] == CLOSED


def test_xt_is_a_time_exit_sized_on_a_never_executed_atr_stop():
    p = _quiet(n_inst=5, n_days=400, seed=29)
    _gap(p, 300, 3, -0.06)
    for k in ("open", "high", "low", "close", "adj_close"):  # −2 % a session from 303 to 310: through the stop
        getattr(p, k)[303:, 3] *= 0.98 ** np.minimum(np.arange(303, 400) - 302, 8)
    st = settings()
    g = prepare_group(0, p, st)
    c1 = _frozen7().confirmatory[0]
    x5 = next(c for c in _frozen7().cells if c.id == "X5.t18.h10")
    assert (x5.exit, x5.max_hold, x5.unit) == ("X8", 10, "r_stop")
    sig = cell_signals(g, c1, st, (0, p.shape[0] - 1))
    out = {}
    for cell in (c1, x5):
        model = cell_model(cell)
        od = make_orders(g.p, g.f, sig.ts, sig.cols, model, 1, 1.0)
        sim = simulate(g.bars, od, model.spec, st.slippage)
        tr = trade_rows(g, cell, sig, od, sim, model, None, st, cell.unit)
        out[cell.exit] = (sim, tr)
    sim_t, tr_t = out["XT"]
    sim_8, tr_8 = out["X8"]
    entry, atr = p.open[301, 3], g.f.atr14[300, 3]
    for sim in (sim_t, sim_8):  # the same 2.5 ATR14 stop (at the close of the signal), slipped, sizes both
        assert sim.stop_exec_init[0] == pytest.approx((entry - 2.5 * atr) * (1 - st.slippage))
    assert sim_t.reason[0] == TIME and sim_t.exit_pos[0] == 310  # never stopped: held to the 10th session
    assert sim_8.reason[0] in (STOP, GAP_STOP) and sim_8.exit_pos[0] < 310  # X8 executes the same stop
    assert tr_t["r"][0] < tr_8["r"][0] - 0.5 < -1.0  # unprotected, the loss runs well beyond 1 R of the trade
    assert primary_unit(c1.combo_key) == "r_stop" and primary_unit(x5.combo_key) == "r_stop"
    assert primary_unit(("X3", 1.0, 1, 10, None, None)) == "r_ref"


# --- 3. the draft plan -----------------------------------------------------------------------------------------------


def test_plan_7_is_a_draft_refused_by_every_command_until_frozen():
    b = REG7.read_bytes()
    draft = parse_prereg(yaml.safe_load(b), file_sha256(b))
    assert draft.draft and draft.id == "2026-10-31_reg7" and draft.seed == 20261031
    with pytest.raises(PreregError, match="BROUILLON"):
        check_frozen(draft)
    p = _frozen7()
    check_frozen(p)
    assert not p.draft and p.sealed_confirmation and p.needs_earnings
    assert p.counts() == {"confirmatoires": 2, "exploratoires_etape0": 13, "etape1_plafond": 0, "diagnostics_f0": 0,
                          "etape2_plafond": 3, "total": 18}  # fmt: skip
    assert [c.id for c in p.confirmatory] == ["C1", "C2"] and [c.h for c in p.confirmatory] == [10, 20]
    assert all(c.params == GAP and c.exit == "XT" and c.unit == "r_stop" for c in p.confirmatory)
    ids = {c.id for c in p.cells}
    assert {"X1.t00.h10", "X1.t00.h20", "X2a.t18.h20", "X2b.t18.h20", "X2c.t18.h20", "X3.t18.cap_classsmall.h20",
            "X3.t18.cap_classlarge.h20", "X4.t18.earningson.h20", "X4.t18.earningsoff.h20", "X5.t18.h10",
            "X5.t18.h20", "X6.t18.h5", "X7.t18.h20"} <= ids  # fmt: skip
    assert p.replication == (date(2007, 1, 2), date(2015, 9, 30)) and len(p.replication_folds) == 5
    assert p.replication_folds[0][0] == date(2007, 1, 2) and p.replication_folds[-1][1] == date(2015, 9, 30)
    assert p.assumptions["suspect_price_ratio"] == 4.0 and p.thresholds["sealed"]["min_t_week"] == 3.0
    assert dsr_trials(p) == p.thresholds["sealed"]["dsr_prior_trials"] + 13  # every non-placebo cell counted
    sp = sealed_plan(p)
    assert sp.segments.discovery == p.replication and sp.segments.wf_test == p.replication_folds
    assert sp.sha256 == p.sha256 and sp.cells == p.cells
    reg3 = parse_prereg(yaml.safe_load(REG3.read_bytes()), "x")  # registration n° 3 unchanged: candidates mode
    assert reg3.replication is not None and not reg3.sealed_confirmation and not reg3.draft
    with pytest.raises(PreregError, match="confirmation"):
        sealed_plan(reg3)


@pytest.mark.parametrize(
    ("edit", "match"),
    [
        (lambda r: r["replication"].pop("wf_test"), "plis"),
        (lambda r: r["replication"]["wf_test"].append(["2015-06-01", "2016-03-31"]), "plis"),
        (lambda r: r["replication"].update(mode="peut-être"), "mode"),
        (lambda r: r["thresholds"]["sealed"].pop("dsr_prior_trials"), "sealed"),
        (lambda r: r["confirmatory"][0]["params"].pop("dedup_sessions"), "absents"),
        (lambda r: r["confirmatory"][0].update(cap="small"), "cap_class"),
        (lambda r: r["confirmatory"][0].update(unit="r_ref"), "r_stop"),
        (lambda r: r["exploratory"][0].update(unit="r_ref"), "r_stop"),
    ],
)
def test_plan_7_edits_that_the_harness_refuses(edit, match):
    raw = _raw7()
    edit(raw)
    with pytest.raises(PreregError, match=match):
        _frozen7(raw)


def test_the_sealed_period_opens_once_without_a_candidate_file():
    p = _frozen7()
    check_sealed_once(p, [{"kind": "run", "prereg_sha256": p.sha256, "prereg_id": p.id}], None)
    with pytest.raises(PreregError, match="fichier de candidats"):
        check_sealed_once(p, [], "data/research/x_candidates.yaml")
    with pytest.raises(PreregError, match="une fois"):
        check_sealed_once(p, [{"kind": "unseal", "prereg_sha256": p.sha256, "prereg_id": p.id}], None)
    with pytest.raises(PreregError, match="une fois"):  # the same plan id re-frozen with another sha256
        check_sealed_once(p, [{"kind": "unseal", "prereg_sha256": "autre", "prereg_id": p.id}], None)


# --- 4. the sealed verdict -------------------------------------------------------------------------------------------


ALL_A = {f"A{k}": True for k in range(1, 14)}


def _cell_stats(**kw):
    s = {"n": 900, "semaines": 400, "mean_r": 0.2, "mean_eur_300": 60.0, "profit_factor": 1.4, "mean_pct_notionnel": 0.01,
         "mean_excess": 0.15, "excess_ctrl_ic": [0.05, 0.25], "t_week": 4.5, "p_holm": 0.0001, "plis_positifs": 5,
         "criteres": dict(ALL_A), "_sr": 0.35, "_nobs": 450, "_skew": 0.0, "_kurt": 3.0}  # fmt: skip
    s.update(kw)
    return s


def _res(c1=None, c2=None, placebo_t=0.4, power=0.9):
    p = _frozen7()
    # the gap cells of a real effect have Sharpe ratios close to each other: their dispersion sets the DSR bar
    cells = {c.id: _cell_stats(_sr=0.30 + 0.01 * (k % 5), t_week=3.1) for k, c in enumerate(p.cells)}
    cells["C1"] = c1 or _cell_stats()
    cells["C2"] = c2 or _cell_stats(_sr=0.33)
    for pid in ("X1.t00.h10", "X1.t00.h20"):
        cells[pid] = _cell_stats(t_week=placebo_t, mean_excess=0.01)
    return p, {
        "cellules": cells,
        "familles": {"C1": {"puissance_decision": power}, "C2": {"puissance_decision": power}},
    }


def test_sealed_verdict_confirms_only_with_every_criterion_and_reads_the_placebo_first():
    p, res = _res()
    v = sealed_verdicts(res, p)
    assert v["confirmatoires"]["C1"]["verdict"] == CONFIRMED and v["confirmes"] == ["C1", "C2"]
    assert v["ordre_de_lecture"][:4] == ["X1.t00.h10", "X1.t00.h20", "C1", "C2"]  # the placebo is read first
    assert v["confirmatoires"]["C1"]["placebo"] == "X1.t00.h10" and v["confirmatoires"]["C2"]["placebo"] == "X1.t00.h20"
    assert v["dsr_essais"] == dsr_trials(p) and res["cellules"]["C1"]["dsr"] >= 0.95
    assert "suivi en ombre" in v["titre"]
    # t under 3: not confirmed (power fine)
    p, res = _res(c1=_cell_stats(t_week=2.6))
    v = sealed_verdicts(res, p)["confirmatoires"]["C1"]
    assert v["verdict"] == NOT_CONFIRMED and v["manquants"] == ["t_semaine"]
    # one A criterion missing (e.g. A7 regime): not confirmed
    p, res = _res(c1=_cell_stats(criteres={**ALL_A, "A7": False}))
    assert sealed_verdicts(res, p)["confirmatoires"]["C1"]["verdict"] == NOT_CONFIRMED
    # a weak Sharpe against N = 93 trials: the DSR refuses
    p, res = _res(c1=_cell_stats(_sr=0.05))
    v = sealed_verdicts(res, p)["confirmatoires"]["C1"]
    assert v["verdict"] == NOT_CONFIRMED and "dsr" in v["manquants"]
    # A1 missing → inconclusive, never « no edge »
    p, res = _res(c1=_cell_stats(criteres={**ALL_A, "A1": False}))
    assert sealed_verdicts(res, p)["confirmatoires"]["C1"]["verdict"] == INCONCLUSIVE
    # the placebo shows an excess (|t| ≥ 3): the measure is suspect → inconclusive for both
    p, res = _res(placebo_t=-3.4)
    v = sealed_verdicts(res, p)
    assert {x["verdict"] for x in v["confirmatoires"].values()} == {INCONCLUSIVE} and v["placebo"]["X1.t00.h10"][
        "suspect"
    ]
    assert "rien ne change" in v["titre"]
    # not confirmed with a low power at the decision edge → inconclusive
    p, res = _res(c1=_cell_stats(t_week=1.2), power=0.3)
    assert sealed_verdicts(res, p)["confirmatoires"]["C1"]["verdict"] == INCONCLUSIVE


# --- 5. the sealed run on a synthetic panel --------------------------------------------------------------------------


def _sealed_panel(edge: float, n_inst=120, events=900, seed=41):
    """2005-01-03 → 2016-02: planted gaps down of −6 % with a reversal of ``edge`` over the next 10 sessions (entry at
    the open after the gap), inside the replication period and outside it (warm-up and after 2015-09-30)."""
    rng = np.random.default_rng(seed)
    n_days = 2900
    days = bdays("2005-01-03", n_days)
    row = {str(d): k for k, d in enumerate(days)}
    lo, hi = row["2005-06-01"], n_days - 40
    extra = np.zeros((n_days, n_inst))
    planted: list[tuple[int, int]] = []
    taken = np.zeros((n_days, n_inst), dtype=bool)
    while len(planted) < events:
        t, c = int(rng.integers(lo, hi)), int(rng.integers(0, n_inst))
        if taken[max(0, t - 30) : t + 30, c].any():
            continue
        taken[t, c] = True
        extra[t + 1 : t + 11, c] += math.log(1 + edge) / 10
        planted.append((t, c))
    p = synthetic_panel(n_inst, n_days, seed, start="2005-01-03", sigma=0.01, lr_extra=extra)
    for t, c in planted:
        _gap(p, t, c, -0.06)
    return p, planted, row


def test_sealed_run_reads_the_replication_period_only_and_confirms_a_planted_reversal():
    plan = _frozen7()
    cells = [c for c in plan.cells if c.id in ("C1", "C2", "X1.t00.h10", "X1.t00.h20")]
    sp = _light(sealed_plan(plan), cells)
    st = dataclasses.replace(settings(), universe_label="all", suspect_ratio=4.0)
    p, planted, row = _sealed_panel(edge=0.03)
    res = run_registration([p], sp, st, stages="all", f0=False)
    a, b = row["2007-01-02"], row["2015-09-30"]
    inside = sum(1 for t, _ in planted if a <= t <= b)
    c1 = res["cellules"]["C1"]
    assert res["segment"] == "decouverte" and 0.9 * inside <= c1["signaux"] <= inside  # the window is the sealed period
    assert len(c1["plis"]) == 5 and c1["plis"][0]["annee"] == 2007 and c1["pli_min_n"] >= 40
    v = sealed_verdicts(res, sp)
    assert v["confirmatoires"]["C1"]["verdict"] == CONFIRMED, v["confirmatoires"]["C1"]["manquants"]
    assert not v["placebo"]["X1.t00.h10"]["suspect"]
    # the same gaps without the reversal: never confirmed
    q, _, _ = _sealed_panel(edge=0.0)
    v0 = sealed_verdicts(run_registration([q], sp, st, stages="all", f0=False), sp)
    assert {x["verdict"] for x in v0["confirmatoires"].values()} <= {NOT_CONFIRMED, INCONCLUSIVE}


def test_the_report_view_masks_small_rows_but_keeps_every_verdict():
    p, res = _res(c1=_cell_stats(n=12, criteres={**ALL_A, "A1": False}))
    v = sealed_verdicts(res, p)
    view = report_view(v, 20)
    c1 = view["confirmatoires"]["C1"]
    assert c1["verdict"] == INCONCLUSIVE and "A1" in c1["manquants"] and c1["supprime"] and "mean_r" not in c1
    assert view["confirmatoires"]["C2"]["mean_r"] == v["confirmatoires"]["C2"]["mean_r"]  # a large row is untouched
    assert view["placebo"]["X1.t00.h10"]["suspect"] is False
    txt = render_sealed(view)
    assert "C1 : NON CONCLUANT" in txt and "V — Verdict de la période scellée 2007-01-02 → 2015-09-30" in txt
