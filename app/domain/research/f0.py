# ruff: noqa: E501
"""F0 — diagnostics of the production pipeline (docs/31 §1.4). Never candidates. Pure.

(a) run-2 decomposition from its stored trades; (b) matched geometry: the run-2 trades against random entries
of the same day with identical stop and target distances in ATR; (c) the d7 frozen entries under 11 exit
variants — (b) and (c) on the run-2 trades of the discovery window only (the sealed-period ones are reported
apart, labelled, by the registration); (d)–(f) forward excess returns of vectorised detector conditions;
(g)–(h) refused and unfilled setups of the real detectors (``run_detectors`` path: zones, admission at the
maximum entry price), on a sample stratified by detector of the days that pass each detector's vectorised
necessary conditions (inverse-sampling-fraction weights).
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np

from app.domain.admission import AdmissionInput, Status, admit
from app.domain.costs import CostParams, round_trip_fee_fraction
from app.domain.detectors import DETECTORS, DetectorContext
from app.domain.indicators import Bar, compute_daily_features
from app.domain.research import stats as S
from app.domain.research import triggers as T
from app.domain.research.costs import LegInputs, economics
from app.domain.research.entries import absolute_orders, calendar_of, forward_excess
from app.domain.research.exits import (
    CLOSED,
    GAP_STOP,
    SLIPPED_EXITS,
    STOP,
    TRAIL,
    UNFILLED,
    ExitSpec,
    Orders,
    simulate,
)
from app.domain.research.panel import F32, F64, I64, Bool, Packing, pack, rolling_min, shift, unpack
from app.domain.research.pipeline import GroupData, Settings, _f, _mean
from app.domain.zones import detect_zones

HOLD_BY_HORIZON = {"days": 5, "swing_2_6w": 20, "semaines": 40}
EXIT_LABELS = {"stop": "par_stop", "gap_stop": "stop_en_gap", "target": "objectif", "time": "duree"}


@dataclass(frozen=True)
class Run2Trade:
    isin: str
    detector: str
    signal_day: date
    entry_day: date
    exit_day: date
    entry: float
    exit: float
    stop: float
    target: float
    reason: str
    r_gross: float
    r_net: float
    mfe_r: float
    mae_r: float
    production: bool  # admitted, no warning


def detector_hold(det: str, params: Mapping[str, Any]) -> int:
    p = params.get(det) or {}
    default = {"d2_breakout": "swing_2_6w", "d3_range_expansion": "days", "d4_pullback": "swing_2_6w",
               "d7_trend_quality": "semaines", "d8_buzz_precoce": "days"}.get(det, "swing_2_6w")  # fmt: skip
    return int(p.get("time_stop_sessions") or HOLD_BY_HORIZON[default])


# --- (a) decomposition ----------------------------------------------------------------------------


def decompose(trades: Sequence[Run2Trade], dividend_days: Mapping[str, Sequence[date]]) -> dict[str, Any]:
    """Aggregated facts of run 2 per detector (production trades): cost drag, R lost by exit kind, losses
    holding an ex-dividend date, excursions. No price, no instrument."""
    by: dict[str, list[Run2Trade]] = defaultdict(list)
    for t in trades:
        if t.production:
            by[t.detector].append(t)
    out: dict[str, Any] = {}
    for det, ts in sorted(by.items()):
        n = len(ts)
        losers = [t for t in ts if t.r_net < 0]
        winners = [t for t in ts if t.r_net > 0]

        def q(xs: list[float]) -> list[float | None]:
            if len(xs) < 4:
                return [None, None, None]
            qs = statistics.quantiles(xs, n=4)
            return [round(x, 3) for x in qs]

        div_hit = 0
        for t in losers:
            days = dividend_days.get(t.isin) or ()
            if any(t.entry_day < d <= t.exit_day for d in days):
                div_hit += 1
        sums: dict[str, float] = defaultdict(float)
        counts: dict[str, int] = defaultdict(int)
        for t in ts:
            k = EXIT_LABELS.get(t.reason, t.reason)
            sums[k] += t.r_net
            counts[k] += 1
        out[det] = {
            "n": n,
            "r_brut_moyen": round(statistics.fmean(t.r_gross for t in ts), 4),
            "r_net_moyen": round(statistics.fmean(t.r_net for t in ts), 4),
            "cout_moyen_r": round(statistics.fmean(t.r_gross - t.r_net for t in ts), 4),
            "sorties": {
                k: {
                    "part": round(counts[k] / n, 3),
                    "r_total": round(sums[k], 2),
                    "r_moyen": round(sums[k] / counts[k], 3),
                }
                for k in sorted(counts)
            },  # fmt: skip
            "perdants_avec_detachement": div_hit,
            "part_perdants_avec_detachement": round(div_hit / len(losers), 3) if losers else None,
            "mae_quartiles_gagnants": q([t.mae_r for t in winners]),
            "mfe_quartiles_perdants": q([t.mfe_r for t in losers]),
            "perdants_passes_par_1r": round(sum(t.mfe_r >= 1 for t in losers) / len(losers), 3) if losers else None,
            "perdants_passes_par_2r": round(sum(t.mfe_r >= 2 for t in losers) / len(losers), 3) if losers else None,
        }
    return out


# --- helpers ------------------------------------------------------------------------------------------


def _locate(g: GroupData, trades: Sequence[Run2Trade]) -> tuple[list[Run2Trade], I64, I64, I64]:
    """Run-2 trades of this group with their column, signal row and entry row (−1 dropped)."""
    col_of = {isin: k for k, isin in enumerate(g.p.isins)}
    days = g.p.days
    keep, cols, ts, te = [], [], [], []
    for t in trades:
        c = col_of.get(t.isin)
        if c is None:
            continue
        a = int(np.searchsorted(days, np.datetime64(t.signal_day, "D")))
        b = int(np.searchsorted(days, np.datetime64(t.entry_day, "D")))
        if a >= days.size or b >= days.size or days[a] != np.datetime64(t.signal_day, "D"):
            continue
        if days[b] != np.datetime64(t.entry_day, "D") or g.f.pk.pos[b, c] < 1:
            continue
        keep.append(t)
        cols.append(c)
        ts.append(a)
        te.append(b)
    return keep, np.array(cols, dtype=np.int64), np.array(ts, dtype=np.int64), np.array(te, dtype=np.int64)


def _r_prod(
    g: GroupData, cols: I64, sim_entry: F64, sim: Any, st: Settings, stop_exec: F64, spread_mult: float = 1.0
) -> F64:
    """Net R (CTO, dividends ignored as in run 2) in the production risk unit (entry − slipped stop).
    ``spread_mult`` 0: without the half-spread legs, which run 2 does not charge (reconciliation only)."""
    pk = g.f.pk
    ft = np.maximum(calendar_of(pk, cols, sim.fill_pos), 0)
    et = np.maximum(calendar_of(pk, cols, sim.exit_pos), 0)
    unit = (sim_entry - stop_exec) / sim_entry
    x = LegInputs(
        entry=sim_entry, exit_px=sim.exit_px, stop_exec=stop_exec, div_cash=np.zeros(cols.size),
        fx_in=g.p.fx_to_eur[ft, cols], fx_out=g.p.fx_to_eur[et, cols], ttf=g.p.ttf_rate[cols],
        ftt=g.p.ftt_rate[cols], fx_fee=g.p.fx_fee_pct[cols], haircut=np.zeros(cols.size), hs_in=g.hs[ft, cols],
        hs_out=g.hs[et, cols], days=np.zeros(cols.size), unit_frac=unit,
        stop_exit=np.isin(sim.reason, (STOP, GAP_STOP, TRAIL)),
        exit_slip=np.where(np.isin(sim.reason, SLIPPED_EXITS), st.slippage, 0.0),
    )  # fmt: skip
    e = economics(x, st.cto, st.risk_budget_eur, st.notional_cap_eur, spread_mult=spread_mult)
    return np.where(sim.status == CLOSED, e.r_unit, np.nan)


def _diff_stats(week: I64, diff: F64, seed: int, draws: int = 1000) -> dict[str, Any]:
    wk, ws = S.weekly_series(week, diff)
    lo, hi = S.block_bootstrap_ci(ws, 4, draws, seed) if ws.size > 2 else (math.nan, math.nan)
    # round 2: the interval is on weekly means — their mean is printed too, so the point and the interval are read
    # on the same quantity (the per-trade mean stays the headline, as pre-registered)
    return {"n": int(np.isfinite(diff).sum()), "difference_moyenne": _f(_mean(diff)), "ic": [_f(lo), _f(hi)],
            "difference_moyenne_hebdo": _f(_mean(ws)) if ws.size else None,
            "t_semaine": _f(S.newey_west_t(ws, 4) if ws.size > 2 else math.nan, 3)}  # fmt: skip


# --- (b) matched geometry and (c) d7 exit set ---------------------------------------------------------


def h0_geometry_parts(
    g: GroupData, trades: Sequence[Run2Trade], st: Settings, holds: Mapping[str, int], draws: int, seed: int
) -> dict[str, dict[str, list[Any]]]:  # fmt: skip
    """Per detector: trade R, mean control R (``draws`` random eligible instruments of the same day with the
    same stop / target distances in ATR, entry at the next open, same exit rules) and the week."""
    tr, cols, ts, te = _locate(g, [t for t in trades if t.production])
    out: dict[str, dict[str, list[Any]]] = defaultdict(lambda: {"trade": [], "control": [], "week": []})
    if not tr:
        return out
    rng = np.random.default_rng(seed + g.gid)
    entry = np.array([t.entry for t in tr])
    stop = np.array([t.stop for t in tr])
    target = np.array([t.target for t in tr])
    atr = g.f.atr14[ts, cols]
    for det in sorted({t.detector for t in tr}):
        m = np.array([t.detector == det for t in tr])
        hold = int(holds.get(det, 20))
        spec = ExitSpec("prod", hold, target_on_fill_bar="always")
        idx = np.nonzero(m)[0]
        sp = g.f.pk.pos[te[idx], cols[idx]] - 1
        od = absolute_orders(cols[idx], sp, entry[idx], stop[idx], target[idx], 1)
        sim = simulate(g.bars, od, spec, st.slippage, dividends=False)
        r_tr = _r_prod(g, cols[idx], sim.entry, sim, st, sim.stop_exec_init)
        ds = (entry[idx] - stop[idx]) / atr[idx]
        dt = (target[idx] - entry[idx]) / atr[idx]
        # controls: eligible instruments of the same day, drawn with replacement
        c_cols, c_t, c_ds, c_dt, owner = [], [], [], [], []
        for k, t_ in enumerate(ts[idx].tolist()):
            pool = np.nonzero(g.f.elig[t_] & np.isfinite(g.f.atr14[t_]) & (g.f.atr14[t_] > 0))[0]
            if pool.size == 0 or not np.isfinite(ds[k]):
                continue
            pick = pool[rng.integers(0, pool.size, size=draws)]
            c_cols.append(pick)
            c_t.append(np.full(draws, t_))
            c_ds.append(np.full(draws, ds[k]))
            c_dt.append(np.full(draws, dt[k]))
            owner.append(np.full(draws, k))
        if not c_cols:
            continue
        cc, ct = np.concatenate(c_cols), np.concatenate(c_t)
        cds, cdt, own = np.concatenate(c_ds), np.concatenate(c_dt), np.concatenate(owner)
        catr = g.f.atr14[ct, cc]
        n = cc.size
        cod = Orders(cc, g.f.pk.pos[ct, cc], np.full(n, np.inf), np.full(n, np.nan), np.ones(n), cds * catr,
                     np.ones(n), cdt * catr, np.full(n, np.nan), 1)  # fmt: skip
        csim = simulate(g.bars, cod, spec, st.slippage, dividends=False)
        r_c = _r_prod(g, cc, csim.entry, csim, st, csim.stop_exec_init)
        sums = np.bincount(own, weights=np.nan_to_num(r_c), minlength=idx.size)
        cnts = np.bincount(own, weights=np.isfinite(r_c).astype(np.float64), minlength=idx.size)
        ctrl = np.where(cnts > 0, sums / np.maximum(cnts, 1), np.nan)
        d = out[det]
        d["trade"] += r_tr.tolist()
        d["control"] += ctrl.tolist()
        d["week"] += g.week[ts[idx]].tolist()
    return out


# what each variant really changes (round 2, 26/10/2026: « s_atr3_t0 » keeps the production target and is not
# sized at constant risk — the R stays in the production unit, entry − slipped production stop)
D7_LABELS = {
    "e4_production": "sorties de production (référence)",
    "s_atr2_t0": "stop 2 × ATR, objectif de production conservé, R en unité de production",
    "s_atr2.5_t0": "stop 2,5 × ATR, objectif de production conservé, R en unité de production",
    "s_atr3_t0": "stop 3 × ATR, objectif de production conservé, R en unité de production",
    "s0_no_target_ch3": "stop de production, sans objectif, suiveur chandelier 3 ATR",
    "s0_t1.5r": "stop de production, objectif 1,5 R",
    "s0_t3r": "stop de production, objectif 3 R",
    "s0_t0_time20": "sorties de production, durée max 20 séances",
    "s0_t0_time40": "sorties de production, durée max 40 séances",
    "cond_time5": "sorties de production + sortie à 5 séances si le gain reste sous 0,5 R",
    "breakeven_1r": "sorties de production + stop au prix d'entrée après +1 R",
}

D7_VARIANTS: tuple[tuple[str, dict[str, Any]], ...] = (
    ("e4_production", {}),
    ("s_atr2_t0", {"stop_atr": 2.0}),
    ("s_atr2.5_t0", {"stop_atr": 2.5}),
    ("s_atr3_t0", {"stop_atr": 3.0}),
    ("s0_no_target_ch3", {"no_target": True, "trail": 3.0}),
    ("s0_t1.5r", {"target_r": 1.5}),
    ("s0_t3r", {"target_r": 3.0}),
    ("s0_t0_time20", {"hold": 20}),
    ("s0_t0_time40", {"hold": 40}),
    ("cond_time5", {"cond": 0.5}),
    ("breakeven_1r", {"breakeven": 1.0}),
)


def d7_exit_parts(
    g: GroupData, trades: Sequence[Run2Trade], st: Settings, hold: int
) -> dict[str, dict[str, list[Any]]]:  # fmt: skip
    """The d7 production entries (frozen) under the 11 exit variants; R in the production risk unit."""
    tr, cols, ts, te = _locate(g, [t for t in trades if t.production and t.detector == "d7_trend_quality"])
    out: dict[str, dict[str, list[Any]]] = defaultdict(lambda: {"r": [], "eur": [], "week": []})
    if not tr:
        return out
    out["e4_production"].update(r_sans_ecart=[], r_run2=[])
    entry = np.array([t.entry for t in tr])
    stop = np.array([t.stop for t in tr])
    target = np.array([t.target for t in tr])
    atr = g.f.atr14[ts, cols]
    sp = g.f.pk.pos[te, cols] - 1
    n = cols.size
    stop_exec_prod = stop * (1 - st.slippage)
    for name, v in D7_VARIANTS:
        s_mult, s_off = np.zeros(n), -stop
        if "stop_atr" in v:
            s_mult, s_off = np.ones(n), float(v["stop_atr"]) * atr
        t_mult, t_off = np.zeros(n), target.copy()
        if v.get("no_target"):
            t_off = np.full(n, np.nan)
        cond = np.full(n, np.nan)
        if "cond" in v:
            cond = float(v["cond"]) * (entry - stop_exec_prod) / entry
        spec = ExitSpec(
            name, int(v.get("hold", hold)), target_r=float(v.get("target_r", 0.0)), trail_atr=float(v.get("trail", 0)),
            cond_session=5 if "cond" in v else 0, breakeven_r=float(v.get("breakeven", 0.0)),
            target_on_fill_bar="always",
        )  # fmt: skip
        od = Orders(cols, sp, entry, stop, s_mult, s_off, t_mult, t_off, cond, 1)
        sim = simulate(g.bars, od, spec, st.slippage, dividends=False)
        r = _r_prod(g, cols, sim.entry, sim, st, stop_exec_prod)
        d = out[name]
        if name == "e4_production":  # reconciliation with the run-2 decomposition (same trades)
            d["r_sans_ecart"] += _r_prod(g, cols, sim.entry, sim, st, stop_exec_prod, spread_mult=0.0).tolist()
            d["r_run2"] += [t.r_net for t in tr]
        d["r"] += r.tolist()
        d["eur"] += (r * 300.0).tolist()
        d["week"] += g.week[ts].tolist()
    return out


# --- (d)–(f) forward excess of vectorised detector conditions ------------------------------------------------


def _fwd_parts(g: GroupData, mask: Bool, fw: F32, rows: tuple[int, int]) -> dict[str, list[Any]]:
    m = mask.copy()
    m[: rows[0]] = False
    m[rows[1] + 1 :] = False
    t, c = np.nonzero(m)
    v = fw[t, c].astype(np.float64)
    return {"v": v.tolist(), "week": g.week[t].tolist()}


def forward_cells(g: GroupData, st: Settings, rows: tuple[int, int]) -> dict[str, dict[str, list[Any]]]:
    det = st.detectors
    p, f = g.p, g.f
    fw = {h: _fwd32(g, h) for h in (1, 3, 5, 20, 60)}
    out: dict[str, dict[str, list[Any]]] = {}
    d3 = T.d3_base(p, f, det.get("d3_range_expansion") or {})
    for h in (1, 3, 5):
        out[f"F0d.d3_inverted.h{h}"] = _fwd_parts(g, d3, fw[h], rows)
    d2p = det.get("d2_breakout") or {}
    d2 = T.d2_base(p, f, d2p)
    with np.errstate(invalid="ignore", divide="ignore"):
        dist = p.close / f.pivot60 - 1
    for name, lo, hi in (("0_1pct", 0.0, 0.01), ("1_3pct", 0.01, 0.03), ("gt3pct", 0.03, math.inf)):
        with np.errstate(invalid="ignore"):
            out[f"F0e.d2_distance.{name}.h20"] = _fwd_parts(g, d2 & (dist > lo) & (dist <= hi), fw[20], rows)
    d7p = det.get("d7_trend_quality") or {}
    d7 = T.d7_base(p, f, d7p)
    pmax7 = p.high * (1 + float(d7p.get("entry_max_pct_above_high", 0.008)))
    low = pack(p.low, f.pk)
    base_low = unpack(shift(rolling_min(low, 20), 1), f.pk, p.shape[0])
    measured = f.pivot60 + (f.pivot60 - base_low)
    pmax2 = f.pivot60 * (1 + float(d2p.get("entry_max_pct_above_pivot", 0.01)))
    with np.errstate(invalid="ignore"):
        no7 = d7 & (f.high_250 <= pmax7 * 1.005)
        no2 = d2 & (np.fmax(f.high_250, measured) <= pmax2 * 1.005)
    for h in (20, 60):
        out[f"F0f.no_target.h{h}"] = _fwd_parts(g, no7 | no2, fw[h], rows)
    return out


# --- (g)–(h) the real detectors on pre-filtered days ---------------------------------------------------------


def _quotas(sizes: Mapping[str, int], total: int) -> dict[str, int]:
    """Stratified allocation of ``total`` evaluations among the detectors: an equal share each, a detector with
    fewer candidate days than its share gives the rest to the others (water-filling)."""
    out: dict[str, int] = {}
    left = total
    names = sorted(sizes, key=lambda k: (sizes[k], k))
    for k, name in enumerate(names):
        share = left // (len(names) - k)
        out[name] = min(sizes[name], share)
        left -= out[name]
    return out


def _pack_cols(x: F64, pk: Packing, cols: I64) -> F64:
    """``pack`` restricted to some columns (the heavy OHLCV arrays are packed one chunk of columns at a time)."""
    order = pk.order[:, cols]
    safe = np.where(order >= 0, order, 0)
    out = np.take_along_axis(x[:, cols], safe, axis=0).astype(np.float64)
    return np.where(order >= 0, out, np.nan)


PRODUCTION_DETECTORS = ("d2_breakout", "d3_range_expansion", "d4_pullback", "d7_trend_quality")
COLUMN_CHUNK = 256


def production_parts(
    g: GroupData, st: Settings, rows: tuple[int, int], prod: Mapping[str, Any], max_candidates: int, seed: int
) -> tuple[dict[str, dict[str, list[Any]]], dict[str, int]]:  # fmt: skip
    """Run ``DETECTORS`` (zones, admission at the maximum entry price, CTO round-trip fees) on the days passing
    each detector's vectorised necessary conditions; collect R/R-refused setups (forward excess, by distance to
    the 52-week high) and admitted setups whose order never filled (gap above the maximum price).

    At most ``max_candidates`` evaluations per group, **stratified by detector** (each detector's own candidate
    days, equal shares, seeded draw); every setup carries the inverse sampling fraction of its stratum as a
    weight. Each sampled (day, instrument, detector) calls ``detect`` once, with the zones (computed once per
    day and instrument); the OHLCV arrays are packed by chunks of columns."""
    det = st.detectors
    p, f = g.p, g.f
    names = [n for n in PRODUCTION_DETECTORS if (det.get(n) or {}).get("enabled", False)]
    pre = {
        "d2_breakout": lambda: T.d2_base(p, f, det["d2_breakout"]),
        "d3_range_expansion": lambda: T.d3_base(p, f, det["d3_range_expansion"]),
        "d4_pullback": lambda: T.d4_prefilter(p, f, det["d4_pullback"]),
        "d7_trend_quality": lambda: T.d7_base(p, f, det["d7_trend_quality"]),
    }
    rng = np.random.default_rng(seed + g.gid)
    found: dict[str, tuple[I64, I64]] = {}
    for n in names:
        m = pre[n]()
        m[: rows[0]] = False
        m[rows[1] + 1 :] = False
        t_d, c_d = np.nonzero(m)
        found[n] = (t_d.astype(np.int64), c_d.astype(np.int64))
        del m
    quota = _quotas({n: int(v[0].size) for n, v in found.items()}, max(0, int(max_candidates)))
    counts: dict[str, int] = {"candidats": 0, "evalues": 0}
    weight: dict[str, float] = {}
    s_t, s_c, s_d = [], [], []
    for k, n in enumerate(names):
        t_d, c_d = found[n]
        size, q = int(t_d.size), quota.get(n, 0)
        if size > q:
            keep = np.sort(rng.choice(size, q, replace=False))
            t_d, c_d = t_d[keep], c_d[keep]
        counts["candidats"] += size
        counts["evalues"] += int(t_d.size)
        counts[f"candidats_{n}"] = size
        counts[f"evalues_{n}"] = int(t_d.size)
        weight[n] = size / t_d.size if t_d.size else 0.0
        s_t.append(t_d)
        s_c.append(c_d)
        s_d.append(np.full(t_d.size, k, dtype=np.int64))
    st_t = np.concatenate(s_t) if s_t else np.zeros(0, dtype=np.int64)
    st_c = np.concatenate(s_c) if s_c else np.zeros(0, dtype=np.int64)
    st_d = np.concatenate(s_d) if s_d else np.zeros(0, dtype=np.int64)
    order = np.lexsort((st_d, st_t, st_c))  # by column, then day: one window per (day, instrument)
    st_t, st_c, st_d = st_t[order], st_c[order], st_d[order]
    zp = prod.get("zones") or {}
    wick, buf = float(prod.get("wick_buffer_pct", 0.005)), float(prod.get("stop_buffer_atr_mult", 0.25))
    k_rr, expiry = float(prod.get("min_reward_risk", 2.0)), int(prod.get("expiry_sessions", 3))
    pos = f.pk.pos
    detectors = {n: DETECTORS[n](dict(det[n])) for n in names}
    refused: dict[str, list[Any]] = {"t": [], "c": [], "dist": [], "det": [], "w": []}
    admitted: dict[str, list[Any]] = {"t": [], "c": [], "pmax": [], "stop": [], "target": [], "det": [], "w": []}
    ucols = np.unique(st_c)
    for a0 in range(0, ucols.size, COLUMN_CHUNK):
        chunk = ucols[a0 : a0 + COLUMN_CHUNK]
        o, h, lo, c, v = (_pack_cols(x, f.pk, chunk) for x in (p.open, p.high, p.low, p.close, p.volume))
        local = {int(col): j for j, col in enumerate(chunk.tolist())}
        sel = np.nonzero(np.isin(st_c, chunk))[0]
        last_key: tuple[int, int] | None = None
        window: list[Bar] = []
        zctx: DetectorContext | None = None
        for i in sel.tolist():
            t, col, name = int(st_t[i]), int(st_c[i]), names[int(st_d[i])]
            if last_key != (t, col):  # one window, one feature set and one zone set per (day, instrument)
                last_key = (t, col)
                j = local[col]
                kk = int(pos[t, col])
                a = max(0, kk - 279)
                window = [Bar(float(o[r, j]), float(h[r, j]), float(lo[r, j]), float(c[r, j]), int(v[r, j]))
                          for r in range(a, kk + 1)]  # fmt: skip
                rank = f.rs_rank[t, col]
                ff = compute_daily_features(window)
                zones = detect_zones(
                    window[-int(zp.get("lookback_sessions", 250)) :], ff.atr14 or 0.0, int(zp.get("pivot_window", 5)),
                    float(zp.get("merge_atr_mult", 0.5)), int(zp.get("min_touches", 2)),
                )  # fmt: skip
                zctx = DetectorContext(float(rank) if np.isfinite(rank) else None, zones, wick, buf)
            assert zctx is not None
            cp = CostParams(st.cto.fees, ttf_rate=float(p.ttf_rate[col]), ftt_rate=float(p.ftt_rate[col]),
                            fx_fee_pct=float(p.fx_fee_pct[col]))  # fmt: skip
            for s_ in detectors[name].detect(window, zctx):
                if not s_.targets:
                    continue
                tgt = s_.targets[0]
                notional = min(st.notional_cap_eur,
                               st.risk_budget_eur / max(1e-9, (s_.p_max_technical - s_.stop) / s_.p_max_technical))  # fmt: skip
                frac = round_trip_fee_fraction(notional, cp)
                adm = admit(AdmissionInput(window[-1].close, tgt, s_.stop, s_.kind, s_.level_low, frac, k_rr,
                                           s_.p_max_technical))  # fmt: skip
                if adm.status == Status.REFUSED:
                    hi52 = f.high_250[t, col]
                    refused["t"].append(t)
                    refused["c"].append(col)
                    refused["dist"].append(float(hi52 / window[-1].close - 1) if np.isfinite(hi52) else math.nan)
                    refused["det"].append(name)
                    refused["w"].append(weight[name])
                elif adm.status == Status.ADMITTED and not s_.warnings and adm.p_max is not None:
                    admitted["t"].append(t)
                    admitted["c"].append(col)
                    admitted["pmax"].append(adm.p_max)
                    admitted["stop"].append(s_.stop)
                    admitted["target"].append(tgt)
                    admitted["det"].append(name)
                    admitted["w"].append(weight[name])
        del o, h, lo, c, v
    out: dict[str, dict[str, list[Any]]] = {}
    fws = {hh: _fwd32(g, hh) for hh in (20, 60)}
    rt, rc = np.array(refused["t"], dtype=np.int64), np.array(refused["c"], dtype=np.int64)
    for hh in (20, 60):
        vals = fws[hh][rt, rc] if rt.size else np.zeros(0, dtype=np.float32)
        out[f"F0g.rr_refused.h{hh}"] = {"v": vals.astype(np.float64).tolist(), "week": g.week[rt].tolist(),
                                        "dist": list(refused["dist"]), "det": list(refused["det"]),
                                        "w": list(refused["w"])}  # fmt: skip
    at, ac = np.array(admitted["t"], dtype=np.int64), np.array(admitted["c"], dtype=np.int64)
    unfilled = np.zeros(at.size, dtype=bool)
    if at.size:
        od = absolute_orders(ac, pos[at, ac], np.array(admitted["pmax"]), np.array(admitted["stop"]),
                             np.array(admitted["target"]), expiry)  # fmt: skip
        sim = simulate(g.bars, od, ExitSpec("prod", 1, target_on_fill_bar="always"), st.slippage)
        unfilled = sim.status == UNFILLED
    counts["refuses_rr"] = int(rt.size)
    counts["admis"] = int(at.size)
    counts["admis_non_executes"] = int(unfilled.sum())
    ut, uc = at[unfilled], ac[unfilled]
    u_idx = np.nonzero(unfilled)[0].tolist()
    for hh in (20, 60):
        vals = fws[hh][ut, uc] if ut.size else np.zeros(0, dtype=np.float32)
        out[f"F0h.unfilled.h{hh}"] = {"v": vals.astype(np.float64).tolist(), "week": g.week[ut].tolist(),
                                      "det": [admitted["det"][i] for i in u_idx],
                                      "w": [admitted["w"][i] for i in u_idx]}  # fmt: skip
    return out, counts


def _fwd32(g: GroupData, h: int) -> F32:
    """Forward excess of horizon ``h`` as float32, without adding it to the group's cache (F0 reads five
    horizons the cells never use; float32 halves their footprint)."""
    cached = g._fwd.get(h)
    if cached is not None:
        return cached.astype(np.float32)
    return forward_excess(g.p, g.f, h).astype(np.float32)


def _weighted_weekly(week: I64, v: F64, w: F64) -> tuple[I64, F64]:
    """Weighted mean of each week (weights = inverse sampling fractions of the strata)."""
    if v.size == 0:
        return np.zeros(0, dtype=np.int64), np.zeros(0)
    uw, inv = np.unique(week, return_inverse=True)
    sw = np.bincount(inv, weights=w)
    return uw.astype(np.int64), np.bincount(inv, weights=w * v) / np.where(sw > 0, sw, 1.0)


def summarize_forward(parts: Mapping[str, list[Any]], seed: int) -> dict[str, Any]:
    v = np.array(parts.get("v", []), dtype=np.float64)
    w_ = np.array(parts.get("week", []), dtype=np.int64)
    weights = np.array(parts["w"], dtype=np.float64) if parts.get("w") else None
    dets = np.array(parts["det"], dtype=object) if parts.get("det") else None
    ok = np.isfinite(v)
    if weights is not None:
        ok &= np.isfinite(weights) & (weights > 0)
    v, w_ = v[ok], w_[ok]
    wt = weights[ok] if weights is not None else None
    mean: float | None
    if wt is not None and v.size:  # stratified sample: means weighted by the inverse sampling fractions
        mean = float(np.average(v, weights=wt))
        wk, ws = _weighted_weekly(w_, v, wt)
    else:
        mean = _mean(v) if v.size else None
        wk, ws = S.weekly_series(w_, v) if v.size else (np.zeros(0, dtype=np.int64), np.zeros(0))
    out: dict[str, Any] = {"n": int(v.size), "exces_moyen": _f(mean)}
    if wt is not None:
        out["ponderation"] = "inverse de la fraction échantillonnée de chaque détecteur"
    if v.size:
        out["t_semaine"] = _f(S.newey_west_t(ws, 4) if ws.size > 2 else math.nan, 3)
        lo, hi = S.block_bootstrap_ci(ws, 4, 1000, seed) if ws.size > 2 else (math.nan, math.nan)
        out["ic"] = [_f(lo), _f(hi)]
    if dets is not None and v.size:
        d_ok = dets[ok]
        out["par_detecteur"] = {
            str(name): {"n": int((d_ok == name).sum()), "exces_moyen": _f(_mean(v[d_ok == name]))}
            for name in sorted(set(d_ok.tolist()))
        }
    if "dist" in parts and v.size:
        d = np.array(parts["dist"], dtype=np.float64)[ok]

        def wmean(m: Bool) -> float | None:
            if not m.any():
                return None
            return float(np.average(v[m], weights=wt[m])) if wt is not None else _mean(v[m])

        out["par_distance_plus_haut_52s"] = {
            name: {"n": int(m.sum()), "exces_moyen": _f(wmean(m))}
            for name, m in (
                ("<2%", d < 0.02),
                ("2–5%", (d >= 0.02) & (d < 0.05)),
                ("5–10%", (d >= 0.05) & (d < 0.10)),
                (">=10%", d >= 0.10),
            )  # fmt: skip
        }
    return out


# --- calibration: golden replay of run 2 (docs/31 §0) -------------------------------------------------


def calibration_parts(
    g: GroupData, trades: Sequence[Run2Trade], st: Settings, holds: Mapping[str, int]
) -> dict[str, list[Any]]:  # fmt: skip
    """Replay each stored run-2 trade from its fill session with the vectorised engine in run-2 conventions
    (limit = executed price, absolute stop and target, stop first, no dividend): exit day, exit reason and
    gross R must match the stored trade."""
    tr, cols, ts, te = _locate(g, trades)
    out: dict[str, list[Any]] = {"detector": [], "match": [], "r_gross_replay": [], "r_gross_run2": []}
    if not tr:
        return out
    entry = np.array([t.entry for t in tr])
    stop = np.array([t.stop for t in tr])
    target = np.array([t.target for t in tr])
    sp = g.f.pk.pos[te, cols] - 1
    base = date(1970, 1, 1).toordinal()
    for det in sorted({t.detector for t in tr}):
        idx = np.nonzero(np.array([t.detector == det for t in tr]))[0]
        spec = ExitSpec("run2", int(holds.get(det, 20)), target_on_fill_bar="always")
        od = absolute_orders(cols[idx], sp[idx], entry[idx], stop[idx], target[idx], 1)
        sim = simulate(g.bars, od, spec, st.slippage, dividends=False)
        xt = calendar_of(g.f.pk, cols[idx], sim.exit_pos)
        with np.errstate(invalid="ignore", divide="ignore"):
            r = sim.gross_return / sim.risk_fraction
        names = {0: "stop", 1: "gap_stop", 2: "target", 3: "time"}
        for k, j in enumerate(idx.tolist()):
            t = tr[j]
            ok = bool(sim.status[k] == CLOSED and xt[k] >= 0)
            xday = (
                date.fromordinal(base + int(g.p.days[xt[k]].astype("datetime64[D]").astype(np.int64))) if ok else None
            )
            same = ok and xday == t.exit_day and names.get(int(sim.reason[k])) == t.reason and sim.entry[k] == t.entry
            out["detector"].append(det)
            out["match"].append(bool(same))
            out["r_gross_replay"].append(float(r[k]) if ok else math.nan)
            out["r_gross_run2"].append(t.r_gross)
    return out


CALIBRATION_SCOPE = (
    "entrées rejouées depuis le prix exécuté du run 2 (limite = prix exécuté, séance d'exécution stockée) : "
    "les règles d'entrée (prix maximal, validité, ouverture en gap) ne sont PAS testées — le prix maximal "
    "n'est pas stocké dans le run 2 ; seules les sorties et le R brut le sont"
)


def calibration_verdict(parts: Mapping[str, list[Any]], totals: Mapping[str, int]) -> dict[str, Any]:
    """Concordance over **all** stored run-2 trades (``totals`` per detector): a trade that cannot be located
    in the panels (instrument or session missing) counts as a mismatch, and ≥ 99 % must be located."""
    det = np.array(parts.get("detector", []), dtype=object)
    match = np.array(parts.get("match", []), dtype=bool)
    rr = np.array(parts.get("r_gross_replay", []), dtype=np.float64)
    r2 = np.array(parts.get("r_gross_run2", []), dtype=np.float64)
    total = int(sum(totals.values()))
    located = int(match.size)
    rows: dict[str, Any] = {}
    ok_means = True
    for d in sorted(set(totals) | set(det.tolist())):
        m = det == d
        n_d = int(totals.get(str(d), 0))
        with np.errstate(invalid="ignore"):
            diff = float(np.nanmean(rr[m]) - np.nanmean(r2[m])) if m.any() else math.nan
        ok_means &= math.isfinite(diff) and abs(diff) <= 0.02
        rows[str(d)] = {"n": n_d, "localises": int(m.sum()),
                        "concordance": _f(float(match[m].sum()) / n_d, 4) if n_d else None,
                        "r_brut_rejeu": _f(float(np.nanmean(rr[m])) if m.any() else None),
                        "r_brut_run2": _f(float(np.nanmean(r2[m])) if m.any() else None),
                        "ecart_moyen_r": _f(diff)}  # fmt: skip
    rate = float(match.sum()) / total if total else 0.0
    located_share = located / total if total else 0.0
    valid = total > 0 and rate >= 0.99 and located_share >= 0.99 and ok_means
    return {
        "verdict": "VALIDE" if valid else "INVALIDE",
        "concordance": _f(rate, 4),
        "trades_run2": total,
        "trades_localises": located,
        "part_localisee": _f(located_share, 4),
        "par_detecteur": rows,
        "regle": "≥ 99 % de TOUS les trades du run 2 identiques (jour et motif de sortie, prix d'entrée ; un trade "
        "non localisé compte comme différent), ≥ 99 % localisés, et R brut moyen à ±0,02 R par détecteur",
        "portee": CALIBRATION_SCOPE,
    }
