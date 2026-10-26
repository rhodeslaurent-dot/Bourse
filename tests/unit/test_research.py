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
from app.domain.research import triggers as T
from app.domain.research.classify import Tier, classify
from app.domain.research.costs import Account, LegInputs, commission, economics, ttf_rates
from app.domain.research.entries import absolute_orders
from app.domain.research.exits import (
    CLOSED,
    COND_TIME,
    DELISTED,
    DIVIDEND_SUSPECT,
    REASONS,
    STOP,
    TARGET,
    TIME,
    UNKNOWN,
    Bars,
    ExitSpec,
    Orders,
    SimResult,
    simulate,
)
from app.domain.research.panel import (
    FeatureParams,
    IndexSeries,
    Panel,
    adj_factor_events,
    build_features,
    make_packing,
)
from app.domain.research.pipeline import (
    DenseExcess,
    Settings,
    Trades,
    add_excess,
    cell_model,
    cell_signals,
    cell_stats,
    evaluate_cell,
    family_walk_forward,
    legs,
    masked_signal_count,
    planted_power,
    prepare_group,
    raw_trigger,
    sanitize_dividends,
    series_of,
    signal_rows,
    simulate_combo,
    walk_forward_choice,
    weekly_matrix,
)
from app.domain.research.prereg import Cell, PreregError, check_ledger, check_unseal, file_sha256, parse_prereg
from app.domain.research.registration import (
    exploratory_romano_wolf,
    portfolio_slots,
    run_registration,
    stage1_stats,
)
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
    st = dataclasses.replace(settings(), universe_label="all")  # A9 evaluable: stage 2 reachable
    res = run_registration(panels, pr, st, f0=False)
    tiers = [c["niveau"] for c in res["cellules"].values()]
    assert all("A9" in c["criteres"] for c in res["cellules"].values())
    assert tiers.count(Tier.CANDIDATE.value) == 0 and tiers.count(Tier.WATCHLIST.value) <= 1
    rep = build_report(pr, res, {"limites": ["test"]})
    assert rep["A_resume"]["titre"] == HEADLINE_NONE
    assert len(res["cellules"]) == 8 + 118
    # inference design: 13-week blocks (63-session holds), the power judged at the critical t that decides
    rw = res["romano_wolf"]
    assert rw["bloc_semaines"] == 13 and rw["lags_semaines"]["F1.t01.rank_min80.h63.gatenone"] == 13
    assert rw["t_max_critique_5pct"] is not None and res["lignes_portefeuille"] == 6
    for fam in ("F1", "F2", "F3", "F5"):
        assert res["familles"][fam]["seuil_t_puissance"] == pytest.approx(max(3.0, rw["t_max_critique_5pct"]), abs=2e-3)
        assert "borne supérieure" in rep["entete"]["puissance_effet_plante"][fam]["nature"]
    for h in range(1, 9):
        assert res["familles"][f"H{h}"]["seuil_t_puissance"] == pytest.approx(S.norm_ppf(1 - 0.05 / 8), abs=1e-3)


# --- 3. no look-ahead ------------------------------------------------------------------------------------


def test_poisoned_future_changes_nothing_in_the_past(prereg):
    st = settings()
    p = synthetic_panel(50, 900, seed=3)
    t0 = 700
    rng = np.random.default_rng(9)
    k = rng.uniform(0.5, 1.5, size=p.close.shape)
    k[: t0 + 1] = 1.0
    k_adj = rng.uniform(0.3, 1.7, size=p.close.shape)  # the adjusted close gets its own factor: fake CAs/dividends
    k_adj[: t0 + 1] = 1.0
    hi_ = np.maximum(p.high * k, np.maximum(p.open * k, p.close * k))
    k_idx = np.where(np.arange(p.shape[0]) > t0, rng.uniform(0.5, 1.5, p.shape[0]), 1.0)  # and the regime indices
    idx = IndexSeries(p.index_cac.days, p.index_cac.close * k_idx)
    q = dataclasses.replace(p, open=p.open * k, high=hi_, low=p.low * k, close=p.close * k,
                            adj_close=p.adj_close * k_adj, volume=p.volume * k, index_cac=idx, index_stoxx=idx)  # fmt: skip
    ga, gb = prepare_group(0, p, st), prepare_group(0, q, st)
    assert gb.quality["operations_sur_titres"] > 0  # the poisoned factor did create events after t0
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
    closed = intraday = 0
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
        assert (res.lo[j] / res.entry[j] - 1) / risk == ref.mae_r
        if res.at_open[j]:
            assert (res.hi[j] / res.entry[j] - 1) / risk == ref.mfe_r
        else:  # intraday fill: the high of the fill session may precede the fill and is not counted
            f0, x0 = ref.entry_index, ref.exit_index
            hi = max([ref.entry] + [bars[k].high for k in range(f0 + 1, x0 + 1)])
            assert res.hi[j] == hi and res.hi[j] / res.entry[j] - 1 <= ref.mfe_r * risk + 1e-15
            intraday += 1
    assert closed > 200 and intraday > 20


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
    cid = "F2.t08.gap_min0.03.cap_atr1.h20"
    frozen = {"prereg_sha256": prereg.sha256, "cells": [cid], "niveaux": {cid: "CANDIDAT"}}
    run_row = {"kind": "run", "prereg_sha256": prereg.sha256, "cell_set_sha256": prereg.cell_set_hash,
               "candidates_sha256": "abc", "candidate_cells": cid}  # fmt: skip
    assert check_unseal(frozen, "abc", prereg, [run_row]) == frozen["cells"]
    with pytest.raises(PreregError):  # no ledgered run wrote it
        check_unseal(frozen, "abc", prereg, [])
    with pytest.raises(PreregError):  # the sealed period is opened once
        check_unseal(frozen, "abc", prereg, [run_row, {"kind": "unseal", "prereg_sha256": prereg.sha256}])
    with pytest.raises(PreregError):
        check_unseal({**frozen, "prereg_sha256": "x"}, "abc", prereg, [run_row])
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


OOS_GOOD = {"mean_r": 0.2, "mean_eur_300": 60.0, "profit_factor": 1.4, "mean_pct_notionnel": 0.01}


def test_classification_tiers():
    th = yaml.safe_load(PREREG.read_text(encoding="utf-8"))["thresholds"]
    good = {
        "tier": "exploratory", "declencheur": "t08", "mode": "event", "n": 900, "semaines": 300, "pli_min_n": 60,
        "oos_sous_ensemble_cellule": OOS_GOOD, "plis_positifs_sous_ensemble_cellule": 5,
        "walk_forward_famille": {"choix_cellule": True, "oos": OOS_GOOD, "oos_capacite": OOS_GOOD, "plis_positifs": 5},
        "p_romano_wolf": 0.01, "t_week": 4.0, "dsr": 0.99, "pbo_famille": 0.1, "gate": True, "mean_excess_ctrl": 0.15,
        "excess_ctrl_ic": [0.05, 0.25], "p_permutation": 0.002, "mean_r_stress": 0.1,
        "regime": {"t13_vrai": {"mean_r": 0.2}, "t13_faux": {"mean_r": 0.1}}, "dividendes": {"fragile": False},
        "survie": {"multiple_risque_equilibre": 5.0, "mean_r_radiees_perte_totale": 0.15}, "mean_r": 0.2,
        "concentration": {"part_top10_valeurs": 0.2, "mean_r_hors_top5_valeurs": 0.15},
        "capacite": {"oos": OOS_GOOD},
        "h": 20, "duree_max": 20, "ambigu": False, "portefeuille_ok": True, "q_bh": 0.01,
    }  # fmt: skip
    assert classify(good, th, "all")[0] == Tier.CANDIDATE
    assert classify({**good, "declencheur": "d2x"}, th, "all")[0] == Tier.WATCHLIST  # D2x: watchlist at most
    assert classify(good, th, "in_universe")[0] == Tier.WATCHLIST  # survivorship not testable: no candidate
    wf = good["walk_forward_famille"]
    assert classify({**good, "walk_forward_famille": {**wf, "plis_positifs": 2}}, th, "all")[0] == Tier.REJECT
    # A2 / A5 / A11 of an exploratory cell: never its own test years (also seen by the selection across the
    # family) — a perfect per-cell subset is not enough when the cell is not the walk-forward choice ...
    not_chosen = {**good, "oos": OOS_GOOD, "plis_positifs": 5, "walk_forward_famille": {**wf, "choix_cellule": False}}
    tier, crit = classify(not_chosen, th, "all")
    assert not (crit["A2"] or crit["A5"] or crit["A11"]) and tier == Tier.REJECT
    # ... nor when the family's walk-forward series misses the effect thresholds
    weak = {**OOS_GOOD, "mean_eur_300": 20.0}
    assert not classify({**good, "walk_forward_famille": {**wf, "oos": weak}}, th, "all")[1]["A2"]
    # a confirmatory hypothesis (no selection) keeps its own out-of-sample years
    conf = {**good, "tier": "confirmatory", "oos": OOS_GOOD, "plis_positifs": 5, "p_holm": 0.01}
    conf.pop("walk_forward_famille")
    assert all(classify(conf, th, "all")[1][k] for k in ("A2", "A5", "A11"))
    assert classify({"tier": "f0"}, th, "all")[0] == Tier.DIAGNOSTIC
    # A9: the cell must stay positive when every exit on an interrupted listing loses 100 % of the notional
    lost = {**good, "survie": {"multiple_risque_equilibre": 5.0, "mean_r_radiees_perte_totale": -0.02}}
    tier, crit = classify(lost, th, "all")
    assert not crit["A9"] and tier == Tier.WATCHLIST
    assert not classify({**good, "survie": {"multiple_risque_equilibre": 5.0}}, th, "all")[1]["A9"]


# --- 11. review fixes, pass A (exits, costs, panel) ---------------------------------------------------------


def _bars(o, h, lo, c, ended=None):
    o, h, lo, c = (np.asarray(x, dtype=np.float64) for x in (o, h, lo, c))
    z = np.zeros_like(c)
    n_bars = np.isfinite(c).sum(axis=0).astype(np.int64)
    return Bars(o, h, lo, c, z, z, z, z.astype(bool), n_bars, None if ended is None else np.asarray(ended))


def _abs_orders(n, limit, stop, target, cond=np.nan):
    return Orders(np.arange(n), np.zeros(n, dtype=np.int64), np.full(n, float(limit)), np.full(n, np.nan),
                  np.zeros(n), np.full(n, -float(stop)), np.zeros(n), np.full(n, float(target)),
                  np.full(n, float(cond)), 1)  # fmt: skip


def test_history_ending_during_the_hold_is_a_counted_delisted_exit():
    c = np.full((40, 2), 50.0)
    c[10:, :] = np.nan  # both histories stop after 10 bars
    b = _bars(c, c * 1.01, c * 0.99, c, ended=[True, False])
    res = simulate(b, _abs_orders(2, 1e9, 1.0, np.inf), ExitSpec("t", 20), 0.002)
    assert res.status[0] == CLOSED and res.reason[0] == DELISTED and REASONS[DELISTED] == "delisted"
    assert res.exit_pos[0] == 9 and res.exit_px[0] == 50.0  # the last available close
    assert res.status[1] == UNKNOWN  # history stopped by the end of the data: never counted


def test_delisted_trades_are_counted_flagged_and_stressed_at_a_total_loss(prereg):
    st = settings()
    p = synthetic_panel(40, 900, seed=21)
    cut = np.array([600 + 3 * k for k in range(10)])
    close, op, hi, lo = p.close.copy(), p.open.copy(), p.high.copy(), p.low.copy()
    for k, t in enumerate(cut.tolist()):
        for a in (close, op, hi, lo):
            a[t:, k] = np.nan  # instruments 0–9 delisted inside the panel
    p = dataclasses.replace(p, close=close, open=op, high=hi, low=lo, adj_close=close.copy())
    g = prepare_group(0, p, st)
    assert g.bars.ended[:10].all() and not g.bars.ended[10:].any()
    mask = np.zeros(p.shape, dtype=bool)
    for k, t in enumerate(cut.tolist()):
        mask[t - 8, k] = True  # a signal 8 sessions before the history ends (hold 20)
    mask[500, 20:30] = True
    cell = custom_cell(20)
    combo = simulate_combo(g, cell.combo_key, st, (300, 700))
    tr = evaluate_cell(g, cell, combo, st, (300, 700), mask=mask)
    dl = tr["delisted"]
    assert dl.sum() >= 8 and tr["closed"][dl].all() and np.isfinite(tr["r"][dl]).all()
    assert (tr["r_delist_loss"][dl] < tr["r"][dl] - 0.5).all()  # −100 % of the notional
    keep = tr["closed"] & ~dl
    assert np.allclose(tr["r_delist_loss"][keep], tr["r"][keep])
    s = cell_stats(add_excess(tr, False), cell, light(prereg), st)
    assert s["sorties_cotation_interrompue"] == int(dl.sum())
    assert s["survie"]["mean_r_radiees_perte_totale"] < s["mean_r"]


def test_engine_slipped_exits_pay_the_exit_spread_once():
    st = settings()
    p = synthetic_panel(5, 600, seed=4)
    g = prepare_group(0, p, st)
    slip, px = st.slippage, 52.0
    cols = np.array([0, 0, 0])
    t_sig = int(g.f.pk.order[299, 0])
    ts = np.full(3, t_sig)
    a = lambda *v: np.array(v, dtype=np.float64)  # noqa: E731
    sim = SimResult(
        np.full(3, CLOSED, dtype=np.int8), np.full(3, 300), np.full(3, 310), a(50, 50, 50),
        a(px * (1 - slip), px, px * (1 - slip)), a(45, 45, 45), a(45 * (1 - slip), 45 * (1 - slip), 45 * (1 - slip)),
        a(np.nan, np.nan, np.nan), a(55, 55, 55), a(44, 44, 44), a(0, 0, 0), np.array([STOP, TIME, 1], dtype=np.int8),
        np.zeros(3, dtype=bool), np.ones(3, dtype=bool),
    )  # fmt: skip
    model = cell_model(custom_cell(20))
    no_fee = Account("x", FeeSchedule("x", None, 0.0))  # spread only (fees follow the actual proceeds)
    for hs in (None, 0.006):  # the floor (= slippage) and a wider spread
        if hs is not None:
            g.hs[:, 0] = hs
        lg = legs(g, ts, cols, sim, model, 0.0, slip)
        e = economics(lg, no_fee, 300.0, 1e9)
        # the same exit cost (slip in the price + spread leg) for a stop, a gap and a close exit at the same price
        assert e.net_ret[0] == pytest.approx(e.net_ret[1], rel=1e-12)
        assert e.net_ret[2] == pytest.approx(e.net_ret[1], rel=1e-12)
        twice = economics(dataclasses.replace(lg, exit_slip=None), no_fee, 300.0, 1e9)  # the old double charge
        assert twice.net_ret[0] < twice.net_ret[1] - 0.9 * slip * px / 50.0


def test_intraday_fill_session_is_stop_first_and_its_high_is_not_an_excursion():
    # fill session: open 105 above the limit 100, high 112, low 95 → filled at 100 intraday
    o = np.array([[100.0] * 3, [105.0] * 3, [101.0] * 3, [101.0] * 3])
    h = np.array([[100.0] * 3, [112.0] * 3, [101.5] * 3, [101.5] * 3])
    lo = np.array([[100.0] * 3, [95.0, 95.0, 99.0], [100.5] * 3, [100.5] * 3])
    c = np.array([[100.0] * 3, [100.0, 100.0, 100.5], [101.0] * 3, [101.0] * 3])
    b = _bars(o, h, lo, c)
    od = _abs_orders(3, 100.0, 96.0, 110.0)
    always = ExitSpec("p", 3, target_on_fill_bar="always")
    for rule in ("ohlc", "target_first"):  # the pre-fill open no longer decides the order after the fill
        r = simulate(b, od, always, 0.002, rule)
        assert r.entry[0] == 100.0 and not r.at_open[0]
        assert r.reason[0] == STOP and r.exit_px[0] == pytest.approx(96.0 * 0.998) and r.ambiguous[0]
    # an open fill keeps the path rules (the stop and target are both after the fill)
    o2 = o.copy()
    o2[1] = 99.0
    r = simulate(_bars(o2, h, lo, c), od, always, 0.002, "target_first")
    assert r.at_open[0] and r.reason[0] == TARGET
    # MFE / X6: the fill-session high (112) may precede the fill → the excursion starts from the entry
    x6 = simulate(b, _abs_orders(3, 100.0, 96.0, np.inf, cond=0.05), ExitSpec("x6", 3, cond_session=1), 0.002)
    assert x6.reason[2] == COND_TIME and x6.exit_pos[2] == 1 and x6.hi[2] == 100.0 and x6.lo[2] == 99.0


def test_vintage_boundaries_create_no_dividend_and_mass_jumps_are_flagged():
    st = settings()
    p = synthetic_panel(30, 400, seed=6, sigma=0.0001)
    adj = p.close.copy()
    adj[:200] *= 0.97  # first download: adjusted for a dividend paid before the second download
    adj[200:300, 0] *= 1 - 1.0 / p.close[299, 0]  # a real dividend of 1.0 inside the second vintage (row 300)
    vintage = np.where(np.arange(400)[:, None] < 200, 0, 1) * np.ones((1, 30), dtype=np.int64)
    g = prepare_group(0, dataclasses.replace(p, adj_close=adj, vintage=vintage), st)
    assert np.allclose(g.bars.div.sum(axis=0)[1:], 0.0) and g.bars.div[:, 0].sum() == pytest.approx(1.0, rel=1e-6)
    assert g.quality["frontieres_millesime_neutralisees"] == 30 and g.quality["jours_sauts_de_masse"] == 0
    assert abs(g.f.ret5[203, 5]) < 0.005  # no fake return across the boundary
    # without the vintages the boundary looks like a dividend on every instrument: flagged by the safety net
    g0 = prepare_group(0, dataclasses.replace(p, adj_close=adj), st)
    assert (g0.bars.div[200] > 0).all() and g0.quality["jours_sauts_de_masse"] == 1
    assert g0.quality["evenements_jours_sauts_de_masse"] == 30
    # a split on the boundary day stays a corporate action
    q = dataclasses.replace(p, adj_close=adj, vintage=vintage)
    sp = {k: getattr(q, k).copy() for k in ("open", "high", "low", "close")}
    for a in sp.values():
        a[200:, 3] /= 2
    q = dataclasses.replace(q, **sp)
    adj_q = q.adj_close.copy()
    adj_q[200:, 3] = q.close[200:, 3] * 2 * 1.0  # adjusted series continuous (factor 1 → 2)
    adj_q[:200, 3] = q.close[:200, 3] * 0.97
    gq = prepare_group(0, dataclasses.replace(q, adj_close=adj_q), st)
    assert gq.bars.ca[200, 3]


def test_corporate_action_masks_eligibility_for_the_feature_lookback():
    st = settings()
    drift = np.full((900, 40), 0.0)
    drift[:, 0] = 0.002
    p = synthetic_panel(40, 900, seed=8, lr_extra=drift)
    sp = {k: getattr(p, k).copy() for k in ("open", "high", "low", "close")}
    for a in sp.values():
        a[500:, 0] *= 2  # a 1-for-2 reverse split on row 500; the adjusted series stays continuous
    p = dataclasses.replace(p, adj_close=p.close.copy(), **sp)
    g = prepare_group(0, p, st)
    assert g.bars.ca[500, 0]
    assert not g.f.elig[500:752, 0].any() and g.f.ca_masked[500:752, 0].sum() > 200
    assert g.f.elig[752:, 0].any() and g.f.elig[300:500, 0].any()
    assert np.isfinite(g.f.rs_rank[500:752, 0]).sum() > 200  # ranked against the day's population (counting)
    cell = dataclasses.replace(custom_cell(5), params={"k_sigma": -100.0})  # ≈ every eligible T04 day
    sig = cell_signals(g, cell, st, (300, 800))
    assert not ((sig.cols == 0) & (sig.ts >= 500) & (sig.ts < 752)).any()
    assert masked_signal_count(g, cell, st, (300, 800)) > 50
    p0 = dataclasses.replace(p, adj_close=p.close.copy())  # no factor jump: nothing masked
    assert not prepare_group(0, p0, st).f.ca_masked.any()


def test_dividend_sanity_uses_a_trailing_yield_and_excludes_the_instrument_year(prereg):
    days = bdays("2016-01-04", 700)
    year = days.astype("datetime64[Y]").astype(np.int64) + 1970
    close = np.full((700, 2), 10.0)
    div = np.zeros((700, 2))
    r = {d: int(np.searchsorted(days, np.datetime64(d))) for d in ("2016-05-02", "2016-12-15", "2017-01-16",
                                                                      "2017-05-02", "2017-12-01")}  # fmt: skip
    div[r["2016-05-02"], 0] = 0.3
    div[r["2017-05-02"], 0] = 2.0  # 20 %: rejected
    div[r["2017-12-01"], 0] = 0.3  # judged on the accepted trailing amounts only: kept
    div[r["2016-12-15"], 1] = 0.7  # 7 % in 2016 and 7 % in 2017, but 14 % over the trailing year
    div[r["2017-01-16"], 1] = 0.7
    pk = make_packing(np.ones((700, 2), dtype=bool))
    out, unreliable, years, rejected = sanitize_dividends(div, close, pk, year, 0.12, 252)
    assert out[r["2016-05-02"], 0] == 0.3 and out[r["2017-05-02"], 0] == 0.0 and out[r["2017-12-01"], 0] == 0.3
    assert out[r["2016-12-15"], 1] == 0.7 and out[r["2017-01-16"], 1] == 0.0
    assert rejected == 2 and years == 2
    assert unreliable[:, 0].sum() == (year == 2017).sum() and not unreliable[year == 2016].any()
    # the marking never crosses the seal: a rejection after it leaves the same year's earlier rows usable
    seal = r["2017-01-16"] + 30
    _, unr2, years2, _ = sanitize_dividends(div, close, pk, year, 0.12, 252, seal)
    assert years2 == 2 and not unr2[:seal, 0].any() and unr2[seal:, 0].sum() == ((year == 2017)[seal:]).sum()
    assert unr2[(year == 2017) & (np.arange(700) < seal), 1].all()  # the January rejection stays before it
    # the trades of an unreliable instrument-year are excluded and counted
    st = settings()
    p = synthetic_panel(30, 900, seed=9, sigma=0.0005)
    adj = p.close.copy()
    t_div = int(np.searchsorted(p.days, np.datetime64("2018-06-01")))
    adj[:t_div, 0] *= 0.85  # a 15 % « dividend » (below the corporate-action jump): not trusted
    g = prepare_group(0, dataclasses.replace(p, adj_close=adj), st)
    assert g.suspect_dividend_years == 1 and g.bars.div[:, 0].sum() == 0.0
    mask = np.zeros(p.shape, dtype=bool)
    t18 = int(np.searchsorted(p.days, np.datetime64("2018-02-01")))
    t17 = int(np.searchsorted(p.days, np.datetime64("2017-06-01")))
    mask[[t17, t18], 0] = True
    cell = custom_cell(5)
    combo = simulate_combo(g, cell.combo_key, st, (300, 800))
    tr = evaluate_cell(g, cell, combo, st, (300, 800), mask=mask)
    st_ = dict(zip(tr["t"].tolist(), tr["status"].tolist(), strict=True))
    assert st_[t18] == DIVIDEND_SUSPECT and st_[t17] == CLOSED
    assert cell_stats(add_excess(tr, False), cell, light(prereg), st)["exclus_dividendes_douteux"] == 1
    assert not np.isfinite(combo.r[t18 - 5 : t18 + 5, 0]).any()  # the controls exclude them too


# --- 12. review fixes, pass B (inference and selection) -----------------------------------------------------


def _null_overlap_matrix(k: int, weeks: int = 380, m: int = 6, h: int = 63) -> np.ndarray:
    """Weekly series of ``m`` correlated null cells, one trade a week held ``h`` sessions (overlapping holds:
    an MA(⌈h/5⌉ − 1) weekly series), 10 % of the weeks without a trade."""
    r = np.random.default_rng(1000 + k)
    days = weeks * 5 + h + 5
    d = 0.6 * r.normal(size=(days, 1)) + 0.8 * r.normal(size=(days, m))
    cs = np.vstack([np.zeros((1, m)), np.cumsum(d, axis=0)])
    starts = np.arange(weeks) * 5
    mat = cs[starts + h] - cs[starts]
    mat[r.random(mat.shape) < 0.1] = np.nan
    return mat


def test_romano_wolf_uses_each_cells_lag_and_hold_long_blocks_on_overlapping_holds(prereg):
    pr = dataclasses.replace(prereg, inference={**prereg.inference, "bootstrap_draws": 200})
    cells = [Cell(id=f"N{j}", tier="exploratory", family="FX", trigger="t08", h=63, exit="X3", cap_atr=1.0)
             for j in range(6)]  # fmt: skip
    new = old = 0
    sims = 150
    for k in range(sims):
        mat = _null_overlap_matrix(k)
        trades = {}
        for j, c in enumerate(cells):
            ok = np.isfinite(mat[:, j])
            n = int(ok.sum())
            trades[c.id] = Trades({"closed": np.ones(n, dtype=bool), "week": 2500 + np.nonzero(ok)[0],
                                   "excess": mat[ok, j], "hold": np.full(n, 63, dtype=np.int64)})  # fmt: skip
        rw, wmat = exploratory_romano_wolf(cells, trades, pr)
        if k == 0:  # the design: 13-week lag of each 63-session cell, 13-week blocks (not 4 / 4)
            assert rw.block == 13 and rw.lags.tolist() == [13] * 6 and np.isfinite(rw.max_t_crit)
        new += int(rw.p_adj.min() <= 0.05)
        old += int(S.romano_wolf(wmat, 4, 200, pr.seed)[1].min() <= 0.05)  # the former wiring: lag 4, block 4
    assert new / sims <= 0.08  # ≈ 5 % family-wise error on overlapping null holds
    assert old / sims >= 0.15  # the fixed lag of 4 weeks over-rejects (≈ 24 %)


def _s1_trades(base: np.ndarray, shift: float, seed: int) -> Trades:
    n = base.size
    day0 = np.datetime64("2017-01-02", "D").astype(np.int64)
    day = day0 + (np.arange(n) * 7) // 2
    r = base + shift + np.random.default_rng(seed).normal(0, 0.02, n)
    return Trades({"inst": np.arange(n, dtype=np.int64), "t": np.arange(n, dtype=np.int64), "closed": np.ones(n, dtype=bool),
                   "r": r, "eur": r * 300.0, "r_stress": r - 0.05, "hazard": np.zeros(n), "day": day,
                   "week": (day + 3) // 7, "entry_day": day + 1, "exit_day": day + 20})  # fmt: skip


def test_stage1_pairs_each_path_rule_with_the_same_rules_x3_and_checks_the_ranks(prereg):
    pr = light(prereg)
    base = np.random.default_rng(3).normal(0, 1.0, 1400)
    # the path rules lift X3 itself (targets reached first): +0.3 under ohlc, +0.6 under target_first
    lift = {"stop_first": 0.0, "ohlc": 0.3, "target_first": 0.6}
    variants = {
        "X3": {rule: _s1_trades(base, lift[rule], 1) for rule in lift},
        "X7": {rule: _s1_trades(base, lift[rule] + 0.3, 2) for rule in lift},  # better under the three rules
        # better than X3 stop-first under every rule, but worse than X3 of the same rule under ohlc/target_first
        "X5": {"stop_first": _s1_trades(base, 0.2, 3), "ohlc": _s1_trades(base, 0.25, 4),
               "target_first": _s1_trades(base, 0.3, 5)},
    }  # fmt: skip
    out = stage1_stats(custom_cell(), variants, pr, 6)
    x7, x5 = out["X7"], out["X5"]
    assert x7["retenue"] and x7["stable_regles_parcours"] and x7["rang_par_regle"] == dict.fromkeys(lift, 1)
    assert x7["delta_r_par_regle"]["target_first"] == pytest.approx(0.3, abs=0.01)
    assert x5["delta_r_par_regle"]["target_first"] == pytest.approx(-0.3, abs=0.01)
    assert not x5["signe_stable"] and not x5["rang_stable"] and not x5["stable_regles_parcours"]
    assert not x5["retenue"]  # paired with X3 stop-first under every rule, it looked stable and was kept
    assert out["X3"]["signe_stable"] and not out["X3"]["retenue"]  # the reference itself: ΔR = 0 under each rule
    # bootstrap blocks cover the longest hold: X7 / X3(20) → 4 weeks, X5 (up to 60 sessions) → 12 weeks
    assert x7["bloc_bootstrap_semaines"] == 4 and x5["bloc_bootstrap_semaines"] == 12


def _wf_trades(excess_by_year: dict[int, float], r_test: float) -> Trades:
    days = np.arange(np.datetime64("2017-01-02"), np.datetime64("2024-07-31"), 3).astype("datetime64[D]")
    years = days.astype("datetime64[Y]").astype(np.int64) + 1970
    ex = np.array([excess_by_year[int(y)] for y in years])
    n = days.size
    r = np.where(years >= 2020, r_test, 0.0) + np.random.default_rng(int(ex.sum() * 100) % 97).normal(0, 0.01, n)
    return Trades({"day": days.astype(np.int64), "excess": ex, "r": r, "eur": r * 300.0, "pct": r * 0.02,
                   "closed": np.ones(n, dtype=bool), "r_pea": r, "eur_pea": r * 300.0,
                   "capacity_ok": np.ones(n, dtype=bool)})  # fmt: skip


def test_family_walk_forward_is_the_only_out_of_sample_series_of_an_exploratory_cell(prereg):
    years = range(2017, 2025)
    a = Cell(id="A", tier="exploratory", family="FX", trigger="t08", cap_atr=1.0)
    b = Cell(id="B", tier="exploratory", family="FX", trigger="t08", cap_atr=1.0)
    # A always wins the training years; B is the better variant on the test years (its own subset passes A2)
    trades = {"A": _wf_trades(dict.fromkeys(years, 0.2), 0.15), "B": _wf_trades(dict.fromkeys(years, 0.1), 0.5)}
    wf = family_walk_forward([a, b], trades, prereg)
    assert [x["variante"] for x in wf["choix"]] == ["A"] * 5 and wf["plis_positifs"] == 5
    assert wf["oos"]["mean_r"] == pytest.approx(0.15, abs=0.01) and wf["oos"]["mean_eur_300"] == pytest.approx(
        45, abs=3
    )
    assert walk_forward_choice("A", wf) and not walk_forward_choice("B", wf)
    # the choice changes across the test years: no cell is the family's choice
    flip = {**dict.fromkeys(range(2017, 2020), 0.15), **dict.fromkeys(range(2020, 2025), -0.3)}
    trades2 = {"A": _wf_trades(flip, 0.15), "B": trades["B"]}
    wf2 = family_walk_forward([a, b], trades2, prereg)
    assert {x["variante"] for x in wf2["choix"]} == {"A", "B"}
    assert not walk_forward_choice("A", wf2) and not walk_forward_choice("B", wf2)


def _cell_table(r: np.ndarray, ctrl: np.ndarray, ew: np.ndarray, month: np.ndarray) -> Trades:
    n = r.size
    day = month.astype("datetime64[M]").astype("datetime64[D]").astype(np.int64) + 27
    nan, z, one = np.full(n, np.nan), np.zeros(n), np.ones(n, dtype=bool)
    return Trades({
        "group": np.zeros(n, dtype=np.int64), "venue": np.zeros(n, dtype=np.int64),
        "inst": np.arange(n, dtype=np.int64) % 40, "t": np.arange(n),
        "day": day, "week": (day + 3) // 7, "month": month, "year": month // 12 + 1970,
        "status": np.full(n, CLOSED, dtype=np.int64), "closed": one, "hold": np.full(n, 21, dtype=np.int64),
        "ambiguous": ~one, "r": r, "r_gross": r + 0.02, "r_stress": r - 0.02, "r_div_net": r, "r_div_excl": r,
        "r_pea": r, "eur_pea": r * 300, "r_srd": r, "eur": r * 300, "pct": r * 0.02, "hazard": z, "fwd": nan,
        "ctrl": ctrl, "ew": ew, "t13": one, "in_universe": one, "capacity_ok": one, "small": ~one,
        "liq": np.zeros(n, dtype=np.int64), "delisted": ~one, "r_delist_loss": r,
    })  # fmt: skip


def test_stage0_gate_uses_the_matched_control_in_every_mode(prereg):
    pr = light(prereg)
    rng = np.random.default_rng(4)
    month = np.repeat(np.arange(np.datetime64("2017-01"), np.datetime64("2024-07")).astype(np.int64), 6)
    n = month.size
    ew = rng.normal(0, 0.2, n)
    r = ew + 0.3 + rng.normal(0, 0.3, n)  # far above the equal-weighted benchmark ...
    cell = Cell(id="M", tier="exploratory", family="F1", trigger="t01", mode="monthly", h=21, cap_atr=1.0, validity=3)
    above_ew = cell_stats(_cell_table(r, r + 0.1 + rng.normal(0, 0.05, n), ew, month), cell, pr, settings())
    assert above_ew["mean_excess_ew"] > 0.25 and above_ew["t_week"] > 5  # (the series of a monthly cell: EW)
    assert above_ew["mean_excess_ctrl"] < 0 and not above_ew["gate"]  # ... but below its matched control
    beats = cell_stats(_cell_table(r, r - 0.2 + rng.normal(0, 0.05, n), ew, month), cell, pr, settings())
    assert beats["gate"] and beats["t_week_controle"] >= 2.0
    assert beats["inference"] == {"lag_newey_west": 2, "bloc_bootstrap": 4, "unite": "mois"}


def test_cto_liquidity_split_and_d2x_controls_with_the_cells_own_cap(prereg):
    st = settings()
    p = synthetic_panel(40, 900, seed=21)
    g = prepare_group(0, p, st)
    thr = float(np.nanmedian(g.f.turnover_med60[g.f.elig]))
    st2 = dataclasses.replace(st, min_turnover_cto_eur=thr)
    cell = custom_cell(5)
    rows = (300, 800)
    combo = simulate_combo(g, cell.combo_key, st2, rows)
    tr = evaluate_cell(g, cell, combo, st2, rows, mask=g.f.elig.copy())
    sp = cell_stats(add_excess(tr, False), cell, light(prereg), st2)["splits"]
    hi_, lo_ = sp["liquidite_seuil_cto_atteint"]["n"], sp["liquidite_sous_seuil_cto"]["n"]
    assert hi_ > 0 and lo_ > 0 and hi_ + lo_ == int(tr["closed"].sum())
    # D2x: the controls enter with the cell's own cap rule, one combination per cap
    d1 = Cell(id="D1", tier="exploratory", family="F5", trigger="d2x", cap="pct_pivot:0.01", h=10, validity=3)
    d3 = dataclasses.replace(d1, id="D3", cap="pct_pivot:0.03")
    assert d1.combo_key != d3.combo_key and d1.combo_key[5] == "pct_pivot:0.01"
    c1 = simulate_combo(g, d1.combo_key, st, rows)
    ts, cols = np.nonzero(c1.index >= 0)
    lim = c1.od.limit[c1.index[ts, cols]]
    want = T.d2_cap(g.p, g.f, "pct_pivot", 0.01)[ts, cols]
    assert np.allclose(lim[np.isfinite(want)], want[np.isfinite(want)])
    assert np.isfinite(want).sum() > 1000


def test_momentum_cap_and_portfolio_lines_come_from_the_plan(prereg):
    mom = [c for c in prereg.cells if c.trigger in ("t01", "t03", "c1")]
    assert mom and all(c.cap_atr == prereg.a("momentum_entry_cap_atr") for c in mom)
    raw = yaml.safe_load(PREREG.read_bytes())
    raw["assumptions"]["momentum_entry_cap_atr"]["value"] = 0.5
    other = parse_prereg(raw, "x")
    assert {c.combo_key[1] for c in other.cells if c.trigger == "t01"} == {0.5}
    assert other.cell_set_hash == prereg.cell_set_hash  # the cap is not part of the cell ids
    del raw["assumptions"]["momentum_entry_cap_atr"]
    with pytest.raises(PreregError):
        parse_prereg(raw, "x")
    assert portfolio_slots(prereg, settings()) == (6, None)
    slots, warn = portfolio_slots(prereg, dataclasses.replace(settings(), slots=4))
    assert slots == 6 and warn and "thresholds.a13.slots" in warn


# --- 13. review fixes, pass C (protocol and operations) -----------------------------------------------------


def test_unseal_needs_the_ledgered_run_and_its_untouched_candidate_file(prereg, tmp_path):
    from app.services.research import append_ledger, freeze_candidates, read_ledger, run_ledger_row

    cid = "F3.t05.k_sigma2.h10"
    res = {"candidats": [cid], "cellules": {cid: {"niveau": "CANDIDAT", "n": 400, "mean_r": 0.2}},
           "comptes_cellules": {"total_execute": 9}}  # fmt: skip
    # an older ledger layout is migrated (rows kept) when the new columns are appended
    (tmp_path / "ledger.csv").write_text("timestamp,kind,prereg_sha256\n20261001_000000,run,x\n", encoding="utf-8")
    frozen = freeze_candidates(tmp_path, "20261026_120000", prereg, res, "VALIDE")
    assert frozen is not None
    append_ledger(tmp_path, run_ledger_row("20261026_120000", prereg, res, "all", "r.json", frozen, "VALIDE"))
    rows = read_ledger(tmp_path)
    assert rows[0]["prereg_sha256"] == "x" and rows[-1]["candidate_cells"] == cid
    path = tmp_path / "20261026_120000_candidates.yaml"
    body = path.read_bytes()
    assert check_unseal(yaml.safe_load(body), file_sha256(body), prereg, rows) == [cid]
    # a hand-edited file (one more cell, or the same cells retyped) has another sha256: refused
    edited = yaml.safe_load(body)
    edited["cells"].append("F3.t05.k_sigma2.5.h10")
    edited["niveaux"]["F3.t05.k_sigma2.5.h10"] = "CANDIDAT"
    raw = yaml.safe_dump(edited).encode()
    with pytest.raises(PreregError, match="registre"):
        check_unseal(edited, file_sha256(raw), prereg, rows)
    with pytest.raises(PreregError, match="registre"):  # a hand-made file, no ledgered run
        check_unseal(yaml.safe_load(body), file_sha256(body + b"\n"), prereg, rows)
    with pytest.raises(PreregError, match="CANDIDAT"):  # a ledgered file whose cells are not all CANDIDAT
        check_unseal({**yaml.safe_load(body), "niveaux": {cid: "SURVEILLANCE"}}, file_sha256(body), prereg, rows)
    # a run whose calibration is not valid freezes nothing: no file, nothing to unseal
    assert freeze_candidates(tmp_path, "20261026_130000", prereg, res, "INVALIDE") is None
    assert not (tmp_path / "20261026_130000_candidates.yaml").exists()
    row = run_ledger_row("20261026_130000", prereg, res, "all", "r2.json", None, "INVALIDE")
    assert row["candidates_sha256"] == "" and row["calibration"] == "INVALIDE"


def _engine_run2_trades(g, st, n: int, seed: int, hold: int = 20) -> list:
    """Run-2-like trades produced by the engine itself in run-2 conventions (what a correct replay finds)."""
    from app.domain.research.entries import calendar_of
    from app.domain.research.f0 import Run2Trade

    rng = np.random.default_rng(seed)
    ts = rng.integers(300, 600, n).astype(np.int64)
    cols = rng.integers(0, g.p.shape[1], n).astype(np.int64)
    te = ts + 1
    entry = g.p.open[te, cols]
    stop, target = entry * 0.93, entry * 1.12
    od = absolute_orders(cols, g.f.pk.pos[te, cols] - 1, entry, stop, target, 1)
    sim = simulate(g.bars, od, ExitSpec("run2", hold, target_on_fill_bar="always"), st.slippage, dividends=False)
    xt = calendar_of(g.f.pk, cols, sim.exit_pos)
    names = {0: "stop", 1: "gap_stop", 2: "target", 3: "time"}
    r = sim.gross_return / sim.risk_fraction

    def day(t: int):
        return np.datetime64(g.p.days[t], "D").astype(object)

    return [
        Run2Trade(g.p.isins[int(cols[k])], "d2_breakout", day(int(ts[k])), day(int(te[k])), day(int(xt[k])),
                  float(sim.entry[k]), float(sim.exit_px[k]), float(stop[k]), float(target[k]),
                  names[int(sim.reason[k])], float(r[k]), float(r[k]), 0.0, 0.0, True)
        for k in range(n) if sim.status[k] == CLOSED
    ]  # fmt: skip


def test_calibration_counts_unlocated_trades_as_mismatches():
    from collections import Counter

    from app.domain.research.f0 import calibration_parts, calibration_verdict

    st = settings()
    g = prepare_group(0, synthetic_panel(30, 800, seed=31), st)
    trades = _engine_run2_trades(g, st, 400, 1)
    assert len(trades) > 300
    parts = calibration_parts(g, trades, st, {"d2_breakout": 20})
    ok = calibration_verdict(parts, Counter(t.detector for t in trades))
    assert ok["verdict"] == "VALIDE" and ok["concordance"] == 1.0 and "PAS testées" in ok["portee"]
    # 5 % of the stored trades cannot be located (instrument missing from the panels): mismatches → INVALIDE
    lost = [dataclasses.replace(t, isin=f"ZZ{k:010d}") for k, t in enumerate(trades[: len(trades) // 19])]
    all_trades = trades + lost
    parts = calibration_parts(g, all_trades, st, {"d2_breakout": 20})
    v = calibration_verdict(parts, Counter(t.detector for t in all_trades))
    assert v["trades_localises"] == len(trades) and v["trades_run2"] == len(all_trades)
    assert v["concordance"] < 0.99 and v["part_localisee"] < 0.99 and v["verdict"] == "INVALIDE"
    # (the former rule — concordance over the located trades only — read 100 % and said VALIDE)
    assert float(np.mean(parts["match"])) == 1.0


def test_report_header_relies_on_the_calibration_of_the_same_plan_and_run(prereg):
    from datetime import date

    from app.services.research import select_calibration

    sha = prereg.sha256
    good = {"verdict": "VALIDE", "plan_sha256": sha, "run": 2, "fin_donnees": "2026-10-01", "concordance": 1.0}
    cals = [
        ("calibration_20261001_000000.json", good),
        ("calibration_20261020_000000.json", {**good, "plan_sha256": "autre plan"}),  # newest file: another plan
        ("calibration_20261021_000000.json", {**good, "run": 3}),  # another run
    ]
    v, label = select_calibration(cals, sha, 2, date(2026, 9, 30))
    assert v == "VALIDE" and "calibration_20261001_000000.json" in label
    assert select_calibration(cals, sha, 2, date(2026, 10, 15))[0] == "INVALIDE"  # does not reach the data end
    assert select_calibration(cals, sha, 4, date(2026, 9, 30))[0] == "INVALIDE"  # no calibration of that run
    assert select_calibration(cals, sha, None, date(2026, 9, 30))[0] == "INVALIDE"
    newer_bad = [*cals, ("calibration_20261025_000000.json", {**good, "verdict": "INVALIDE", "concordance": 0.9})]
    assert select_calibration(newer_bad, sha, 2, date(2026, 9, 30))[0] == "INVALIDE"
    # an INVALIDE calibration prefixes the title of the report
    rep = build_report(prereg, {"cellules": {}, "candidats": []}, {"calibration_verdict": "INVALIDE", "limites": []})
    assert rep["A_resume"]["titre"].startswith("INVALIDE") and HEADLINE_NONE in rep["A_resume"]["titre"]
    assert (
        build_report(prereg, {"cellules": {}, "candidats": []}, {"calibration_verdict": "VALIDE"})["A_resume"]["titre"]
        == HEADLINE_NONE
    )


def _run2_at(g, days_wanted, det="d7_trend_quality"):
    from app.domain.research.f0 import Run2Trade

    out = []
    for k, d in enumerate(days_wanted):
        t = int(np.searchsorted(g.p.days, np.datetime64(d, "D")))
        col = k % g.p.shape[1]
        e = float(g.p.open[t + 1, col])
        day = g.p.days[t].astype(object)
        out.append(Run2Trade(g.p.isins[col], det, day, g.p.days[t + 1].astype(object), day, e, e, e * 0.92,
                             e * 1.15, "time", 0.0, 0.0, 0.0, 0.0, True))  # fmt: skip
    return out


def test_f0_run2_diagnostics_use_discovery_trades_and_report_the_sealed_period_apart(prereg):
    from app.domain.research.registration import F0Accumulator

    st = settings()
    g = prepare_group(0, synthetic_panel(30, 2450, seed=41), st)
    disc = _run2_at(g, [f"{y}-0{m}-10" for y in range(2018, 2024) for m in (2, 5, 8)])
    embargo = _run2_at(g, ["2024-08-12", "2024-09-10"])
    sealed = _run2_at(g, ["2024-11-12", "2024-12-10", "2025-01-14"])
    acc = F0Accumulator(st, disc + embargo + sealed, {}, 7, 10, prereg.segments)
    acc(g, signal_rows(g.p.days, *prereg.segments.discovery, prereg.segments.end_margin_sessions))
    out = acc.summary(7)
    row = out["F0b.h0_geometry.d7_trend_quality"]
    assert row["n"] == len(disc) and row["periode"] == "decouverte"
    assert out["F0c.d7.e4_production"]["n"] == len(disc)
    sealed_part = out["periode_scellee_run2"]
    assert sealed_part["trades_run2"] == len(sealed) and "scellée" in sealed_part["libelle"]
    assert sealed_part["cellules"]["F0b.h0_geometry.d7_trend_quality"]["n"] == len(sealed)
    assert sealed_part["cellules"]["F0c.d7.s0_t3r"]["periode"] == "scellee"
    rep = build_report(prereg, {"cellules": {}, "candidats": [], "f0": out}, {"limites": []})
    sealed_rep = rep["B_diagnostics_run2"]["cellules_f0"]["periode_scellee_run2"]["cellules"]
    assert "supprime" in sealed_rep["F0b.h0_geometry.d7_trend_quality"]  # 3 trades: suppressed, still apart
    assert "(scellée)" in render_fr(rep)


def test_f0_production_sample_is_stratified_by_detector_and_detect_runs_once(monkeypatch):
    import inspect
    from collections import Counter

    from app.domain.research import f0 as F0
    from app.domain.research.registration import run_registration as rr
    from app.services.research import DEFAULT_MAX_F0_CANDIDATES

    assert DEFAULT_MAX_F0_CANDIDATES == 20_000
    assert inspect.signature(rr).parameters["max_f0_candidates"].default == 20_000
    calls: Counter[str] = Counter()

    def counting(cls):
        class Counted(cls):  # type: ignore[misc, valid-type]
            def detect(self, bars, ctx):
                calls[self.name] += 1
                assert ctx.zones is not None
                return super().detect(bars, ctx)

        return Counted

    monkeypatch.setattr(F0, "DETECTORS", {k: counting(v) for k, v in F0.DETECTORS.items()})
    st = settings()
    g = prepare_group(0, synthetic_panel(40, 900, seed=51, sigma=0.03), st)
    prod = {"zones": {}, "min_reward_risk": 2.0, "expiry_sessions": 3}
    out, counts = F0.production_parts(g, st, (300, 800), prod, 60, 5)
    names = [n for n in F0.PRODUCTION_DETECTORS if st.detectors[n].get("enabled")]
    sizes = {n: counts[f"candidats_{n}"] for n in names}
    assert sum(sizes.values()) > 60 and max(sizes.values()) > 15
    quota = F0._quotas(sizes, 60)
    assert sum(quota.values()) <= 60 and all(quota[n] >= min(sizes[n], 15) for n in names)
    for n in names:  # each detector its own share of the sample, one detect() per sampled day
        assert counts[f"evalues_{n}"] == quota[n] == min(sizes[n], quota[n])
        assert calls[n] == counts[f"evalues_{n}"]
    assert counts["evalues"] == sum(quota.values())
    for part in out.values():  # every setup carries the inverse sampling fraction of its detector
        for d, w in zip(part["det"], part["w"], strict=True):
            assert w == pytest.approx(sizes[d] / counts[f"evalues_{d}"])
    s = F0.summarize_forward({"v": [1.0, 0.0], "week": [1, 2], "w": [3.0, 1.0], "det": ["a", "b"]}, 1)
    assert s["exces_moyen"] == pytest.approx(0.75) and s["par_detecteur"]["a"]["n"] == 1


def test_venue_split_uses_the_group_name_not_the_panel_index(prereg):
    st = settings()
    g = prepare_group(0, synthetic_panel(30, 900, seed=61, group="P1"), st)  # the first panel, but P1
    cell = custom_cell(5)
    combo = simulate_combo(g, cell.combo_key, st, (300, 800))
    tr = evaluate_cell(g, cell, combo, st, (300, 800), mask=g.f.elig.copy())
    sp = cell_stats(add_excess(tr, False), cell, light(prereg), st)["splits"]
    assert sp["P0"]["n"] == 0 and sp["P1_P2"]["n"] == int(tr["closed"].sum()) > 0


def test_fx_is_never_back_filled_from_a_later_rate():
    from app.domain.research.panel import fx_path

    days = bdays("2017-05-25", 10)
    rate_days = np.array(["2017-06-01", "2017-06-05"], dtype="datetime64[D]")
    v, flagged = fx_path(days, rate_days, np.array([0.1, 0.11]), 0.088)
    before = days < rate_days[0]
    assert np.allclose(v[before], 0.088) and flagged[before].all() and not flagged[~before].any()
    assert v[days == np.datetime64("2017-06-02")][0] == 0.1 and v[-1] == 0.11
    v2, flagged2 = fx_path(days, rate_days, np.array([0.1, 0.11]), None)
    assert np.isnan(v2[before]).all() and not flagged2.any()  # no static rate: unknown, never the first rate
    v3, flagged3 = fx_path(days, rate_days[:0], np.zeros(0), 0.088)
    assert np.allclose(v3, 0.088) and flagged3.all()


def test_cli_universe_has_no_pit_alias():
    from app.cli import main

    with pytest.raises(SystemExit):
        main(["research", "run", "--universe", "pit"])


def test_unseal_verdict_needs_a2_in_full_and_the_same_sign():
    from app.domain.research.classify import unseal_confirmed

    th = yaml.safe_load(PREREG.read_text(encoding="utf-8"))["thresholds"]
    disc = {"mean_r": 0.2}
    assert unseal_confirmed(dict(OOS_GOOD), disc, th)
    # the former check ignored the % of notional: a loss in money with a positive R mean was "confirmed"
    assert not unseal_confirmed({**OOS_GOOD, "mean_pct_notionnel": -0.001}, disc, th)
    assert not unseal_confirmed(dict(OOS_GOOD), {"mean_r": -0.2}, th)
    assert not unseal_confirmed(dict(OOS_GOOD), {}, th)
    assert not unseal_confirmed({**OOS_GOOD, "profit_factor": 1.05}, disc, th)


def test_report_suppresses_every_small_nested_row_and_refuses_isins(prereg):
    cell = {
        "id": "F3.a", "famille": "F3", "tier": "exploratory", "n": 400, "mean_r": 0.1, "niveau": "REJET",
        "splits": {"P0": {"n": 390, "mean_r": 0.1}, "P1_P2": {"n": 10, "mean_r": 2.5},
                   "annees": {2017: {"n": 5, "mean_r": -3.0}}},
        "plis": [{"annee": 2020, "n": 12, "mean_r": 0.9}, {"annee": 2021, "n": 80, "mean_r": 0.1}],
        "regime": {"t13_vrai": {"n": 3, "mean_r": 1.2}},
    }  # fmt: skip
    res = {"cellules": {"F3.a": cell}, "candidats": [],
           "run2_decomposition": {"d4_pullback": {"n": 7, "r_net_moyen": -0.4}},
           "etape1": {"F3.a": {"X5": {"n": 15, "delta_r_oos": 0.3}}}}  # fmt: skip
    rep = build_report(prereg, res, {"limites": []})
    f = rep["F_robustesse"]["F3.a"]
    assert f["splits"]["P0"]["mean_r"] == 0.1 and "mean_r" not in f["splits"]["P1_P2"]
    assert "mean_r" not in f["splits"]["annees"][2017] and "supprime" in f["regime"]["t13_vrai"]
    assert f["plis"][0] == {"annee": 2020, "n": 12, "supprime": "moins de 20 trades"} and f["plis"][1]["mean_r"] == 0.1
    assert "r_net_moyen" not in rep["B_diagnostics_run2"]["decomposition"]["d4_pullback"]
    assert "delta_r_oos" not in rep["E_etape1_sorties"]["F3.a"]["X5"]
    render_fr(rep)
    for bad in ({"avertissements": ["FR0000120271 exclu"]}, {"x": {"DE0007164600": 1}}, {"l": [("NL0000235190",)]}):
        with pytest.raises(ReportError, match="ISIN"):
            assert_no_price_fields(bad)
    assert_no_price_fields({"plan_sha256": "ab" * 32, "cellule": "F2.t08.gap_min0.03.cap_atr1.h20", "id": "H1"})


def test_pbo_is_low_for_a_planted_best_column_and_near_half_for_noise():
    noise = [S.pbo_cscv(np.random.default_rng(70 + k).normal(size=(320, 8)), 16) for k in range(8)]
    assert 0.3 <= float(np.mean(noise)) <= 0.7
    m = np.random.default_rng(80).normal(size=(320, 8))
    m[:, 3] += 0.6  # one variant truly better in every period
    assert S.pbo_cscv(m, 16) <= 0.05


def _planted_cell_panel(n_inst=150, n_days=2400, events=1100, h=10, edge_r=0.4, seed=91):
    rng = np.random.default_rng(seed)
    sigma = 0.02
    move = edge_r * 2 * sigma * math.sqrt(h)
    days = bdays("2016-01-04", n_days)
    lo = int(np.searchsorted(days, np.datetime64("2017-01-03")))
    hi = int(np.searchsorted(days, np.datetime64("2024-07-30")))
    extra = np.zeros((n_days, n_inst))
    mask = np.zeros((n_days, n_inst), dtype=bool)
    placed = 0
    while placed < events:
        t, i = int(rng.integers(lo, hi)), int(rng.integers(0, n_inst))
        if mask[max(0, t - 25) : t + 25, i].any():
            continue
        mask[t, i] = True
        extra[t + 2 : t + 2 + h, i] += math.log(1 + move) / h
        placed += 1
    return synthetic_panel(n_inst, n_days, seed, sigma=sigma, lr_extra=extra), mask


def test_planted_trigger_on_a_registered_cell_reaches_candidate(prereg, monkeypatch):
    from app.domain.research import pipeline as P

    target = "F3.t05.k_sigma2.h10"
    fam = tuple(c for c in prereg.cells if c.family == "F3")
    assert target in {c.id for c in fam} and len(fam) == 9
    pr = dataclasses.replace(light(prereg), cells=fam, inference={**light(prereg).inference, "permutation_draws": 200})
    panel, mask = _planted_cell_panel()
    real = P.raw_trigger

    def wired(g, cell, st):  # the registered cell's trigger fires on the planted events (others: real triggers)
        if cell.id != target:
            return real(g, cell, st)
        with np.errstate(invalid="ignore"):
            orderable = np.isfinite(g.f.atr14) & (g.f.atr14 > 0) & np.isfinite(g.f.sigma20) & (g.f.sigma20 > 0)
        return mask & g.f.elig & orderable, None

    monkeypatch.setattr(P, "raw_trigger", wired)
    st = dataclasses.replace(settings(), universe_label="all")
    res = run_registration([panel], pr, st, f0=False)
    c = res["cellules"][target]
    assert c["niveau"] == Tier.CANDIDATE.value, {k: v for k, v in c["criteres"].items() if not v}
    assert res["candidats"] == [target] and target in res["etape2"] and res["etape1"].get(target)
    assert c["walk_forward_famille"]["choix_cellule"] and c["pbo_famille"] <= 0.05
    others = [res["cellules"][x.id]["niveau"] for x in fam if x.id != target]
    assert Tier.CANDIDATE.value not in others


def test_holdout_bars_do_not_change_discovery_statistics(prereg):
    import json

    cells = tuple(prereg.confirmatory) + tuple(c for c in prereg.cells if c.family == "F3")
    pr = dataclasses.replace(light(prereg), cells=cells)
    st = dataclasses.replace(settings(), universe_label="all")
    p = synthetic_panel(40, 2450, seed=97)

    def perturbed(start: str, seed: int) -> Panel:
        """Bars from ``start`` on multiplied by random factors; the adjusted close by its own factors (fake
        dividends and corporate actions, some rejected by the dividend check)."""
        t0 = int(np.searchsorted(p.days, np.datetime64(start, "D")))
        rng = np.random.default_rng(seed)
        after = np.arange(p.shape[0])[:, None] >= t0
        k = np.where(after, rng.uniform(0.6, 1.4, p.close.shape), 1.0)
        k_adj = np.where(after, rng.uniform(0.3, 1.7, p.close.shape), 1.0)
        hi_ = np.maximum(p.high * k, np.maximum(p.open * k, p.close * k))
        return dataclasses.replace(p, open=p.open * k, high=hi_, low=p.low * k, close=p.close * k,
                                   adj_close=p.adj_close * k * k_adj, volume=p.volume * np.where(after, 3 * k, 1.0))  # fmt: skip

    def discovery(res: dict) -> str:  # everything but the panel-wide data-quality counts of the header
        keep = {k: v for k, v in res.items() if k not in ("qualite_donnees", "annees_dividendes_ecartees")}
        return json.dumps(keep, sort_keys=True, default=str)

    a = run_registration([p], pr, st, f0=False)
    b = run_registration([perturbed(str(pr.segments.holdout_start), 98)], pr, st, f0=False)
    assert a["segment"] == "decouverte" and sum(c.get("n", 0) for c in a["cellules"].values()) > 0
    assert b["annees_dividendes_ecartees"] > 0  # sealed-period dividends were rejected: no 2024 discovery exclusion
    assert discovery(a) == discovery(b)
    # the comparison is sensitive: the same perturbation from inside the discovery window changes the statistics
    c = run_registration([perturbed("2024-03-01", 98)], pr, st, f0=False)
    assert discovery(a) != discovery(c)
