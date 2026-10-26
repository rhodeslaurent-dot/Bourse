# ruff: noqa: E501
"""docs/31 — trigger research harness: synthetic data only (planted edge found, random walk gives nothing, no
look-ahead, engine equivalence with ``simulate_trade``, features vs scalar indicators, dividends, costs, stats,
pre-registration, report)."""

from __future__ import annotations

import dataclasses
import math
from pathlib import Path

import numpy as np
import pytest
import yaml

from app.domain.backtest import simulate_trade
from app.domain.costs import CostParams, buy_fees, sell_fees
from app.domain.indicators import Bar, atr, high_52w, percentile_rank, rs_score, sma
from app.domain.research import stats as S
from app.domain.research.classify import Tier, classify
from app.domain.research.costs import Account, LegInputs, commission, economics, ttf_rates
from app.domain.research.entries import absolute_orders
from app.domain.research.exits import CLOSED, REASONS, Bars, ExitSpec, Orders, simulate
from app.domain.research.panel import FeatureParams, IndexSeries, Panel, adj_factor_events, build_features
from app.domain.research.pipeline import (
    DenseExcess,
    Settings,
    add_excess,
    cell_signals,
    cell_stats,
    evaluate_cell,
    planted_power,
    prepare_group,
    raw_trigger,
    series_of,
    signal_rows,
    simulate_combo,
    weekly_matrix,
)
from app.domain.research.prereg import Cell, PreregError, check_ledger, check_unseal, file_sha256, parse_prereg
from app.domain.research.registration import run_registration
from app.domain.research.report import HEADLINE_NONE, ReportError, assert_no_price_fields, build_report, render_fr
from app.domain.risk import FeeSchedule

ROOT = Path(__file__).resolve().parents[2]
PREREG = ROOT / "research" / "prereg" / "2026-10-27_reg1.yaml"
CTO = FeeSchedule("saxo", 0.0008, 2.0)
PEA = FeeSchedule("boursobank", None, 0.0, 0.005, ((500.0, 1.99, 0.0, 0.0), (None, 0.0, 0.006, 0.0)))


# --- fixtures ---------------------------------------------------------------------------------------


def bdays(start: str, n: int) -> np.ndarray:
    d = np.arange(np.datetime64(start), np.datetime64(start) + n * 2, dtype="datetime64[D]")
    return d[((d.astype(np.int64) + 3) % 7) < 5][:n]


def synthetic_panel(n_inst=60, n_days=1500, seed=0, start="2016-01-04", sigma=0.02, group="P0", lr_extra=None):
    rng = np.random.default_rng(seed)
    days = bdays(start, n_days)
    lr = rng.normal(0, sigma, (n_days, n_inst))
    if lr_extra is not None:
        lr = lr + lr_extra
    close = 50 * np.exp(np.cumsum(lr, axis=0))
    op = np.vstack([close[:1], close[:-1]]) * np.exp(rng.normal(0, sigma / 3, (n_days, n_inst)))
    hi = np.maximum(op, close) * (1 + np.abs(rng.normal(0, sigma / 2, (n_days, n_inst))))
    lo = np.minimum(op, close) * (1 - np.abs(rng.normal(0, sigma / 2, (n_days, n_inst))))
    vol = rng.lognormal(12, 0.5, (n_days, n_inst))
    idx = IndexSeries(days, 4000 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, n_days))))
    z = np.zeros(n_inst)
    return Panel(group, days, tuple(f"XX{k:010d}" for k in range(n_inst)), op, hi, lo, close, close.copy(), vol,
                 np.ones((n_days, n_inst)), np.arange(n_inst) % 2 == 0, np.arange(n_inst) % 3 == 0,
                 np.ones(n_inst, dtype=bool), z.copy(), z.copy(), z.copy(), np.ones(n_inst, dtype=bool), idx, idx)  # fmt: skip


def settings() -> Settings:
    det = yaml.safe_load((ROOT / "config" / "params.example.yaml").read_text(encoding="utf-8"))["detectors"]
    return Settings(
        FeatureParams(1.0, 500_000, 0.5), 0.002, 300.0, 8000.0, 0.10, Account("cto", CTO, dividend_tax=0.314),
        Account("pea", PEA), Account("srd", CTO, srd_daily_rate=0.00023, dividend_tax=0.314), 0.02, 0.2, 0.002,
        0.03, 0.005, 0.25, 1.5, 2.0, 6, 6, 20261027, det,
    )  # fmt: skip


@pytest.fixture(scope="module")
def prereg():
    b = PREREG.read_bytes()
    return parse_prereg(yaml.safe_load(b), file_sha256(b))


def light(prereg):
    """The same plan with fewer resamples (unit-test speed); thresholds unchanged."""
    inf = {**prereg.inference, "bootstrap_draws": 300, "permutation_draws": 100, "power_reps": 10}
    return dataclasses.replace(prereg, inference=inf)


def custom_cell(h=20) -> Cell:
    return Cell(id="TEST", tier="exploratory", family="FX", trigger="t05", params={"k_sigma": 2.0}, h=h, exit="X3",
                cap_atr=1.0, validity=1)  # fmt: skip


# --- 1. a planted edge is found ----------------------------------------------------------------------


def _planted(edge_r: float, n_inst=300, n_days=1500, events=600, seed=5):
    rng = np.random.default_rng(seed)
    sigma, h = 0.02, 20
    move = edge_r * 2 * sigma * math.sqrt(h)  # R_ref unit = 2 σ √h
    extra = np.zeros((n_days, n_inst))
    mask = np.zeros((n_days, n_inst), dtype=bool)
    days = bdays("2016-01-04", n_days)
    lo = int(np.searchsorted(days, np.datetime64("2017-01-02")))
    hi = n_days - 100
    placed = 0
    while placed < events:
        t, i = int(rng.integers(lo, hi)), int(rng.integers(0, n_inst))
        if mask[max(0, t - 30) : t + 30, i].any():
            continue
        mask[t, i] = True
        extra[t + 2 : t + 2 + h, i] += math.log(1 + move) / h
        placed += 1
    return synthetic_panel(n_inst, n_days, seed, lr_extra=extra), mask


def test_planted_edge_passes_gate_effect_placebo_romano_wolf_and_power(prereg):
    pr = light(prereg)
    st = settings()
    p, mask = _planted(0.30)
    g = prepare_group(0, p, st)
    rows = signal_rows(p.days, *pr.segments.discovery, pr.segments.end_margin_sessions)
    cell = custom_cell()
    combo = simulate_combo(g, cell.combo_key, st, rows)
    tr = add_excess(evaluate_cell(g, cell, combo, st, rows, mask=mask), False)
    s = cell_stats(tr, cell, pr, st)
    assert s["n"] >= 400 and s["gate"] and s["t_week"] >= 3
    assert s["mean_r"] >= 0.10 and s["mean_excess_ctrl"] >= 0.08 and s["excess_ctrl_ic"][0] > 0  # A2, A4
    cl = tr.closed
    assert (cl["r"] <= cl["r_gross"] + 1e-12).all()  # costs never improve a trade
    assert (cl["r_stress"] <= cl["r"] + 1e-12).all()  # ×1.5 costs and slippage ×2: never better
    # Romano–Wolf with ten null cells beside it
    series = [series_of(cl, False)]
    rng = np.random.default_rng(1)
    for k in range(10):
        null_mask = rng.random(p.shape) < 0.002
        nt = add_excess(evaluate_cell(g, dataclasses.replace(cell, id=f"N{k}"), combo, st, rows, mask=null_mask), False)
        series.append(series_of(nt.closed, False))
    _, mat = weekly_matrix(series)
    _, p_rw = S.romano_wolf(mat, 4, 300, 7)
    assert p_rw[0] <= 0.05
    dense = {0: DenseExcess((combo.r - combo.ctrl)[rows[0] : rows[1] + 1].astype(np.float32), None,
                            g.f.stratum[rows[0] : rows[1] + 1], rows)}  # fmt: skip
    assert planted_power(cl, dense, False, 0.20, 10, 3, 3.0, 0.08, 4) >= 0.9


# --- 2. a random walk yields nothing ---------------------------------------------------------------------


@pytest.mark.parametrize("seed", [11, 12, 13])
def test_random_walk_gives_no_candidate(prereg, seed):
    pr = light(prereg)
    panels = [synthetic_panel(40, 2300, seed=seed, group="P0"), synthetic_panel(30, 2300, seed=seed + 100, group="P1")]
    res = run_registration(panels, pr, settings(), f0=False)
    tiers = [c["niveau"] for c in res["cellules"].values()]
    assert tiers.count(Tier.CANDIDATE.value) == 0 and tiers.count(Tier.WATCHLIST.value) <= 1
    rep = build_report(pr, res, {"limites": ["test"]})
    assert rep["A_resume"]["titre"] == HEADLINE_NONE
    assert len(res["cellules"]) == 8 + 118


# --- 3. no look-ahead ------------------------------------------------------------------------------------


def test_poisoned_future_changes_nothing_in_the_past(prereg):
    st = settings()
    p = synthetic_panel(50, 900, seed=3)
    t0 = 700
    rng = np.random.default_rng(9)
    k = rng.uniform(0.5, 1.5, size=p.close.shape)
    k[: t0 + 1] = 1.0
    hi_ = np.maximum(p.high * k, np.maximum(p.open * k, p.close * k))
    q = dataclasses.replace(p, open=p.open * k, high=hi_, low=p.low * k, close=p.close * k, adj_close=p.adj_close * k,
                            volume=p.volume * k)  # fmt: skip
    ga, gb = prepare_group(0, p, st), prepare_group(0, q, st)
    for fld in dataclasses.fields(ga.f):
        a, b = getattr(ga.f, fld.name), getattr(gb.f, fld.name)
        if isinstance(a, np.ndarray) and a.shape[:1] == (p.shape[0],):
            assert np.array_equal(a[: t0 + 1], b[: t0 + 1], equal_nan=True), fld.name
    for c in prereg.confirmatory + prereg.exploratory[::7]:
        ma, _ = raw_trigger(ga, c, st)
        mb, _ = raw_trigger(gb, c, st)
        assert np.array_equal(ma[: t0 + 1], mb[: t0 + 1]), c.id
        sa = cell_signals(ga, c, st, (300, t0))
        sb = cell_signals(gb, c, st, (300, t0))
        assert np.array_equal(sa.ts, sb.ts) and np.array_equal(sa.cols, sb.cols)
    # entries never before the next session; forward excess uses bars ≤ t + h only
    cell = custom_cell(5)
    combo = simulate_combo(ga, cell.combo_key, st, (300, 650))
    filled = combo.sim.fill_pos >= 0
    assert (combo.sim.fill_pos[filled] >= combo.od.sig_pos[filled] + 1).all()
    h = 20
    assert np.array_equal(ga.fwd(h)[: t0 - h], gb.fwd(h)[: t0 - h], equal_nan=True)


# --- 4. engine equivalence -----------------------------------------------------------------------------------


def _random_bars(n=500, length=40, seed=3):
    rng = np.random.default_rng(seed)
    c = 50 * np.exp(np.cumsum(rng.normal(0, 0.03, (length, n)), axis=0))
    o = np.vstack([c[:1], c[:-1]]) * np.exp(rng.normal(0, 0.02, (length, n)))
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.01, (length, n))))
    lo = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.01, (length, n))))
    lens = rng.integers(5, length + 1, n)
    for j in range(n):
        o[lens[j] :, j] = h[lens[j] :, j] = lo[lens[j] :, j] = c[lens[j] :, j] = np.nan
    z = np.zeros((length, n))
    return Bars(o, h, lo, c, z, z, z, z.astype(bool), lens.astype(np.int64)), rng


def test_vectorised_engine_equals_simulate_trade_on_random_brackets():
    b, rng = _random_bars()
    n = b.width
    limit = b.close[0] * rng.uniform(0.95, 1.03, n)
    stop = limit * rng.uniform(0.85, 0.99, n)
    target = limit * rng.uniform(1.01, 1.2, n)
    es, mh, slip = 3, 15, 0.002
    od = absolute_orders(np.arange(n), np.zeros(n, dtype=np.int64), limit, stop, target, es)
    res = simulate(
        b, od, ExitSpec("prod", mh, target_on_fill_bar="always", require_bracket=True), slip, dividends=False
    )
    closed = 0
    for j in range(n):
        bars = [Bar(b.open[k, j], b.high[k, j], b.low[k, j], b.close[k, j], 0) for k in range(1, int(b.n_bars[j]))]
        ref = simulate_trade(bars, limit[j], stop[j], target[j], slip, es, mh)
        if ref is None:
            assert res.status[j] != CLOSED
            continue
        closed += 1
        risk = res.risk_fraction[j]
        assert res.status[j] == CLOSED
        assert (res.fill_pos[j] - 1, res.exit_pos[j] - 1) == (ref.entry_index, ref.exit_index)
        assert REASONS[int(res.reason[j])] == ref.reason
        assert res.entry[j] == ref.entry and res.exit_px[j] == ref.exit
        assert (res.hi[j] / res.entry[j] - 1) / risk == ref.mfe_r and (res.lo[j] / res.entry[j] - 1) / risk == ref.mae_r
    assert closed > 200


def test_path_rules_differ_only_on_ambiguous_sessions():
    b, rng = _random_bars(seed=8)
    n = b.width
    limit = b.close[0] * 1.01
    od = Orders(np.arange(n), np.zeros(n, dtype=np.int64), limit, np.full(n, np.nan), np.full(n, 0.96), np.zeros(n),
                np.full(n, 1.04), np.zeros(n), np.full(n, np.nan), 1)  # fmt: skip
    spec = ExitSpec("x", 20)
    r = {rule: simulate(b, od, spec, 0.002, rule) for rule in ("stop_first", "ohlc", "target_first")}
    base = r["stop_first"]
    clear = (base.status == CLOSED) & ~base.ambiguous
    assert base.ambiguous.any() and clear.any()
    for rule in ("ohlc", "target_first"):
        assert np.array_equal(r[rule].exit_pos[clear], base.exit_pos[clear])
        assert np.array_equal(r[rule].exit_px[clear], base.exit_px[clear])
    amb = base.ambiguous & (base.status == CLOSED)
    assert (r["target_first"].exit_px[amb] >= base.exit_px[amb]).all()  # the optimism bound


# --- 5. vectorised features = scalar indicators -----------------------------------------------------------


def test_vectorised_features_match_the_scalar_indicators():
    st = settings()
    p = synthetic_panel(12, 600, seed=4)
    holes = p.close.copy()
    holes[100:110, 3] = np.nan  # an instrument without bars for a few sessions (own-session packing)
    p = dataclasses.replace(p, close=holes)
    f = build_features(p, st.features)
    for i in (0, 3, 7):
        ok = np.isfinite(p.close[:, i])
        rows = np.nonzero(ok)[0]
        bars = [Bar(p.open[t, i], p.high[t, i], p.low[t, i], p.close[t, i], int(p.volume[t, i])) for t in rows]
        closes = [x.close for x in bars]
        for k in (260, 400, len(rows) - 1):
            t = rows[k]
            assert f.sma50[t, i] == pytest.approx(sma(closes[: k + 1], 50), rel=1e-9)
            assert f.atr14[t, i] == pytest.approx(atr(bars[: k + 1], 14), rel=1e-9)
            assert f.rs_score[t, i] == pytest.approx(rs_score(closes[: k + 1]), rel=1e-9)
            assert f.high_250[t, i] == pytest.approx(high_52w(bars[: k + 1]), rel=1e-12)
    t = 500
    pop = [f.rs_score[t, j] for j in range(p.shape[1]) if f.elig[t, j] and np.isfinite(f.rs_score[t, j])]
    for j in range(p.shape[1]):
        if f.elig[t, j] and np.isfinite(f.rs_score[t, j]):
            assert f.rs_rank[t, j] == pytest.approx(percentile_rank(f.rs_score[t, j], pop), abs=1e-5)


# --- 6. dividends and corporate actions -------------------------------------------------------------------


def test_dividend_lowers_levels_and_is_credited_and_splits_are_flagged():
    st = settings()
    p = synthetic_panel(3, 80, seed=2, sigma=0.0001)
    adj = p.close.copy()
    adj[:40, 0] *= 1 - 1.0 / p.close[39, 0]  # a dividend of 1.0 detached on session 40
    adj[:60, 1] *= 0.5  # a 2:1 split on session 60 (factor halves)
    p = dataclasses.replace(p, adj_close=adj)
    f = build_features(p, st.features)
    ca, div = adj_factor_events(f.adj_factor, p.close, f.pk, 0.2, 0.002)
    assert div[40, 0] == pytest.approx(1.0, rel=1e-9) and div[:, 0].sum() == pytest.approx(1.0, rel=1e-9)
    assert ca[60, 1] and not ca[:, 0].any() and div[:, 1].sum() == 0
    g = prepare_group(0, p, st)
    od = Orders(np.array([0, 1]), np.array([35, 55]), np.full(2, 1e9), np.full(2, np.nan), np.full(2, 0.5),
                np.zeros(2), np.full(2, np.nan), np.full(2, np.nan), np.full(2, np.nan), 1)  # fmt: skip
    with_div = simulate(g.bars, od, ExitSpec("t", 10), 0.002, dividends=True)
    no_div = simulate(g.bars, od, ExitSpec("t", 10), 0.002, dividends=False)
    assert with_div.div_cash[0] == pytest.approx(1.0) and no_div.div_cash[0] == 0
    assert with_div.status[1] == 4  # corporate action inside the trade: excluded
    x = LegInputs(*(np.array([v]) for v in (50.0, 50.0, 45.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 10.0, 0.1)),
                  np.array([False]))  # fmt: skip
    acct = Account("cto", CTO, dividend_tax=0.314)
    gross, net, excl = (economics(x, acct, 300.0, 8000.0, div_rate=r).r_unit[0] for r in (1.0, 0.686, 0.0))
    assert gross > net > excl


# --- 7. costs ------------------------------------------------------------------------------------------------


def test_costs_reproduce_the_fee_schedules_and_ttf_scope():
    xs = np.array([100.0, 499.0, 500.0, 501.0, 1000.0, 2500.0, 8000.0, 20000.0])
    for fs in (CTO, PEA):
        assert np.allclose(commission(fs, xs), [fs.commission(float(v)) for v in xs])
    for fs, ttf in ((CTO, 0.004), (PEA, 0.0)):
        acct = Account("x", fs)
        entry, exit_, stop = 40.0, 44.0, 36.0
        x = LegInputs(*(np.array([v]) for v in (entry, exit_, stop, 0.0, 1.0, 1.0, ttf, 0.001, 0.0025, 0.0, 0.0, 0.0,
                                                 0.0, 0.1)), np.array([False]))  # fmt: skip
        e = economics(x, acct, 300.0, 1e9)
        cp = CostParams(fs, ttf_rate=ttf, ftt_rate=0.001, fx_fee_pct=0.0025)
        nb = float(e.notional_frac[0])
        shares = nb / entry
        expected = shares * (exit_ - entry) - buy_fees(nb, cp) - sell_fees(shares * exit_, cp)
        assert float(e.net_ret[0]) * nb == pytest.approx(expected, rel=1e-9)
        stress = economics(x, acct, 300.0, 1e9, fee_mult=1.5, spread_mult=2.0)
        assert stress.net_ret[0] < e.net_ret[0]
    rates = ttf_rates(["FR0000000001", "FR0000000002", "DE0000000003"], [2e9, 5e8, 9e9], 0.004, 1e9)
    assert list(rates) == [0.004, 0.0, 0.0]


# --- 8. statistics -------------------------------------------------------------------------------------------


def _nw_reference(x, lag):
    e = x - x.mean()
    n = len(x)
    v = sum(e[i] * e[i] for i in range(n)) / n
    for k in range(1, lag + 1):
        v += 2 * (1 - k / (lag + 1)) * sum(e[i] * e[i - k] for i in range(k, n)) / n
    return x.mean() / math.sqrt(v / n)


def test_statistics_reference_values_and_error_rates():
    rng = np.random.default_rng(0)
    x = np.zeros(400)
    for i in range(1, 400):
        x[i] = 0.6 * x[i - 1] + rng.normal()
    x += 0.1
    assert S.newey_west_t(x, 6) == pytest.approx(_nw_reference(x, 6), rel=1e-9)
    assert S.newey_west_se(x, 6) > x.std() / math.sqrt(x.size)  # positive autocorrelation widens the error
    p = np.array([0.01, 0.04, 0.03, 0.005])
    assert np.allclose(S.holm(p), [0.03, 0.06, 0.06, 0.02])
    assert np.allclose(S.bh(p), [0.02, 0.04, 0.04, 0.02])
    assert S.norm_ppf(0.975) == pytest.approx(1.959964, abs=1e-6)
    # Romano–Wolf controls the family-wise error on null data
    rej = 0
    for k in range(300):
        r = np.random.default_rng(100 + k)
        common = r.normal(size=(150, 1))
        mat = 0.5 * common + r.normal(size=(150, 10))
        _, padj = S.romano_wolf(mat, 4, 300, k)
        rej += int(padj.min() <= 0.05)
    assert rej / 300 <= 0.07
    # block bootstrap interval covers the mean about 95 % of the time
    cover = 0
    for k in range(200):
        y = np.random.default_rng(500 + k).normal(size=200)
        lo, hi = S.block_bootstrap_ci(y, 4, 400, k)
        cover += int(lo <= 0.0 <= hi)
    assert 0.88 <= cover / 200 <= 0.99
    assert S.deflated_sharpe(0.5, 1, 0.0, 200, 0.0, 3.0) > 0.99
    assert 0.0 <= S.pbo_cscv(np.random.default_rng(1).normal(size=(320, 8))) <= 1.0


# --- 9. pre-registration -------------------------------------------------------------------------------------


def test_preregistration_counts_hash_and_refusals(prereg):
    assert prereg.counts() == {"confirmatoires": 8, "exploratoires_etape0": 118, "etape1_plafond": 32,
                               "diagnostics_f0": 27, "etape2_plafond": 10, "total": 195}  # fmt: skip
    assert prereg.total_cells <= prereg.max_total_cells == 400
    b = PREREG.read_bytes()
    again = parse_prereg(yaml.safe_load(b), file_sha256(b))
    assert again.sha256 == prereg.sha256 and again.cell_set_hash == prereg.cell_set_hash
    check_ledger([{"kind": "run", "prereg_sha256": prereg.sha256, "cell_set_sha256": prereg.cell_set_hash}], prereg)
    with pytest.raises(PreregError):
        check_ledger([{"kind": "run", "prereg_sha256": prereg.sha256, "cell_set_sha256": "autre"}], prereg)
    with pytest.raises(PreregError):
        check_unseal(None, None, prereg, [])
    frozen = {"prereg_sha256": prereg.sha256, "cells": ["F2.t08.gap_min0.03.cap_atr1.h20"]}
    assert check_unseal(frozen, "abc", prereg, []) == frozen["cells"]
    with pytest.raises(PreregError):  # the sealed period is opened once
        check_unseal(frozen, "abc", prereg, [{"kind": "unseal", "prereg_sha256": prereg.sha256}])
    with pytest.raises(PreregError):
        check_unseal({**frozen, "prereg_sha256": "x"}, "abc", prereg, [])
    raw = yaml.safe_load(b)
    raw["expected_total_cells"] = 203
    with pytest.raises(PreregError):
        parse_prereg(raw, "x")


# --- 10. report ------------------------------------------------------------------------------------------------


def test_report_suppresses_small_cells_and_refuses_price_fields(prereg):
    cells = {
        "F2.a": {"id": "F2.a", "famille": "F2", "tier": "exploratory", "n": 12, "mean_r": 0.5, "niveau": "REJET"},
        "F2.b": {"id": "F2.b", "famille": "F2", "tier": "exploratory", "n": 250, "mean_r": 0.05, "t_week": 1.2,
                 "niveau": "REJET", "_sr": 0.1},
    }  # fmt: skip
    rep = build_report(prereg, {"cellules": cells, "candidats": []}, {"debut_donnees": "2016-01-04", "limites": ["x"]})
    rows = {r["id"]: r for r in rep["C_etape0_par_famille"]["F2"]}
    assert "supprime" in rows["F2.a"] and "mean_r" not in rows["F2.a"] and rows["F2.b"]["mean_r"] == 0.05
    assert "_sr" not in str(rep)
    txt = render_fr(rep)
    assert HEADLINE_NONE in txt and prereg.sha256 in txt and "2016-01-04" in txt
    with pytest.raises(ReportError):
        assert_no_price_fields({"cellules": [{"n": 30, "entry": 12.3}]})


def test_classification_tiers():
    th = yaml.safe_load(PREREG.read_text(encoding="utf-8"))["thresholds"]
    good = {
        "tier": "exploratory", "declencheur": "t08", "mode": "event", "n": 900, "semaines": 300, "pli_min_n": 60,
        "oos": {"mean_r": 0.2, "mean_eur_300": 60.0, "profit_factor": 1.4, "mean_pct_notionnel": 0.01},
        "p_romano_wolf": 0.01, "t_week": 4.0, "dsr": 0.99, "pbo_famille": 0.1, "gate": True, "mean_excess_ctrl": 0.15,
        "excess_ctrl_ic": [0.05, 0.25], "p_permutation": 0.002, "plis_positifs": 5, "mean_r_stress": 0.1,
        "regime": {"t13_vrai": {"mean_r": 0.2}, "t13_faux": {"mean_r": 0.1}}, "dividendes": {"fragile": False},
        "survie": {"multiple_risque_equilibre": 5.0}, "mean_r": 0.2,
        "concentration": {"part_top10_valeurs": 0.2, "mean_r_hors_top5_valeurs": 0.15},
        "capacite": {"oos": {"mean_r": 0.2, "mean_eur_300": 60.0, "profit_factor": 1.4, "mean_pct_notionnel": 0.01}},
        "h": 20, "duree_max": 20, "ambigu": False, "portefeuille_ok": True, "q_bh": 0.01,
    }  # fmt: skip
    assert classify(good, th, "all")[0] == Tier.CANDIDATE
    assert classify({**good, "declencheur": "d2x"}, th, "all")[0] == Tier.WATCHLIST  # D2x: watchlist at most
    assert classify(good, th, "in_universe")[0] == Tier.WATCHLIST  # survivorship not testable: no candidate
    assert classify({**good, "plis_positifs": 2}, th, "all")[0] == Tier.REJECT
    assert classify({"tier": "f0"}, th, "all")[0] == Tier.DIAGNOSTIC
