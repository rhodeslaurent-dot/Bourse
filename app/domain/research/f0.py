# ruff: noqa: E501
"""F0 — diagnostics of the production pipeline (docs/31 §1.4). Never candidates. Pure.

(a) run-2 decomposition from its stored trades; (b) matched geometry: the run-2 trades against random entries
of the same day with identical stop and target distances in ATR; (c) the d7 frozen entries under 11 exit
variants; (d)–(f) forward excess returns of vectorised detector conditions; (g)–(h) refused and unfilled
setups of the real detectors (``run_detectors`` path: zones, admission at the maximum entry price), evaluated
only on the days that pass the vectorised necessary conditions.
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
from app.domain.research.entries import absolute_orders, calendar_of
from app.domain.research.exits import CLOSED, GAP_STOP, STOP, TRAIL, UNFILLED, ExitSpec, Orders, simulate
from app.domain.research.panel import F64, I64, Bool, pack, rolling_min, shift, unpack
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


def _r_prod(g: GroupData, cols: I64, sim_entry: F64, sim: Any, st: Settings, stop_exec: F64) -> F64:
    """Net R (CTO, dividends ignored as in run 2) in the production risk unit (entry − slipped stop)."""
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
    )  # fmt: skip
    e = economics(x, st.cto, st.risk_budget_eur, st.notional_cap_eur)
    return np.where(sim.status == CLOSED, e.r_unit, np.nan)


def _diff_stats(week: I64, diff: F64, seed: int, draws: int = 1000) -> dict[str, Any]:
    wk, ws = S.weekly_series(week, diff)
    lo, hi = S.block_bootstrap_ci(ws, 4, draws, seed) if ws.size > 2 else (math.nan, math.nan)
    return {"n": int(np.isfinite(diff).sum()), "difference_moyenne": _f(_mean(diff)), "ic": [_f(lo), _f(hi)],
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
        d["r"] += r.tolist()
        d["eur"] += (r * 300.0).tolist()
        d["week"] += g.week[ts].tolist()
    return out


# --- (d)–(f) forward excess of vectorised detector conditions ------------------------------------------------


def _fwd_parts(g: GroupData, mask: Bool, h: int, rows: tuple[int, int]) -> dict[str, list[Any]]:
    m = mask.copy()
    m[: rows[0]] = False
    m[rows[1] + 1 :] = False
    t, c = np.nonzero(m)
    v = g.fwd(h)[t, c]
    return {"v": v.tolist(), "week": g.week[t].tolist()}


def forward_cells(g: GroupData, st: Settings, rows: tuple[int, int]) -> dict[str, dict[str, list[Any]]]:
    det = st.detectors
    p, f = g.p, g.f
    out: dict[str, dict[str, list[Any]]] = {}
    d3 = T.d3_base(p, f, det.get("d3_range_expansion") or {})
    for h in (1, 3, 5):
        out[f"F0d.d3_inverted.h{h}"] = _fwd_parts(g, d3, h, rows)
    d2p = det.get("d2_breakout") or {}
    d2 = T.d2_base(p, f, d2p)
    with np.errstate(invalid="ignore", divide="ignore"):
        dist = p.close / f.pivot60 - 1
    for name, lo, hi in (("0_1pct", 0.0, 0.01), ("1_3pct", 0.01, 0.03), ("gt3pct", 0.03, math.inf)):
        with np.errstate(invalid="ignore"):
            out[f"F0e.d2_distance.{name}.h20"] = _fwd_parts(g, d2 & (dist > lo) & (dist <= hi), 20, rows)
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
        out[f"F0f.no_target.h{h}"] = _fwd_parts(g, no7 | no2, h, rows)
    return out


# --- (g)–(h) the real detectors on pre-filtered days ---------------------------------------------------------


def production_parts(
    g: GroupData, st: Settings, rows: tuple[int, int], prod: Mapping[str, Any], max_candidates: int, seed: int
) -> tuple[dict[str, dict[str, list[Any]]], dict[str, int]]:  # fmt: skip
    """Run ``DETECTORS`` (zones, admission at the maximum entry price, CTO round-trip fees) on the days passing
    the vectorised necessary conditions; collect R/R-refused setups (forward excess, by distance to the
    52-week high) and admitted setups whose order never filled (gap above the maximum price)."""
    det = st.detectors
    p, f = g.p, g.f
    names = [n for n in ("d2_breakout", "d3_range_expansion", "d4_pullback", "d7_trend_quality")
             if (det.get(n) or {}).get("enabled", False)]  # fmt: skip
    pre = {
        "d2_breakout": lambda: T.d2_base(p, f, det["d2_breakout"]),
        "d3_range_expansion": lambda: T.d3_base(p, f, det["d3_range_expansion"]),
        "d4_pullback": lambda: T.d4_prefilter(p, f, det["d4_pullback"]),
        "d7_trend_quality": lambda: T.d7_base(p, f, det["d7_trend_quality"]),
    }
    cand = np.zeros(p.shape, dtype=bool)
    for n in names:
        cand |= pre[n]()
    cand[: rows[0]] = False
    cand[rows[1] + 1 :] = False
    t_all, c_all = np.nonzero(cand)
    counts = {"candidats": int(t_all.size), "evalues": int(min(t_all.size, max_candidates))}
    if t_all.size > max_candidates:
        keep = np.sort(np.random.default_rng(seed + g.gid).choice(t_all.size, max_candidates, replace=False))
        t_all, c_all = t_all[keep], c_all[keep]
    zp = prod.get("zones") or {}
    wick, buf = float(prod.get("wick_buffer_pct", 0.005)), float(prod.get("stop_buffer_atr_mult", 0.25))
    k_rr, expiry = float(prod.get("min_reward_risk", 2.0)), int(prod.get("expiry_sessions", 3))
    pos = f.pk.pos
    o, h, lo, c, v = (pack(x, f.pk) for x in (p.open, p.high, p.low, p.close, p.volume))
    refused: dict[str, list[Any]] = {"t": [], "c": [], "dist": []}
    admitted: dict[str, list[Any]] = {"t": [], "c": [], "pmax": [], "stop": [], "target": []}
    for t, col in zip(t_all.tolist(), c_all.tolist(), strict=True):
        k = int(pos[t, col])
        a = max(0, k - 279)
        window = [Bar(float(o[j, col]), float(h[j, col]), float(lo[j, col]), float(c[j, col]), int(v[j, col]))
                  for j in range(a, k + 1)]  # fmt: skip
        rank = f.rs_rank[t, col]
        ctx = DetectorContext(float(rank) if np.isfinite(rank) else None, [], wick, buf)
        cp = CostParams(st.cto.fees, ttf_rate=float(p.ttf_rate[col]), ftt_rate=float(p.ftt_rate[col]),
                        fx_fee_pct=float(p.fx_fee_pct[col]))  # fmt: skip
        for name in names:
            dp = det[name]
            if not DETECTORS[name](dict(dp)).detect(window, ctx):
                continue
            ff = compute_daily_features(window)
            zones = detect_zones(
                window[-int(zp.get("lookback_sessions", 250)) :], ff.atr14 or 0.0, int(zp.get("pivot_window", 5)),
                float(zp.get("merge_atr_mult", 0.5)), int(zp.get("min_touches", 2)),
            )  # fmt: skip
            zctx = DetectorContext(ctx.rs_rank, zones, wick, buf)
            for s_ in DETECTORS[name](dict(dp)).detect(window, zctx):
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
                elif adm.status == Status.ADMITTED and not s_.warnings and adm.p_max is not None:
                    admitted["t"].append(t)
                    admitted["c"].append(col)
                    admitted["pmax"].append(adm.p_max)
                    admitted["stop"].append(s_.stop)
                    admitted["target"].append(tgt)
    out: dict[str, dict[str, list[Any]]] = {}
    rt, rc = np.array(refused["t"], dtype=np.int64), np.array(refused["c"], dtype=np.int64)
    dist = np.array(refused["dist"])
    for hh in (20, 60):
        vals = g.fwd(hh)[rt, rc] if rt.size else np.zeros(0)
        out[f"F0g.rr_refused.h{hh}"] = {"v": vals.tolist(), "week": g.week[rt].tolist(), "dist": dist.tolist()}
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
    for hh in (20, 60):
        vals = g.fwd(hh)[ut, uc] if ut.size else np.zeros(0)
        out[f"F0h.unfilled.h{hh}"] = {"v": vals.tolist(), "week": g.week[ut].tolist()}
    return out, counts


def summarize_forward(parts: Mapping[str, list[Any]], seed: int) -> dict[str, Any]:
    v = np.array(parts.get("v", []), dtype=np.float64)
    w = np.array(parts.get("week", []), dtype=np.int64)
    ok = np.isfinite(v)
    v, w = v[ok], w[ok]
    out: dict[str, Any] = {"n": int(v.size), "exces_moyen": _f(_mean(v))}
    if v.size:
        wk, ws = S.weekly_series(w, v)
        out["t_semaine"] = _f(S.newey_west_t(ws, 4) if ws.size > 2 else math.nan, 3)
        lo, hi = S.block_bootstrap_ci(ws, 4, 1000, seed) if ws.size > 2 else (math.nan, math.nan)
        out["ic"] = [_f(lo), _f(hi)]
    if "dist" in parts and v.size:
        d = np.array(parts["dist"], dtype=np.float64)[ok]
        out["par_distance_plus_haut_52s"] = {
            name: {"n": int(m.sum()), "exces_moyen": _f(_mean(v[m]))}
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


def calibration_verdict(parts: Mapping[str, list[Any]], located: int, total: int) -> dict[str, Any]:
    det = np.array(parts.get("detector", []), dtype=object)
    match = np.array(parts.get("match", []), dtype=bool)
    rr = np.array(parts.get("r_gross_replay", []), dtype=np.float64)
    r2 = np.array(parts.get("r_gross_run2", []), dtype=np.float64)
    rows: dict[str, Any] = {}
    ok_means = True
    for d in sorted(set(det.tolist())):
        m = det == d
        diff = float(np.nanmean(rr[m]) - np.nanmean(r2[m])) if m.any() else math.nan
        ok_means &= math.isfinite(diff) and abs(diff) <= 0.02
        rows[str(d)] = {"n": int(m.sum()), "concordance": _f(float(match[m].mean()), 4),
                        "r_brut_rejeu": _f(float(np.nanmean(rr[m]))), "r_brut_run2": _f(float(np.nanmean(r2[m]))),
                        "ecart_moyen_r": _f(diff)}  # fmt: skip
    rate = float(match.mean()) if match.size else 0.0
    valid = match.size > 0 and rate >= 0.99 and ok_means
    return {
        "verdict": "VALIDE" if valid else "INVALIDE",
        "concordance": _f(rate, 4),
        "trades_run2": total,
        "trades_localises": located,
        "par_detecteur": rows,
        "regle": "≥ 99 % des trades identiques (jour et motif de sortie, prix d'entrée) et R brut moyen à ±0,02 R par détecteur",
    }
