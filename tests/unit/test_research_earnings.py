# ruff: noqa: E501
"""Registration n° 3 (docs/34 §34.5): earnings announcements in the research panel (reaction session, known span,
surprise), the information triggers t14 / t15 / t16 on small synthetic panels, the coverage floor, the plan and
its sealed old replication period."""

from __future__ import annotations

import dataclasses
from datetime import date
from pathlib import Path

import numpy as np
import pytest
import yaml

from app.domain.research import triggers as T
from app.domain.research.panel import EarningsRow, build_features, earnings_flags
from app.domain.research.pipeline import cell_signals, earnings_coverage, prepare_group, raw_trigger, signal_rows
from app.domain.research.prereg import (
    PreregError,
    check_run_start,
    check_unseal_span,
    file_sha256,
    parse_prereg,
)
from app.domain.research.registration import run_registration
from tests.unit.test_research import bdays, settings, synthetic_panel

ROOT = Path(__file__).resolve().parents[2]
REG2 = ROOT / "research" / "prereg" / "2026-10-28_reg2.yaml"
REG3 = ROOT / "research" / "prereg" / "2026-10-28_reg3.yaml"


def _load(path: Path):
    b = path.read_bytes()
    return parse_prereg(yaml.safe_load(b), file_sha256(b))


@pytest.fixture(scope="module")
def reg3():
    return _load(REG3)


def _light(p):
    inf = {**p.inference, "bootstrap_draws": 200, "permutation_draws": 50, "power_reps": 5}
    return dataclasses.replace(p, inference=inf)


def _d(s: str) -> np.datetime64:
    return np.datetime64(s, "D")


# --- 1. panel flags ------------------------------------------------------------------------------------------


def test_reaction_session_amc_is_the_next_own_session_and_unknown_span_is_never_an_event():
    days = bdays("2020-01-06", 300)  # Monday 6 January 2020
    valid = np.ones((300, 2), dtype=bool)
    valid[10, 1] = False  # value B has no bar on Monday 20 January (suspended)
    valid[100:120, 1] = False  # … nor for four weeks from row 100 (a long suspension)
    isins = ("AA0000000001", "BB0000000002")
    rows = [
        EarningsRow("AA0000000001", _d("2020-01-08"), "bmo", 1.2, 1.0),  # Wednesday before the open → Wednesday, beat
        EarningsRow("AA0000000001", _d("2020-01-10"), "amc", 0.8, 1.0),  # Friday after the close → Monday 13, miss
        EarningsRow("AA0000000001", _d("2020-01-18"), "unknown"),  # Saturday, time unknown → Monday 20, no EPS
        EarningsRow("BB0000000002", _d("2020-01-17"), "amc", 1.0, 1.0),  # Friday amc, Monday missing → Tuesday 21
        EarningsRow("BB0000000002", _d("2020-02-04"), "bmo", 2.0, 1.0),  # sources disagree on the time: both flagged
        EarningsRow("BB0000000002", _d("2020-02-04"), "amc", 0.5, 1.0),  # … and on the surprise: unknown (NaN)
        EarningsRow("BB0000000002", _d("2020-03-03"), "unknown", 1.1, 1.0),  # unknown dropped when a source knows
        EarningsRow("BB0000000002", _d("2020-03-03"), "amc", None, None),
        EarningsRow("BB0000000002", days[101], "bmo"),  # suspended: next own session 4 weeks later, not a reaction
        EarningsRow("BB0000000002", _d("2019-06-03"), "bmo"),  # before the panel: outside its calendar
        EarningsRow("ZZ0000000009", _d("2020-01-08"), "bmo"),  # a value outside the panel: ignored
    ]
    fl = earnings_flags(days, valid, isins, rows, margin_days=100, max_gap_days=7)
    flagged = {(str(days[t]), k) for t, k in zip(*np.nonzero(fl.react), strict=True)}
    assert flagged == {
        ("2020-01-08", 0), ("2020-01-13", 0), ("2020-01-20", 0),
        ("2020-01-21", 1), ("2020-02-04", 1), ("2020-02-05", 1), ("2020-03-04", 1),
    }  # fmt: skip
    assert (fl.placed, fl.unplaced, fl.conflicting_when, fl.outside) == (6, 1, 1, 1)
    t = {str(d): k for k, d in enumerate(days)}
    assert fl.surprise_pos[t["2020-01-08"], 0] == 1.0 and fl.surprise_pos[t["2020-01-13"], 0] == 0.0
    assert np.isnan(fl.surprise_pos[t["2020-01-20"], 0]) and np.isnan(fl.surprise_pos[t["2020-02-04"], 1])
    assert fl.surprise_pos[t["2020-03-04"], 1] == 1.0
    assert np.isnan(fl.surprise_pos[~fl.react]).all()  # only on reaction sessions
    # the day before a reaction is never flagged (no look-ahead: a flag sits on its own session only)
    assert not fl.react[t["2020-01-10"], 0] and not fl.react[t["2020-01-17"], 1]
    # known: within 100 days of a loaded announcement (A: 8 Jan → 18 Jan announcements)
    k0 = fl.known[:, 0]
    assert k0[t["2020-01-06"]] and k0[t["2020-04-27"]] and not k0[t["2020-04-28"]]


def test_known_span_keeps_a_hole_in_the_history_unknown():
    days = bdays("2015-01-05", 900)
    valid = np.ones((900, 1), dtype=bool)
    rows = [EarningsRow("AA0000000001", _d(x), "bmo") for x in ("2015-03-02", "2015-08-03", "2017-03-01")]
    fl = earnings_flags(days, valid, ("AA0000000001",), rows, 100, 7)
    t = {str(d): k for k, d in enumerate(days)}
    assert fl.known[t["2015-05-05"], 0] and fl.known[t["2015-11-10"], 0]  # half-yearly: the span is continuous
    assert not fl.known[t["2016-03-01"], 0]  # 2016 missing in the history: unknown, not « no announcement »
    assert fl.known[t["2016-11-22"], 0] and not fl.known[t["2016-11-18"], 0]  # 2017-03-01 − 100 days = 21 Nov
    assert fl.known[t["2015-01-05"], 0]  # 56 days before the first announcement


# --- 2. triggers on synthetic panels -----------------------------------------------------------------------------


def _shock_panel(n_inst=6, n_days=600, seed=3):
    p = synthetic_panel(n_inst=n_inst, n_days=n_days, seed=seed)
    react = np.zeros(p.shape, dtype=bool)
    known = np.ones(p.shape, dtype=bool)
    return dataclasses.replace(p, earn_react=react, earn_known=known, earn_surprise_pos=np.full(p.shape, np.nan))


def _plant_shock(p, t: int, c: int, jump: float = 0.12) -> None:
    """A +12 % close on a 20 × volume, close at the top of the range (arrays edited in place)."""
    prev = p.close[t - 1, c]
    p.open[t, c] = prev * 1.005
    p.close[t, c] = p.adj_close[t, c] = prev * (1 + jump)
    p.high[t, c] = p.close[t, c] * 1.001
    p.low[t, c] = prev * 0.999
    p.volume[t, c] = 20 * float(np.median(p.volume[:, c]))


def _feat(p):
    return build_features(p, settings().features)


def test_t14_fires_on_a_shock_of_a_reaction_session_inside_the_known_span_only():
    p = _shock_panel()
    _plant_shock(p, 400, 2)
    _plant_shock(p, 450, 3)
    p.earn_react[400, 2] = True  # announcement reaction on the shock
    p.earn_react[451, 3] = True  # the shock of value 3 is the session BEFORE its reaction: no t14
    f = _feat(p)
    m = T.t14_earnings_reaction(p, f, 2.0, 2.0, 0.5)
    assert m[400, 2] and not m[450, 3] and not m[451, 3]
    assert set(zip(*np.nonzero(m), strict=True)) == {(400, 2)}  # reaction flags only where announced
    poisoned = dataclasses.replace(p, close=p.close.copy(), adj_close=p.adj_close.copy(), volume=p.volume.copy())
    poisoned.close[401:] *= 3.0
    poisoned.adj_close[401:] *= 3.0
    poisoned.volume[401:] *= 50.0  # the future changes nothing up to J0
    mp = T.t14_earnings_reaction(poisoned, _feat(poisoned), 2.0, 2.0, 0.5)
    assert (mp[:401] == m[:401]).all()
    # surprise filter: unknown → no signal, positive → signal
    assert not T.t14_earnings_reaction(p, f, 2.0, 2.0, 0.5, require_surprise=True)[400, 2]
    p.earn_surprise_pos[400, 2] = 1.0
    assert T.t14_earnings_reaction(p, f, 2.0, 2.0, 0.5, require_surprise=True)[400, 2]
    p.earn_surprise_pos[400, 2] = 0.0
    assert not T.t14_earnings_reaction(p, f, 2.0, 2.0, 0.5, require_surprise=True)[400, 2]
    # outside the known span: never
    p.earn_known[:, 2] = False
    assert not T.t14_earnings_reaction(p, f, 2.0, 2.0, 0.5)[400, 2]
    # without any announcement history: nothing at all
    bare = dataclasses.replace(p, earn_react=None, earn_known=None, earn_surprise_pos=None)
    assert not T.t14_earnings_reaction(bare, _feat(bare), 2.0, 2.0, 0.5).any()


def test_t14_abnormal_return_is_net_of_the_venue_index():
    p = _shock_panel()
    _plant_shock(p, 400, 2, jump=0.12)
    p.earn_react[400, 2] = True
    f = _feat(p)
    ar = T.abnormal_log_return(p, f)
    idx = p.index_cac.close  # value 2 is Paris-flagged (is_xpar): its index is the CAC 40 series
    assert p.is_xpar[2]
    expected = np.log(p.adj_close[400, 2] / p.adj_close[399, 2]) - np.log(idx[400] / idx[399])
    assert ar[400, 2] == pytest.approx(expected, abs=1e-12)
    # the same +12 % on the index: no abnormal return, no signal
    close = idx.copy()
    close[400:] *= 1.12
    q = dataclasses.replace(p, index_cac=dataclasses.replace(p.index_cac, close=close))
    assert not T.t14_earnings_reaction(q, _feat(q), 2.0, 2.0, 0.5)[400, 2]
    # a NaN index: never estimated, no signal
    r = dataclasses.replace(p, index_cac=None)
    assert not T.t14_earnings_reaction(r, _feat(r), 2.0, 2.0, 0.5)[400, 2]


def test_t15_is_lead_own_sessions_before_the_reaction_session():
    p = _shock_panel()
    for k in ("open", "high", "low", "close", "adj_close", "volume"):
        getattr(p, k)[397, 1] = np.nan  # value 1 has no bar 3 rows before its reaction: own sessions count
    p.earn_react[400, 1] = True
    p.earn_react[400, 0] = True
    f = _feat(p)
    m = T.t15_pre_earnings(p, f, lead=5)
    assert m[395, 0] and not m[396, 0]
    assert m[394, 1] and not m[395, 1]  # 394, 395, 396, 398, 399 → 400: five own sessions
    assert set(zip(*np.nonzero(m), strict=True)) == {(395, 0), (394, 1)}
    p.earn_known[:, 0] = False
    assert not T.t15_pre_earnings(p, _feat(p), lead=5)[395, 0]


def test_t16_is_the_same_shock_without_an_announcement_within_two_sessions():
    p = _shock_panel()
    _plant_shock(p, 400, 2)
    _plant_shock(p, 400, 3)
    _plant_shock(p, 400, 4)
    p.earn_react[402, 2] = True  # two sessions later: news → not a « without news » shock
    p.earn_react[403, 3] = True  # three sessions later: outside the window
    p.earn_react[398, 1] = True
    f = _feat(p)
    m = T.t16_shock_without_news(p, f, 2.0, 2.0, 0.5)
    assert not m[400, 2] and m[400, 3] and m[400, 4]
    p.earn_known[:, 4] = False  # unknown is not « no news »
    assert not T.t16_shock_without_news(p, _feat(p), 2.0, 2.0, 0.5)[400, 4]
    # a shock ON the reaction session is t14's, never t16's
    p.earn_react[400, 3] = True
    m2 = T.t16_shock_without_news(p, _feat(p), 2.0, 2.0, 0.5)
    assert not m2[400, 3] and T.t14_earnings_reaction(p, _feat(p), 2.0, 2.0, 0.5)[400, 3]


# --- 3. pipeline: cells, coverage floor -------------------------------------------------------------------------


def test_registered_cells_signal_through_the_pipeline_and_the_coverage_floor_silences_a_group(reg3):
    p = _shock_panel()
    _plant_shock(p, 400, 2)
    p.earn_react[400, 2] = True
    st = dataclasses.replace(settings(), min_earnings_coverage=0.5)
    g = prepare_group(0, p, st)
    c1 = next(c for c in reg3.cells if c.id == "C1")
    rows = signal_rows(p.days, date(2017, 1, 2), date(2026, 7, 31), 65)
    sig = cell_signals(g, c1, st, rows)
    assert (int(sig.ts[0]), int(sig.cols[0])) == (400, 2) and sig.ts.size == 1
    t15 = next(c for c in reg3.cells if c.trigger == "t15")
    assert t15.h == 4 and t15.params["lead"] == 5  # exit at the close of the session before the reaction
    s15 = cell_signals(g, t15, st, rows)
    assert (int(s15.ts[0]), int(s15.cols[0])) == (395, 2)
    cov = earnings_coverage(g, rows, 0.5)
    assert cov["compte_dans_le_verdict"] and cov["part_observations_couvertes"] == 1.0
    p.earn_known[:, :4] = False  # 4 values of 6 unknown: coverage ≈ 1/3 < 50 %
    g2 = prepare_group(0, p, st)
    cov2 = earnings_coverage(g2, rows, 0.5)
    assert cov2["statut"] == "couverture insuffisante" and cov2["valeurs_couvertes"] == 2
    g2.earn_ok = False
    assert not raw_trigger(g2, c1, st)[0].any()
    placebo = next(c for c in reg3.cells if c.trigger == "t00")
    always = dataclasses.replace(placebo, params={**placebo.params, "rate": 1.0})
    assert raw_trigger(g2, always, st)[0].any()  # the placebo is not an earnings cell: never silenced


def test_registration_reports_the_coverage_and_keeps_a_low_group_out_of_the_verdict(reg3):
    p = _shock_panel(n_inst=8, n_days=900)
    for t in range(300, 880, 60):
        _plant_shock(p, t, 1)
        p.earn_react[t, 1] = True
    st = dataclasses.replace(settings(), min_earnings_coverage=0.5)
    plan = _light(reg3)
    res = run_registration([p], plan, st, stages="0", f0=False)
    cov = res["couverture_resultats"]["P0"]
    assert cov["compte_dans_le_verdict"] and res["cellules"]["C1"]["signaux"] > 0
    low = dataclasses.replace(p, earn_known=np.zeros(p.shape, dtype=bool))
    res2 = run_registration([low], plan, st, stages="0", f0=False)
    assert res2["couverture_resultats"]["P0"]["statut"] == "couverture insuffisante"
    assert res2["cellules"]["C1"]["signaux"] == 0 and res2["cellules"]["X4.t16.h10"]["signaux"] == 0
    assert any("couverture" in w for w in res2["avertissements"])


# --- 4. the plan and its sealed old replication period -----------------------------------------------------------


def test_plan_3_parses_with_its_cells_counts_and_replication(reg3):
    assert reg3.id == "2026-10-28_reg3" and reg3.seed == 20261029 and reg3.venue_groups is None
    assert reg3.counts() == {"confirmatoires": 2, "exploratoires_etape0": 5, "etape1_plafond": 0,
                             "diagnostics_f0": 0, "etape2_plafond": 3, "total": 10}  # fmt: skip
    assert sorted(c.id for c in reg3.cells) == ["C1", "C2", "X1.t00.h10", "X1.t00.h20", "X2.t14.h10",
                                                "X3.t15.h4", "X4.t16.h10"]  # fmt: skip
    c1, c2 = reg3.confirmatory
    assert (c1.trigger, c1.h, c2.h, c1.exit, c1.entry_cap_atr, c1.validity) == ("t14", 10, 20, "X3", 1.0, 1)
    assert c1.accounts == ("cto", "pea") and c1.params["require_surprise"] is False
    assert reg3.replication == (date(2007, 1, 2), date(2015, 9, 30)) and reg3.needs_earnings
    assert reg3.sealed_window == reg3.replication
    assert reg3.thresholds["min_earnings_coverage"] == 0.5
    reg2 = _load(REG2)  # registration n° 2 unchanged: no replication, the holdout window, no earnings
    assert reg2.replication is None and not reg2.needs_earnings
    assert reg2.sealed_window == (date(2099, 1, 3), date(2999, 12, 31))
    raw = yaml.safe_load(REG3.read_bytes())
    raw["confirmatory"][0]["trigger"] = "t99"
    with pytest.raises(PreregError, match="inconnu"):
        parse_prereg(raw, "x")
    raw = yaml.safe_load(REG3.read_bytes())
    raw["replication"] = {"start": "2010-01-04", "end": "2017-06-30"}  # overlapping the discovery: refused
    with pytest.raises(PreregError, match="réplication"):
        parse_prereg(raw, "x")


def test_run_never_reads_the_replication_period_and_unseal_needs_its_whole_span(reg3):
    with pytest.raises(PreregError, match="réplication"):
        check_run_start(reg3, date(2015, 9, 30))
    with pytest.raises(PreregError):
        check_run_start(reg3, date(2005, 1, 3))
    check_run_start(reg3, date(2015, 10, 1))
    check_run_start(reg3, date(2016, 1, 4))  # the CLI default
    check_run_start(_load(REG2), date(2005, 1, 3))  # no replication: nothing to guard
    with pytest.raises(PreregError, match="--from"):
        check_unseal_span(reg3, date(2016, 1, 4), date(2026, 10, 27))  # the CLI default start: no warm-up
    with pytest.raises(PreregError):
        check_unseal_span(reg3, date(2006, 1, 1), date(2015, 10, 15))  # the last signals could not be held
    check_unseal_span(reg3, date(2005, 1, 3), date(2016, 1, 31))


def test_unseal_of_the_replication_reads_signals_inside_it_only(reg3):
    p = _shock_panel(n_inst=6, n_days=3300, seed=11)  # re-dated below: 2005-01-03 → ~2017-08
    days = bdays("2005-01-03", 3300)
    p = dataclasses.replace(p, days=days, index_cac=dataclasses.replace(p.index_cac, days=days),
                            index_stoxx=dataclasses.replace(p.index_stoxx, days=days))  # fmt: skip
    row = {str(d): k for k, d in enumerate(days)}
    inside = [row[x] for x in ("2008-03-04", "2011-05-03", "2015-09-29")]
    outside = [row[x] for x in ("2006-06-06", "2015-10-06", "2016-03-01", "2017-03-01")]
    for t in inside + outside:
        _plant_shock(p, t, 2)
        p.earn_react[t, 2] = True
    st = settings()
    res = run_registration([p], _light(reg3), st, stages="0", holdout_cells=["C1"], f0=False)
    assert res["segment"] == "scelle" and set(res["cellules"]) == {"C1"}
    g = prepare_group(0, p, st)
    sig = cell_signals(g, reg3.confirmatory[0], st, (0, p.shape[0] - 1))
    planted = {int(t) for t, c in zip(sig.ts, sig.cols, strict=True) if c == 2}
    assert set(inside) <= planted and set(outside) & planted  # the trigger sees both sides …
    n_inside = sum(1 for t, _ in zip(sig.ts, sig.cols, strict=True)
                   if row["2007-01-02"] <= t <= row["2015-09-30"])  # fmt: skip
    assert n_inside == len(inside)  # only the planted announcements react
    assert res["cellules"]["C1"]["signaux"] == n_inside  # … the unseal counts the replication's only
