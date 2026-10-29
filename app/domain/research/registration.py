# ruff: noqa: E501
"""One registration run (docs/31 §3): stage 0 → gate → path rules and stage 1 → stage 2 → classification.

Pure orchestration over the panels: two passes at most (the second only when a cell passes the stage-0
gate), one venue group in memory at a time. Returns raw results that ``report.build_report`` turns into the
aggregated report (statistics only).
"""

from __future__ import annotations

import dataclasses
import math
from collections import defaultdict
from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import date
from typing import Any

import numpy as np

from app.domain.research import f0 as F0
from app.domain.research import stats as S
from app.domain.research.classify import Tier, a2_met, classify, potential_candidate
from app.domain.research.costs import breakeven_hazard_multiple
from app.domain.research.entries import exit_model, make_orders
from app.domain.research.exits import PathRule, simulate
from app.domain.research.panel import Panel
from app.domain.research.pipeline import (
    DenseExcess,
    GroupData,
    Settings,
    Signals,
    Trades,
    _f,
    _mean,
    _summary,
    _window,
    add_excess,
    cell_model,
    cell_signals,
    cell_stats,
    combo_label,
    date_shift_null,
    family_walk_forward,
    finalize_monthly,
    nw_lag_of,
    planted_power,
    prepare_group,
    replay,
    run_cells,
    series_of,
    signal_rows,
    trade_rows,
    walk_forward_choice,
    weekly_block_of,
    weekly_lag_of,
    weekly_matrix,
)
from app.domain.research.prereg import Cell, Prereg, Segments

Progress = Callable[[str], None]
POWER_UPPER_BOUND = "borne supérieure : A2 (effet hors échantillon) et A5 (plis) ne sont pas simulés"
RULES: tuple[PathRule, ...] = ("stop_first", "ohlc", "target_first")


def _groups(panels: Sequence[Panel], st: Settings) -> Iterator[GroupData]:
    for k, p in enumerate(panels):
        if p.shape[1] == 0:
            continue
        yield prepare_group(k, p, st)


def _key(tr: Trades) -> Any:
    return tr["inst"] * 100_000 + tr["t"]


def _monthly(c: Cell) -> bool:
    return c.mode != "event"


SEALED_F0_LABEL = (
    "période scellée — trades du run 2 déjà vus par le run 2 (signal ≥ début de la période scellée) : "
    "diagnostic séparé, jamais mêlé à la découverte"
)


class F0Accumulator:
    """Collects the F0 parts of every group (lists only, no prices). The run-2 trades of (b) and (c) are
    split by signal day: the discovery window (the F0 cells) and, reported apart and labelled, the sealed
    period (signal on or after ``holdout_start``); embargo trades enter neither."""

    def __init__(self, st: Settings, run2: Sequence[F0.Run2Trade], prod: Mapping[str, Any], seed: int,
                 max_candidates: int, segments: Segments | None = None) -> None:  # fmt: skip
        self.st, self.run2, self.prod, self.seed, self.max_candidates = st, run2, prod, seed, max_candidates
        if segments is None:
            self.run2_disc, self.run2_sealed = list(run2), []
        else:
            d0, d1 = segments.discovery
            self.run2_disc = [t for t in run2 if d0 <= t.signal_day <= d1]
            self.run2_sealed = [t for t in run2 if t.signal_day >= segments.holdout_start]
        self.h0: dict[str, dict[str, list[Any]]] = defaultdict(lambda: {"trade": [], "control": [], "week": []})
        self.d7: dict[str, dict[str, list[Any]]] = defaultdict(lambda: {"r": [], "eur": [], "week": []})
        self.h0_sealed: dict[str, dict[str, list[Any]]] = defaultdict(lambda: {"trade": [], "control": [], "week": []})
        self.d7_sealed: dict[str, dict[str, list[Any]]] = defaultdict(lambda: {"r": [], "eur": [], "week": []})
        self.fwd: dict[str, dict[str, list[Any]]] = defaultdict(lambda: defaultdict(list))
        self.counts: dict[str, int] = defaultdict(int)
        self.dividend_days: dict[str, list[date]] = {}

    def _run2_parts(self, g: GroupData, trades: Sequence[F0.Run2Trade], h0: dict[str, dict[str, list[Any]]],
                    d7: dict[str, dict[str, list[Any]]]) -> None:  # fmt: skip
        if not trades:
            return
        st = self.st
        holds = {d: F0.detector_hold(d, st.detectors) for d in {t.detector for t in self.run2}}
        for det, part in F0.h0_geometry_parts(g, trades, st, holds, 20, self.seed).items():
            for fld, vals in part.items():
                h0[det][fld] += vals
        for name, part in F0.d7_exit_parts(g, trades, st, holds.get("d7_trend_quality", 60)).items():
            for fld, vals in part.items():
                d7[name].setdefault(fld, []).extend(vals)

    def __call__(self, g: GroupData, rows: tuple[int, int]) -> None:
        st = self.st
        isins = {t.isin for t in self.run2}
        base = date(1970, 1, 1).toordinal()
        div = g.bars.div
        for col, isin in enumerate(g.p.isins):
            if isin in isins:
                k = np.nonzero(div[:, col] > 0)[0]
                t = g.f.pk.order[k, col]
                self.dividend_days[isin] = [date.fromordinal(base + int(d)) for d in
                                            g.p.days[t].astype("datetime64[D]").astype(np.int64).tolist()]  # fmt: skip
        self._run2_parts(g, self.run2_disc, self.h0, self.d7)
        self._run2_parts(g, self.run2_sealed, self.h0_sealed, self.d7_sealed)
        for cid, part in F0.forward_cells(g, st, rows).items():
            for fld, vals in part.items():
                self.fwd[cid][fld] += vals
        if self.prod:
            parts, counts = F0.production_parts(g, st, rows, self.prod, self.max_candidates, self.seed)
            for cid, part in parts.items():
                for fld, vals in part.items():
                    self.fwd[cid][fld] += vals
            for fld, cnt in counts.items():
                self.counts[fld] += cnt

    @staticmethod
    def _run2_rows(h0: Mapping[str, dict[str, list[Any]]], d7: Mapping[str, dict[str, list[Any]]], seed: int,
                   period: str) -> dict[str, Any]:  # fmt: skip
        out: dict[str, Any] = {}
        for det, d in sorted(h0.items()):
            tr, ct = np.array(d["trade"], dtype=np.float64), np.array(d["control"], dtype=np.float64)
            wk = np.array(d["week"], dtype=np.int64)
            row = F0._diff_stats(wk, tr - ct, seed)
            row.update(r_trades=_f(_mean(tr)), r_controles=_f(_mean(ct)), periode=period)
            out[f"F0b.h0_geometry.{det}"] = row
        ref = d7.get("e4_production")
        if ref is not None:
            r0 = np.array(ref["r"], dtype=np.float64)
            wk = np.array(ref["week"], dtype=np.int64)
            for name, d in d7.items():
                r = np.array(d["r"], dtype=np.float64)
                row = F0._diff_stats(wk, r - r0, seed)
                row.update(r_moyen=_f(_mean(r)), eur_300_moyen=_f(_mean(r * 300.0), 2),
                           delta_eur_300=_f(_mean((r - r0) * 300.0), 2), periode=period,
                           libelle=F0.D7_LABELS.get(name, name))  # fmt: skip
                if d.get("r_run2"):  # the base of the replay against run 2 (round 2: −0.51 vs −0.137 unexplained)
                    row.update(r_moyen_sans_ecart=_f(_mean(np.array(d["r_sans_ecart"], dtype=np.float64))),
                               r_net_run2_memes_trades=_f(_mean(np.array(d["r_run2"], dtype=np.float64))))  # fmt: skip
                out[f"F0c.d7.{name}"] = row
        return out

    def summary(self, seed: int) -> dict[str, Any]:
        out: dict[str, Any] = self._run2_rows(self.h0, self.d7, seed, "decouverte")
        for cid, d in sorted(self.fwd.items()):
            out[cid] = F0.summarize_forward(d, seed)
        out["_comptes_production"] = dict(self.counts)
        if self.run2_sealed:
            out["periode_scellee_run2"] = {
                "libelle": SEALED_F0_LABEL,
                "trades_run2": len(self.run2_sealed),
                "cellules": self._run2_rows(self.h0_sealed, self.d7_sealed, seed, "scellee"),
            }
        return out


def existing_detectors(run2: Sequence[F0.Run2Trade], prereg: Prereg, seed: int) -> dict[str, Any]:
    """Run-2 production trades of each detector on the walk-forward test years: mean net R and its block
    bootstrap interval — an upper bound below zero calls for an ADR (deactivation or rework)."""
    out: dict[str, Any] = {}
    wins = prereg.segments.wf_test
    by: dict[str, list[F0.Run2Trade]] = defaultdict(list)
    for t in run2:
        if t.production and any(a <= t.signal_day <= b for a, b in wins):
            by[t.detector].append(t)
    for det, ts in sorted(by.items()):
        r = np.array([t.r_net for t in ts])
        wk = np.array([(t.signal_day.toordinal() - date(1970, 1, 1).toordinal() + 3) // 7 for t in ts], dtype=np.int64)
        _, ws = S.weekly_series(wk, r)
        lo, hi = S.block_bootstrap_ci(ws, 4, 2000, seed) if ws.size > 2 else (math.nan, math.nan)
        out[det] = {"n": int(r.size), "r_net_moyen_oos": _f(float(r.mean())), "ic": [_f(lo), _f(hi)],
                    "r_net_moyen_hebdo_oos": _f(_mean(ws)) if ws.size else None,  # same quantity as the interval
                    "adr_requis": bool(math.isfinite(hi) and hi < float(prereg.thresholds["existing_detectors_ci_high_max"]))}  # fmt: skip
    return out


# --- pass 2: path rules and stage 1 ------------------------------------------------------------------


def _filter_signals(sig: Signals, keep: set[int], gid: int) -> Signals:
    k = (gid * 1_000_000 + sig.cols) * 100_000 + sig.ts
    m = np.isin(k, np.array(sorted(keep), dtype=np.int64))
    return Signals(sig.ts[m], sig.cols[m], None if sig.p_max is None else sig.p_max[m],
                   None if sig.score is None else sig.score[m], None if sig.overlay is None else sig.overlay[m])  # fmt: skip


def second_pass(
    panels: Sequence[Panel], st: Settings, prereg: Prereg, path_cells: Sequence[Cell], stage1_cells: Sequence[Cell],
    keys: Mapping[str, set[int]], window: tuple[date, date], progress: Progress | None,
) -> tuple[dict[str, dict[str, Trades]], dict[str, dict[str, dict[str, Trades]]]]:  # fmt: skip
    """Re-simulate the frozen signals: path rules of the gate passers; the exit models X1–X8 of the stage-1
    triggers under the three path rules (orders invalidated as with X3, so the entries stay frozen)."""
    rules: dict[str, dict[str, list[Trades]]] = defaultdict(lambda: defaultdict(list))
    exits: dict[str, dict[str, dict[str, list[Trades]]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for g in _groups(panels, st):
        rows = signal_rows(g.p.days, window[0], window[1], prereg.segments.end_margin_sessions)
        if rows[1] < rows[0]:
            continue
        if progress:
            progress(f"{g.p.group} : seconde passe (règles de parcours, étape 1)")
        todo = {c.id: c for c in (*path_cells, *stage1_cells)}
        path_ids, s1_ids = {c.id for c in path_cells}, {c.id for c in stage1_cells}
        for c in todo.values():
            sig = cell_signals(g, c, st, rows)
            sig = _filter_signals(sig, keys.get(c.id, set()), g.gid)
            if sig.ts.size == 0:
                continue
            cap = c.entry_cap_atr
            ref_model = cell_model(c)
            ref_od = make_orders(g.p, g.f, sig.ts, sig.cols, ref_model, c.validity, cap, sig.p_max)
            if c.id in path_ids:
                for rule in RULES:
                    sim = simulate(g.bars, ref_od, ref_model.spec, st.slippage, rule)
                    rules[c.id][rule].append(trade_rows(g, c, sig, ref_od, sim, ref_model, None, st, c.unit))
            if c.id in s1_ids:
                for code in prereg.stage1_exits:
                    m = exit_model(code, c.h)
                    od = make_orders(g.p, g.f, sig.ts, sig.cols, m, c.validity, cap, sig.p_max)
                    od = dataclasses.replace(od, cancel_below=ref_od.cancel_below)
                    for rule in RULES:
                        sim = simulate(g.bars, od, m.spec, st.slippage, rule)
                        exits[c.id][code][rule].append(trade_rows(g, c, sig, od, sim, m, None, st, "r_ref"))
    r_out = {cid: {k: Trades.concat(v) for k, v in d.items()} for cid, d in rules.items()}
    e_out = {
        cid: {code: {k: Trades.concat(v) for k, v in d2.items()} for code, d2 in d.items()} for cid, d in exits.items()
    }
    return r_out, e_out


def stage1_stats(
    cell: Cell, variants: Mapping[str, Mapping[str, Trades]], prereg: Prereg, slots: int
) -> dict[str, Any]:
    """Paired ΔR (R_ref) and Δ€ of each exit model against X3(h*) on the same frozen entries. Path-rule
    stability: under each rule, the exit is paired with X3 **under the same rule**; the sign of the paired ΔR
    and the exit's rank among the models must be the same under the three rules."""
    th = prereg.thresholds["stage1"]
    inf = prereg.inference
    bases = {rule: variants.get("X3", {}).get(rule) for rule in RULES}
    base = bases["stop_first"]
    out: dict[str, Any] = {}
    if base is None or base.n == 0:
        return out
    b = base.closed
    bk = _key(b)
    b_dd = replay(b, slots).get("max_drawdown_r")

    def paired(code: str, rule: PathRule) -> float | None:
        v, x3 = variants.get(code, {}).get(rule), bases[rule]
        if v is None or x3 is None or v.n == 0 or x3.n == 0:
            return None
        vc, xc = v.closed, x3.closed
        _, i1, i2 = np.intersect1d(_key(vc), _key(xc), return_indices=True)
        return _mean(vc["r"][i1] - xc["r"][i2])

    codes = sorted(variants)
    by_rule = {rule: {code: paired(code, rule) for code in codes} for rule in RULES}
    ranks: dict[str, dict[str, int | None]] = defaultdict(dict)
    for rule, vals in by_rule.items():
        ok = sorted((c for c in codes if vals[c] is not None), key=lambda c: (-(vals[c] or 0.0), c))
        for code in codes:
            ranks[code][rule] = ok.index(code) + 1 if code in ok else None
    weekly: list[tuple[Any, Any]] = []
    names: list[str] = []
    for code in codes:
        v = variants[code].get("stop_first")
        if v is None:
            continue
        vc = v.closed
        weekly.append(S.weekly_series(vc["week"], vc["r"]))
        names.append(code)
        common, ia, ib = np.intersect1d(_key(vc), bk, return_indices=True)
        d = vc["r"][ia] - b["r"][ib]
        de = vc["eur"][ia] - b["eur"][ib]
        sub = Trades({"r": d, "eur": de, "day": vc["day"][ia], "week": vc["week"][ia]})
        oos = np.zeros(common.size, dtype=bool)
        folds = 0
        for a_, b_ in prereg.segments.wf_test:
            w = _window(sub, a_, b_)
            oos |= w
            folds += int((_mean(d[w]) or 0) > 0)
        _, ws = S.weekly_series(sub["week"][oos], d[oos])
        hold = max(int(exit_model(code, cell.h, cell.max_hold).spec.max_hold), int(cell_model(cell).spec.max_hold))
        block = max(int(inf["block_weeks"]), math.ceil(hold / 5))
        lo, hi = S.block_bootstrap_ci(ws, block, int(inf["bootstrap_draws"]), prereg.seed)
        signs = {np.sign(x) for x in (by_rule[rule][code] for rule in RULES) if x is not None}
        complete = all(by_rule[rule][code] is not None for rule in RULES)
        rank_ok = complete and len({ranks[code][rule] for rule in RULES}) == 1
        dd = replay(vc, slots).get("max_drawdown_r")
        dd_ok = dd is not None and b_dd is not None and (b_dd == 0 or dd >= b_dd * (1 + float(th["max_dd_worse"])))
        row: dict[str, Any] = {
            "n": int(common.size), "delta_r_moyen": _f(_mean(d)), "delta_r_oos": _f(_mean(d[oos])),
            "delta_r_oos_ic": [_f(lo), _f(hi)], "bloc_bootstrap_semaines": block, "delta_eur_300": _f(_mean(de), 2),
            "plis_positifs": folds,
            "delta_r_par_regle": {rule: _f(by_rule[rule][code]) for rule in RULES},
            "rang_par_regle": {rule: ranks[code][rule] for rule in RULES},
            "signe_stable": complete and len(signs) == 1, "rang_stable": rank_ok,
            "stable_regles_parcours": complete and len(signs) == 1 and rank_ok,
            "r_stress_moyen": _f(_mean(vc["r_stress"])),
            "recul_max_r": dd, "recul_max_r_x3": b_dd, "recul_ok": dd_ok,
            "multiple_risque_faillite_equilibre": _f(breakeven_hazard_multiple(vc["r"], vc["hazard"]), 2),
        }  # fmt: skip
        row["retenue"] = bool(
            code != "X3" and _gt0(row["delta_r_oos"], float(th["min_delta_r"])) and lo > 0
            and folds >= int(th["min_positive_folds"]) and row["stable_regles_parcours"]
            and (row["r_stress_moyen"] or -1) > 0 and dd_ok
        )  # fmt: skip
        out[code] = row
    if len(weekly) >= 2:
        _, mat = weekly_matrix(weekly)
        pbo = S.pbo_cscv(mat, int(inf["pbo_blocks"]))
        for code in names:
            out[code]["pbo_famille_sorties"] = _f(pbo, 3)
            if out[code]["retenue"] and not (math.isfinite(pbo) and pbo <= float(th["max_pbo"])):
                out[code]["retenue"] = False
    return out


def _gt0(x: float | None, bound: float) -> bool:
    return x is not None and x >= bound


# --- the registration ----------------------------------------------------------------------------------


def run_registration(
    panels: Sequence[Panel],
    prereg: Prereg,
    st: Settings,
    run2: Sequence[F0.Run2Trade] = (),
    prod: Mapping[str, Any] | None = None,
    stages: str = "all",
    holdout_cells: Sequence[str] | None = None,
    progress: Progress | None = None,
    f0: bool = True,
    max_f0_candidates: int = 20_000,
) -> dict[str, Any]:
    if prereg.is_exit_plan:  # registration n° 6: exit rules on frozen entries, paired per trade (docs/34 §34.13)
        from app.domain.research.exit_rules import run_exit_registration

        return run_exit_registration(panels, prereg, st, prod or {}, stages, holdout_cells, progress)
    if prereg.is_entry_plan:  # registration n° 4: entry policies paired per signal (docs/34 §34.11)
        from app.domain.research.entry_policies import run_entry_registration

        return run_entry_registration(panels, prereg, st, prod or {}, stages, holdout_cells, progress)
    seg = prereg.segments
    th, inf = prereg.thresholds, prereg.inference
    st = dataclasses.replace(st, seal_start=seg.holdout_start)  # data-quality exclusions never cross the seal
    holdout = holdout_cells is not None
    slots, slot_warning = portfolio_slots(prereg, st)
    if slot_warning and progress:
        progress(slot_warning)
    if holdout:  # the sealed period: the holdout, or the plan's old replication period (registration n° 3)
        window = prereg.sealed_window
        cells = [c for c in prereg.cells if c.id in set(holdout_cells or ()) and c.tier != "f0"]
    else:
        window = seg.discovery
        cells = prereg.confirmatory + prereg.exploratory
    acc = F0Accumulator(st, run2, prod or {}, prereg.seed, max_f0_candidates, seg) if f0 and not holdout else None
    res = run_cells(_groups(panels, st), cells, st, window, seg.end_margin_sessions, progress=progress, hook=acc)
    trades: dict[str, Trades] = {}
    for c in cells:
        tr = finalize_monthly(res.trades.get(c.id, Trades({})), c, st)
        trades[c.id] = add_excess(tr, _monthly(c)) if tr.n else tr
    stats: dict[str, dict[str, Any]] = {}
    for c in cells:
        s = cell_stats(trades[c.id], c, prereg, st)
        s["tier"] = c.tier
        s["gate_label"] = c.gate
        s["duree_max"] = cell_model(c).spec.max_hold
        s["comptes"] = list(c.accounts)
        s["signaux_masques_ost"] = res.masked_signals.get(c.id, 0)
        if _monthly(c) and trades[c.id].n:
            s["trades_portefeuille"] = replay(trades[c.id].closed, slots).get("taken", 0)
        stats[c.id] = s
    out: dict[str, Any] = {"segment": "scelle" if holdout else "decouverte", "cellules": stats,
                           "lignes_portefeuille": slots, "avertissements": [slot_warning] if slot_warning else []}  # fmt: skip
    if res.coverage:  # registration n° 3: earnings coverage per venue group (a group below the floor does not count)
        out["couverture_resultats"] = res.coverage
        low = sorted(k for k, v in res.coverage.items() if not v.get("compte_dans_le_verdict"))
        if low:
            out["avertissements"].append(
                f"couverture des publications insuffisante (groupe(s) {', '.join(low)}) : leurs cellules « résultats » "
                f"ne produisent aucun signal et ne comptent pas dans le verdict"
            )
    if holdout:
        return out

    conf = [c for c in cells if c.tier == "confirmatory"]
    expl = [c for c in cells if c.tier == "exploratory"]
    # confirmatory: Holm over 8 (one-sided)
    pc = np.array([stats[c.id].get("p_unilateral") if stats[c.id].get("p_unilateral") is not None else 1.0
                   for c in conf])  # fmt: skip
    for c, ph, q in zip(conf, S.holm(pc), S.bh(pc), strict=True):
        stats[c.id]["p_holm"] = _f(float(ph), 6)
        stats[c.id]["q_bh"] = _f(float(q), 6)
    # exploratory: Romano–Wolf (binding), Holm over M_total, BH
    rw, mat = exploratory_romano_wolf(expl, trades, prereg)
    p_rw = rw.p_adj
    out["romano_wolf"] = {
        "bloc_semaines": rw.block, "t_max_critique_5pct": _f(rw.max_t_crit, 3),
        "lags_semaines": {c.id: int(rw.lags[j]) for j, c in enumerate(expl)},
    }  # fmt: skip
    pe = np.array([stats[c.id].get("p_unilateral") if stats[c.id].get("p_unilateral") is not None else 1.0
                   for c in expl])  # fmt: skip
    m_total = prereg.total_cells
    holm_all = S.holm(np.concatenate([pe, np.ones(max(0, m_total - pe.size))]))[: pe.size]
    q = S.bh(pe)
    for j, c in enumerate(expl):
        stats[c.id]["p_romano_wolf"] = _f(float(p_rw[j]), 6)
        stats[c.id]["lag_romano_wolf_semaines"] = int(rw.lags[j])
        stats[c.id]["p_holm_m_total"] = _f(float(holm_all[j]), 6)
        stats[c.id]["q_bh"] = _f(float(q[j]), 6)
    # families: PBO, DSR, selection walk-forward, power, minimum detectable effect
    fams: dict[str, list[Cell]] = defaultdict(list)
    for c in expl:
        fams[c.family].append(c)
    families: dict[str, Any] = {}
    for fam, cs in sorted(fams.items()):
        idx = [expl.index(c) for c in cs]
        pbo = S.pbo_cscv(mat[:, idx], int(inf["pbo_blocks"])) if idx else math.nan
        srs = [stats[c.id].get("_sr") for c in cs]
        srs_f = [x for x in srs if x is not None and math.isfinite(x)]
        var_sr = float(np.var(srs_f, ddof=1)) if len(srs_f) > 1 else 0.0
        for c in cs:
            s = stats[c.id]
            s["pbo_famille"] = _f(pbo, 3)
            s["dsr"] = _f(S.deflated_sharpe(float(s.get("_sr") or math.nan), len(cs), var_sr, int(s.get("_nobs") or 0),
                                            float(s.get("_skew") or 0.0), float(s.get("_kurt") or 3.0)), 4)  # fmt: skip
        wf = family_walk_forward(cs, trades, prereg)
        families[fam] = {"cellules": len(cs), "pbo": _f(pbo, 3), "walk_forward_selection": wf}
        for c in cs:  # A2 / A5 / A11 of an exploratory cell: the family's selection walk-forward only
            s = stats[c.id]
            for k in ("oos", "plis", "plis_positifs"):
                if k in s:
                    s[f"{k}_sous_ensemble_cellule"] = s.pop(k)
            s["walk_forward_famille"] = {
                "choix_cellule": walk_forward_choice(c.id, wf), "oos": wf["oos"], "oos_pea": wf["oos_pea"],
                "oos_capacite": wf["oos_capacite"], "plis_positifs": wf["plis_positifs"],
            }  # fmt: skip
        # power: success = what A3 needs — t ≥ the run's Romano–Wolf 5 % max-t critical value and ≥ min_t
        z_rw = max(float(th["a3"]["min_t"]), rw.max_t_crit if math.isfinite(rw.max_t_crit) else math.inf)
        families[fam].update(_power_and_mde(cs, trades, res.dense, prereg, z_rw))
    # confirmatory: Holm over the 8 — the strongest needs p ≤ alpha / 8
    z_holm = S.norm_ppf(1 - float(th["a3"]["holm_alpha"]) / max(1, len(conf)))
    for c in conf:
        families[c.id] = _power_and_mde([c], trades, res.dense, prereg, z_holm)
    # permutation (top by t_week + gate passers)
    ranked = sorted((c for c in cells if c.mode != "monthly_overlay"),
                    key=lambda c: -(stats[c.id].get("t_week") or -math.inf))  # fmt: skip
    perm = {c.id for c in ranked[: int(inf["permutation_top"])]} | {c.id for c in cells if stats[c.id].get("gate")}
    for c in cells:
        if c.id not in perm or c.mode == "monthly_overlay" or trades[c.id].n == 0:
            continue
        tr = trades[c.id].closed
        dense = res.dense.get(c.combo_key, {})
        null = date_shift_null(tr, dense, _monthly(c), int(inf["permutation_draws"]), prereg.seed + 11)
        obs = _mean(tr["excess"]) or -math.inf
        stats[c.id]["p_permutation"] = _f(S.permutation_p(obs, null), 4)
    out["familles"] = families
    out["annees_dividendes_ecartees"] = res.suspect_dividend_years
    out["qualite_donnees"] = dict(res.quality)
    out["references"] = {combo_label(k): v for k, v in res.baselines.items()}
    # pass 2: path rules of the gate passers, stage-1 exits of at most 4 triggers
    gate = [c for c in cells if stats[c.id].get("gate") and c.mode != "monthly_overlay"]
    s1 = sorted(gate, key=lambda c: -(stats[c.id].get("t_week") or 0))[: int(th["stage1"]["max_triggers"])]
    if stages in ("0",):
        s1 = []
    keys = {c.id: set(_key(trades[c.id]).tolist()) for c in gate}
    stage1: dict[str, Any] = {}
    if gate:
        rules, exits = second_pass(panels, st, prereg, gate, s1, keys, window, progress)
        for c in gate:
            by_rule = rules.get(c.id, {})
            signs = {np.sign(_mean(t.closed["r"]) or 0.0) for t in by_rule.values() if t.n}
            stats[c.id]["regles_parcours"] = {k: _summary(t.closed) for k, t in by_rule.items()}
            stats[c.id]["ambigu"] = len(signs) != 1
        for c in s1:
            stage1[c.id] = stage1_stats(c, exits.get(c.id, {}), prereg, slots)
    out["etape1"] = stage1
    # stage 2: portfolio replays (A13) for the potential candidates, within the cap
    stage2: dict[str, Any] = {}
    budget = prereg.stage2_max_cells if stages in ("2", "all") else 0
    for c in sorted(cells, key=lambda c: -(stats[c.id].get("t_week") or 0)):
        if budget <= 0:
            break
        if c.mode == "monthly_overlay" or not potential_candidate(stats[c.id], th, st.universe_label):
            continue
        tr = trades[c.id].closed
        a, b = replay(tr, slots, "r"), replay(tr, slots, "ctrl")
        ok = (
            a.get("max_drawdown_r") is not None
            and b.get("max_drawdown_r") is not None
            and (a["max_drawdown_r"] >= b["max_drawdown_r"] * float(th["a13"]["max_dd_ratio_vs_benchmark"]))
        )
        stage2[c.id] = {"cellule": a, "reference_controles": b, "ok": bool(ok)}
        stats[c.id]["portefeuille_ok"] = bool(ok)
        budget -= 1
    out["etape2"] = stage2
    # classification
    for c in cells:
        tier, crit = classify(stats[c.id], th, st.universe_label)
        stats[c.id]["niveau"] = tier.value
        stats[c.id]["criteres"] = crit
        if c.tier == "exploratory":
            pea_oos = (stats[c.id].get("walk_forward_famille") or {}).get("oos_pea") or {}
        else:
            pea_oos = (stats[c.id].get("pea") or {}).get("oos") or {}
        stats[c.id]["etiquette_pea"] = bool(crit.get("A2")) and a2_met(pea_oos, th)
    cands = [c.id for c in sorted(cells, key=lambda c: -(stats[c.id].get("t_week") or 0))
             if stats[c.id]["niveau"] == Tier.CANDIDATE.value][: int(th["candidates_max"])]  # fmt: skip
    out["candidats"] = cands
    out["surveillance"] = [c.id for c in cells if stats[c.id]["niveau"] == Tier.WATCHLIST.value]
    out["verdicts_familles"] = {
        fam: ("candidat" if any(stats[c.id]["niveau"] == Tier.CANDIDATE.value for c in cs)
              else "pas d'avantage détecté" if (families[fam].get("puissance_decision") or 0) >= float(th["power_min"])
              else "test non concluant (puissance insuffisante)")
        for fam, cs in fams.items()
    }  # fmt: skip
    out["f0"] = acc.summary(prereg.seed) if acc is not None else {}
    if acc is not None and run2:
        out["run2_decomposition"] = F0.decompose(run2, acc.dividend_days)
        out["detecteurs_existants"] = existing_detectors(run2, prereg, prereg.seed)
    out["comptes_cellules"] = {
        "confirmatoires": len(conf), "exploratoires": len(expl), "f0": len(prereg.f0),
        "etape1": sum(len(v) for v in stage1.values()), "etape2": len(stage2),
    }  # fmt: skip
    out["comptes_cellules"]["total_execute"] = sum(out["comptes_cellules"].values())
    return out


def _power_and_mde(
    cs: Sequence[Cell], trades: Mapping[str, Trades], dense: Mapping[Any, Mapping[int, DenseExcess]], prereg: Prereg,
    z_bar: float,
) -> dict[str, Any]:  # fmt: skip
    """Planted-edge power on the design of the family's largest cell, and the minimum detectable effect, at the
    critical t of the test that decides (``z_bar``). Only the significance and the mean excess (A3, A4) are
    simulated on the pseudo-trades, not the out-of-sample effect (A2) nor the folds (A5): an upper bound."""
    inf, th = prereg.inference, prereg.thresholds
    rep = max(cs, key=lambda c: trades[c.id].closed.n if trades[c.id].n else 0)
    tr = trades[rep.id].closed if trades[rep.id].n else trades[rep.id]
    out: dict[str, Any] = {"cellule_reference": rep.id, "seuil_t_puissance": _f(z_bar, 3),
                           "puissance_nature": POWER_UPPER_BOUND}  # fmt: skip
    if tr.n < 3 or rep.mode == "monthly_overlay" or not math.isfinite(z_bar):
        out.update(puissance={}, puissance_decision=None, effet_minimal_detectable=None)
        return out
    _, ws = series_of(tr, _monthly(rep))
    sd = float(np.std(ws, ddof=1)) if ws.size > 2 else math.nan
    out["effet_minimal_detectable"] = _f((z_bar + 0.8416) * sd / math.sqrt(ws.size), 4) if ws.size > 2 else None
    lag = nw_lag_of(tr, rep)
    pw = {}
    for e in inf["power_edges"]:
        pw[f"{e:+.2f}"] = _f(planted_power(tr, dense.get(rep.combo_key, {}), _monthly(rep), float(e),
                                           int(inf["power_reps"]), prereg.seed + 7, z_bar,
                                           float(th["a4"]["min_excess"]), lag), 3)  # fmt: skip
    out["puissance"] = pw
    out["puissance_decision"] = pw.get(f"{float(inf['power_decision_edge']):+.2f}")
    return out


def exploratory_romano_wolf(
    expl: Sequence[Cell], trades: Mapping[str, Trades], prereg: Prereg
) -> tuple[S.RomanoWolf, Any]:
    """Romano–Wolf over the exploratory cells' weekly series: each column studentised with its own Newey–West
    lag (the overlap of its holds, in weeks), rows resampled in blocks of at least the longest hold."""
    inf = prereg.inference
    series, lags = [], []
    for c in expl:
        tr = trades.get(c.id, Trades({}))
        cl = tr.closed if tr.n else tr
        series.append(series_of(cl, _monthly(c)) if cl.n else (np.zeros(0, dtype=np.int64), np.zeros(0)))
        lags.append(weekly_lag_of(cl, c) if cl.n else 2)
    _, mat = weekly_matrix(series)
    block = max([int(inf["block_weeks"])] + [weekly_block_of(c, int(inf["block_weeks"])) for c in expl])
    rw = S.romano_wolf_full(mat, block, int(inf["bootstrap_draws"]), prereg.seed, np.array(lags, dtype=np.int64))
    return rw, mat


def portfolio_slots(prereg: Prereg, st: Settings) -> tuple[int, str | None]:
    """Lines of the portfolio replays: the plan's ``thresholds.a13.slots``; a difference with the value derived
    from params.yaml (open-risk cap ÷ risk per trade) is reported, never silently resolved (rule 8)."""
    slots = int(prereg.thresholds["a13"]["slots"])
    if slots != st.slots:
        return slots, (f"lignes du portefeuille : {slots} (plan, thresholds.a13.slots) ≠ {st.slots} (params.yaml, "
                       f"risque ouvert ÷ risque par trade) — le plan fait foi")  # fmt: skip
    return slots, None
