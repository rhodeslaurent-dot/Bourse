# ruff: noqa: E501
"""Registration n° 6 (docs/34 §34.13): exit rules on frozen entries, paired per trade. Pure.

Signals and entry orders are those of registration n° 4 (``entry_policies``: production setups by the real detector
with their zones, R1 stop and exploitable targets; letters with explicit levels; t00 / t14), entry P1 = E_open(1) or
P2 = buy stop-limit on the confirmation. Each cell simulates the SAME orders twice — the tested exit and the baseline
exit (XP production, or X3 for t14) — so both arms share every fill; the tested quantity is Δ = value(tested) −
value(baseline) per filled trade. A fill outside the production frame stop_exec < entry < target 1 is no trade
under XP: the pair is excluded and counted (``hors_cadre_reference``); an unknown outcome on either side drops it.

Units: ``r_stop`` (both arms on the same initial stop: net result ÷ initial slipped risk, size on the risk budget),
``eur_300`` (stop-width family: net € at the budget-sized position for 300 € of risk) or ``r_ref`` (t14: 2 σ20 √h of
the baseline horizon for both arms). Statistics reused from registration n° 4 (weekly series, Newey–West t, block
interval, folds, power); on top: Δ under the three path rules (each arm under the same rule) and under stressed costs,
the 6-line drawdown against the baseline's, the deflated Sharpe ratio (prior trials of the earlier plans + this
plan's cells of the same trigger) and the PBO of each exploratory family; the excursion table (MFE / MAE in R).
Aggregates only leave this module (no price, no instrument).
"""

from __future__ import annotations

import dataclasses
import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np

from app.domain.research import entry_policies as E
from app.domain.research import stats as S
from app.domain.research import triggers as T
from app.domain.research.classify import Tier, a2_met
from app.domain.research.entries import ExitModel, exit_model, unit_fraction
from app.domain.research.exits import CLOSED, INVALID, REASONS, ExitSpec, Orders, PathRule, SimResult, simulate
from app.domain.research.panel import F64, Panel
from app.domain.research.pipeline import (
    GroupData,
    Settings,
    Signals,
    Trades,
    _f,
    _mean,
    earnings_coverage,
    prepare_group,
    replay,
    signal_rows,
    trade_rows,
)
from app.domain.research.prereg import Cell, Prereg, default_stop

RULES: tuple[PathRule, ...] = ("stop_first", "ohlc", "target_first")
HORIZONS = (5, 10, 20, 60)
QUANTILES = (0.10, 0.25, 0.50, 0.75, 0.90)
HEADLINE_NONE = "AUCUNE RÈGLE DE SORTIE RETENUE"
EXIT_FR = {
    "XP": "production : stop structurel R1 glissé, objectif 1 exploitable, sortie au temps du détecteur",
    "XS": "stop de production, sans objectif, sortie au temps",
    "XQ": "moitié vendue à +1 R, stop remonté au prix de revient, chandelier 3 ATR sur le reste",
    "X1": "sortie au temps seule (le stop 3 σ √h ne sert qu'à la taille)",
    "X3": "stop 3 σ20 √h, sortie au temps",
    "X4": "chandelier : plus haut depuis l'exécution − 3 ATR14, jamais abaissé",
    "X5": "deux clôtures sous la MM20 → ouverture suivante",
    "X6": "sortie conditionnelle à la 5e séance si le meilleur moment < 0,5 R",
    "X8": "stop 2,5 ATR14 sous l'exécution, sans objectif, sortie au temps",
}
STOP_FR = {"prod": "structurel (R1) de production", "atr2.5": "2,5 ATR14 sous l'exécution",
           "sigma3": "3 σ20 √h sous l'exécution", "bench": "propre à la sortie (banc)"}  # fmt: skip


@dataclass(frozen=True)
class ExitParams:
    partial_at_r: float  # XQ: the partial sale at entry + k · initial risk
    partial_share: float  # XQ: share of the quantity sold (risk.stop.reduce_at_target1_pct, frozen in the plan)
    dsr_prior_trials: int  # exits already seen on the discovery window for d7 (plan n° 1 F0(c) + n° 4)


def exit_params(prereg: Prereg) -> ExitParams:
    a = prereg.assumptions
    return ExitParams(float(a["partial_at_r"]), float(a["partial_share"]), int(a["dsr_prior_trials"]))


def entry_params(prereg: Prereg, prod: Mapping[str, Any]) -> E.EntryParams:
    """The entry conventions of registration n° 4 (P0 information arm, P1, P2), from this plan's copy of them."""
    a = prereg.assumptions
    return E.EntryParams(
        float(prod.get("min_reward_risk", 2.0)), int(prod.get("expiry_sessions", 3)), float(a["next_open_cap_atr"]),
        math.nan, float(a["confirm_cap_atr"]), float(a.get("letters_entry_max_pct_above_trigger", 0.01)),
    )  # fmt: skip


# --- one arm: the cell's orders with one exit -------------------------------------------------------------------


def exit_spec(code: str, max_hold: int, unit: str, xp: ExitParams) -> tuple[ExitSpec, float]:
    """The engine spec of an exit code with its time exit at ``max_hold``, and the R_ref conditional threshold
    (X6 in an r_ref cell; in the other units the threshold is in R of the trade's own initial risk)."""
    if code == "XP":
        return ExitSpec("XP", max_hold, bracket_at_fill=True), 0.0
    if code == "XS":
        return ExitSpec("XS", max_hold), 0.0
    if code == "XQ":  # X4's chandelier on the remainder after the partial sale
        trail = exit_model("X4", max_hold).spec.trail_atr
        return ExitSpec("XQ", max_hold, trail_atr=trail, partial_r=xp.partial_at_r, partial_share=xp.partial_share), 0.0
    m = exit_model(code, max_hold, max_hold=max_hold)
    spec = dataclasses.replace(m.spec, max_hold=max_hold)
    if m.cond_r > 0 and unit != "r_ref":
        return dataclasses.replace(spec, cond_r_stop=m.cond_r), 0.0
    return spec, m.cond_r


def stop_levels(kind: str, code: str, g: GroupData, su: E.SetupTable, h_ref: int) -> tuple[F64, F64]:
    """(stop_mult, stop_off) of the initial stop: the setup's structural stop, 2.5 ATR or 3 σ √h under the fill, or
    the exit code's own bench stop."""
    ts, cols = su.ts, su.cols
    atr, sig = g.f.atr14[ts, cols], g.f.sigma20[ts, cols]
    zero, one = np.zeros(su.size), np.ones(su.size)
    if kind == "prod":
        return zero, -su.stop
    if kind == "bench":
        m = exit_model(code, h_ref)
        kind = f"atr{m.stop_k:g}" if m.stop_kind == "atr" else f"sigma{m.stop_k:g}"
    if kind.startswith("atr"):
        return one, float(kind[3:]) * atr
    return np.exp(-float(kind[5:]) * sig * math.sqrt(h_ref)), zero


@dataclass(frozen=True)
class ArmDef:
    code: str
    max_hold: int
    stop: str


def arm_defs(cell: Cell) -> dict[str, ArmDef]:
    """Tested exit (time ``h``, the cell's initial stop) and baseline exit (time ``ref_h``, its own stop)."""
    out = {"sortie": ArmDef(cell.exit, cell.h, cell.stop or default_stop(cell.exit, cell.trigger))}
    if cell.baseline_exit is not None:
        out["reference"] = ArmDef(cell.baseline_exit, cell.ref_h, default_stop(cell.baseline_exit, cell.trigger))
    return out


def entry_orders(policy: str, g: GroupData, su: E.SetupTable, cell: Cell, ep: E.EntryParams) -> Orders:
    """The common entry orders of both arms (limit, invalidation, validity, stop-limit level) as registration n° 4
    builds them; without a setup (t14) the invalidation is X3's stop at the reference horizon applied to the limit."""
    return E.policy_orders(policy, g, su, dataclasses.replace(cell, exit="X3", h=cell.ref_h), ep)


def run_arm(
    g: GroupData, cell: Cell, su: E.SetupTable, base: Orders, arm: ArmDef, st: Settings, xp: ExitParams,
    rule: PathRule = "stop_first",
) -> tuple[Trades, SimResult]:  # fmt: skip
    """One exit on the common orders: the harness's trade table plus the value per trade in the cell's unit (NaN
    unless closed), the fill flags and the partial-sale flag."""
    spec, cond_r = exit_spec(arm.code, arm.max_hold, cell.unit, xp)
    s_mult, s_off = stop_levels(arm.stop, arm.code, g, su, cell.ref_h)
    n = su.size
    t_off = su.t1.copy() if arm.code == "XP" else np.full(n, np.nan)
    sig = g.f.sigma20[su.ts, su.cols]
    cond = cond_r * unit_fraction(sig, cell.ref_h) if cond_r > 0 else np.full(n, np.nan)
    od = Orders(base.inst, base.sig_pos, base.limit, base.cancel_below, s_mult, s_off, np.zeros(n), t_off, cond,
                base.validity, base.stop_level)  # fmt: skip
    model = ExitModel(arm.code, spec, "atr", 0.0, cell.ref_h)  # h_ref: the R_ref unit of both arms
    sim = simulate(g.bars, od, spec, st.slippage, rule)
    unit = "r_ref" if cell.unit == "r_ref" else "r_stop"
    tr = trade_rows(g, cell, Signals(su.ts, su.cols), od, sim, model, None, st, unit)
    cols = dict(tr.cols)
    closed = tr["closed"].astype(bool)
    if cell.unit == "eur_300":
        v, vp, vs = tr["eur"], tr["eur_pea"], tr["eur_stress"]
    else:
        v, vp, vs = tr["r"], tr["r_pea"], tr["r_stress"]
    cols["value"] = np.where(closed, v, np.nan)
    cols["value_pea"] = np.where(closed & tr["pea_eligible"].astype(bool), vp, np.nan)
    cols["value_stress"] = np.where(closed, vs, np.nan)
    filled = np.isin(tr["status"], (CLOSED, 3, 4, 6))
    cols["ordered"] = np.isfinite(od.limit)
    cols["filled"] = filled
    cols["invalid"] = tr["status"] == INVALID
    cols["at_open"] = sim.at_open & filled
    cols["delay"] = np.where(filled & (sim.fill_pos >= 0), sim.fill_pos - od.sig_pos, -1).astype(np.int64)
    pq = sim.partial_qty if sim.partial_qty is not None else np.zeros(n)
    cols["partial"] = (pq > 0) & closed
    return Trades(cols), sim


# --- excursions (MFE / MAE in R of the initial slipped stop) -----------------------------------------------------


def excursion_rows(g: GroupData, su: E.SetupTable, od: Orders, sim: SimResult, tr: Trades) -> Trades:
    """Per closed trade: the best and worst excursion in R at 5 / 10 / 20 / 60 sessions while held (the fill session
    filled intraday at the limit does not claim its high), over the whole hold, the first target's distance in R,
    whether it was reached, the net R. Read in the closed trades' own bars, nothing after the exit."""
    ok = tr["closed"].astype(bool) & np.isfinite(tr["r"])
    idx = np.nonzero(ok)[0]
    fp, xpos, e = sim.fill_pos[idx], sim.exit_pos[idx], sim.entry[idx]
    risk = e - sim.stop_exec_init[idx]
    inst = od.inst[idx]
    rising = sim.rising[idx] if sim.rising is not None else np.zeros(idx.size, dtype=bool)
    pre = ~sim.at_open[idx] & ~rising
    w = g.bars.width
    hi_all, lo_all = g.bars.high.ravel(), g.bars.low.ravel()
    run_hi, run_lo = e.copy(), e.copy()
    cols: dict[str, Any] = {}
    with np.errstate(invalid="ignore", divide="ignore"):
        for j in range(max(HORIZONS)):
            pos = fp + j
            alive = pos <= xpos
            gi = np.where(alive, pos, fp) * w + inst
            hj, lj = hi_all[gi], lo_all[gi]
            if j == 0:
                hj = np.where(pre, e, hj)
            run_hi = np.where(alive, np.maximum(run_hi, hj), run_hi)
            run_lo = np.where(alive, np.minimum(run_lo, lj), run_lo)
            if j + 1 in HORIZONS:
                cols[f"mfe_{j + 1}"] = (run_hi - e) / risk
                cols[f"mae_{j + 1}"] = (run_lo - e) / risk
        cols["mfe_tout"] = (sim.hi[idx] - e) / risk
        cols["objectif_1_r"] = (su.t1[idx] - e) / risk
        cols["objectif_1_atteint"] = sim.hi[idx] >= su.t1[idx]
    cols["r"] = tr["r"][idx]
    return Trades(cols)


def _quantiles(x: F64) -> dict[str, float | None]:
    x = x[np.isfinite(x)]
    if x.size == 0:
        return {f"q{int(q * 100)}": None for q in QUANTILES}
    return {f"q{int(q * 100)}": _f(float(np.quantile(x, q)), 3) for q in QUANTILES}


def excursion_summary(ex: Trades) -> dict[str, Any]:
    """Quantiles in R only (never a price): MFE / MAE at each horizon, passed +1 R then lost, target 1 reached and
    its distance in R."""
    if ex.n == 0:
        return {"n": 0}
    r, mfe = ex["r"], ex["mfe_tout"]
    out: dict[str, Any] = {"n": ex.n}
    for k in HORIZONS:
        out[f"mfe_r_{k}_seances"] = _quantiles(ex[f"mfe_{k}"])
        out[f"mae_r_{k}_seances"] = _quantiles(ex[f"mae_{k}"])
    out["part_passee_par_1r_puis_perte"] = _f(float(np.mean((mfe >= 1.0) & (r < 0))), 4)
    out["part_objectif_1_atteint"] = _f(float(np.mean(ex["objectif_1_atteint"].astype(bool))), 4)
    out["distance_objectif_1_r"] = _quantiles(ex["objectif_1_r"])
    return out


# --- the cells over the groups ---------------------------------------------------------------------------------------


def lock_cell(cell: Cell) -> Cell:
    """The lockout of both arms (one signal per value every max(h, ref_h) own sessions)."""
    return dataclasses.replace(cell, h=max(cell.h, cell.ref_h))


def run_exit_cells(
    groups: Iterable[GroupData], cells: Sequence[Cell], st: Settings, prod: Mapping[str, Any], ep: E.EntryParams,
    xp: ExitParams, window: tuple[date, date], end_margin: int, rules: bool = True, progress: E.Progress | None = None,
) -> tuple[dict[str, dict[str, Trades]], dict[str, Any]]:  # fmt: skip
    """Every cell's arms over the groups (one group in memory at a time): tested and baseline exits under the three
    path rules, or the excursion table of a diagnostic cell (its entry and P0 for information)."""
    parts: dict[str, dict[str, list[Trades]]] = defaultdict(lambda: defaultdict(list))
    info: dict[str, Any] = {"taux_placebo": {}, "setups": defaultdict(dict), "couverture": {}}
    earnings = any(c.trigger in T.EARNINGS_TRIGGERS for c in cells)
    for g in groups:
        rows = signal_rows(g.p.days, window[0], window[1], end_margin)
        if rows[1] < rows[0]:
            continue
        if earnings:  # t14: the coverage floor of registration n° 3, per group
            cov = earnings_coverage(g, rows, st.min_earnings_coverage)
            g.earn_ok = bool(cov["compte_dans_le_verdict"])
            info["couverture"][g.p.group] = cov
        gs = E.GroupSetups(g, st, prod, ep, rows)
        for c in cells:
            if progress:
                progress(f"{g.p.group} : {c.id}")
            lc = lock_cell(c)
            su = gs.signals(lc)
            info["setups"][g.p.group][c.trigger] = gs.table(lc).size
            if su.size == 0:
                continue
            assert c.entry is not None
            base = entry_orders(c.entry, g, su, c, ep)
            defs = arm_defs(c)
            if c.baseline_exit is None:  # the excursion table: the cell's entry, and P0 for information
                for name, pol in ((c.entry, c.entry), ("P0", "P0")):
                    od = base if pol == c.entry else entry_orders(pol, g, su, c, ep)
                    tr, sim = run_arm(g, c, su, od, defs["sortie"], st, xp)
                    parts[c.id][f"excursions_{name}"].append(excursion_rows(g, su, od, sim, tr))
                    parts[c.id][f"bras_{name}"].append(tr)
                continue
            for rule in RULES if rules else RULES[:1]:
                sfx = "" if rule == "stop_first" else f"_{rule}"
                for name, d in defs.items():
                    parts[c.id][name + sfx].append(run_arm(g, c, su, base, d, st, xp, rule)[0])
        for cid, r in gs.rates.items():
            info["taux_placebo"].setdefault(cid, {})[g.p.group] = _f(r, 6)
    trades = {cid: {k: Trades.concat(v) for k, v in d.items()} for cid, d in parts.items()}
    info["setups"] = dict(info["setups"])
    return trades, info


# --- statistics ------------------------------------------------------------------------------------------------------


def arm_summary(tr: Trades, prereg: Prereg) -> dict[str, Any]:
    """The arm of registration n° 4 plus the exit reasons, the partial-sale share and R per slot-month (R per trade ÷
    mean hold × 21 sessions: the cost of holding a line longer)."""
    out = E.arm_summary(tr, prereg)
    cl = tr.closed
    if cl.n:
        hold = float(np.mean(cl["hold"]))
        mr = _mean(cl["r"][np.isfinite(cl["r"])])
        out["r_par_ligne_mois"] = _f(mr / hold * 21) if mr is not None and hold > 0 else None
        out["duree_moyenne"] = _f(hold, 1)
        out["part_vente_partielle"] = _f(float(cl["partial"].mean()), 4)
        out["motifs"] = {
            REASONS[int(k)]: _f(float(np.mean(cl["reason"] == k)), 4)
            for k in np.unique(cl["reason"])
            if int(k) in REASONS
        }
    return out


def _both(a: Trades, b: Trades, col: str = "value") -> np.ndarray:
    return np.asarray(np.isfinite(a[col]) & np.isfinite(b[col]), dtype=bool)


def _dd(a: Trades, b: Trades, unit: str, slots: int, worse: float) -> dict[str, Any]:
    """6-line replay of both arms on the paired trades: the tested exit's drawdown at most ``worse`` deeper."""
    m = _both(a, b)
    col = "eur" if unit == "eur_300" else "r"
    ra, rb = replay(a.select(m), slots, col), replay(b.select(m), slots, col)
    da, db = ra.get("max_drawdown_r"), rb.get("max_drawdown_r")
    ok = da is not None and db is not None and (db == 0 or float(da) >= float(db) * (1 + worse))
    return {"sortie": ra, "reference": rb, "ok": bool(ok)}


def _placebo_of(cell: Cell, cells: Sequence[Cell]) -> Cell | None:
    key = (cell.entry, cell.exit, cell.baseline_exit, cell.h, cell.ref_h, cell.stop, cell.validity, cell.unit)
    for p in cells:
        if (
            p.trigger == "t00"
            and p is not cell
            and (p.entry, p.exit, p.baseline_exit, p.h, p.ref_h, p.stop, p.validity, p.unit) == key
        ):
            return p
    return None


def _role(c: Cell) -> str:
    if c.trigger == "t00":
        return "placebo (lu en premier)"
    if c.tier == "confirmatory":
        return "confirmatoire"
    if c.trigger == "t14":
        return "diagnostic, lu après le verdict du n° 3"
    if c.entry == "P2":
        return "diagnostic, lu après le verdict du n° 4"
    return "diagnostic" if c.diagnostic else "exploratoire"


def reading_order(cells: Sequence[Cell]) -> list[str]:
    def rank(c: Cell) -> int:
        if c.trigger == "t00":
            return 0
        if c.tier == "confirmatory":
            return 1
        if not c.diagnostic:
            return 2
        return 5 if c.trigger == "t14" else 4 if c.entry == "P2" else 3

    return [c.id for c in sorted(cells, key=lambda c: (rank(c), c.id))]


def exit_criteria(c: Mapping[str, Any], th: Mapping[str, Any], confirmatory: bool) -> dict[str, bool]:
    """A paired exit cell: A1 on the pairs, the paired thresholds, the path-rule and cost-stress signs, the 6-line
    drawdown, the DSR, (exploratory) the family's PBO, and the multiple-testing bar (Holm / Romano–Wolf)."""
    pa, a1, a3 = th["paired"], th["a1"], th["a3"]

    def ge(x: Any, b: float) -> bool:
        return x is not None and float(x) >= b

    ic = c.get("ic") or [None, None]
    rules = c.get("delta_par_regle") or {}
    min_d = float(pa["min_delta_eur_300_per_trade"] if c.get("unite") == "eur_300" else pa["min_delta_r_per_trade"])
    out = {
        "A1": ge(c.get("n"), a1["event_min_n"]) and ge(c.get("semaines"), a1["event_min_weeks"]) and ge(c.get("pli_min_n"), a1["fold_min_trades"]),
        "delta": ge(c.get("delta_moyen"), min_d),
        "t": ge(c.get("t_newey_west"), float(pa["min_t_newey_west_weekly"])),
        "ic": ic[0] is not None and float(ic[0]) > float(pa["min_ci_low"]),
        "plis": ge(c.get("plis_positifs"), float(pa["min_positive_folds"])),
        "regles_parcours": len(rules) == len(RULES) and all(v is not None and float(v) > 0 for v in rules.values()),
        "couts_stresses": ge((c.get("delta_couts_stresses") or {}).get("delta_moyen"), 1e-12),
        "recul_portefeuille": bool((c.get("recul_max") or {}).get("ok")),
        "dsr": ge(c.get("dsr"), float(a3["min_dsr"])),
    }  # fmt: skip
    if not confirmatory:
        out["pbo"] = c.get("pbo_famille") is not None and float(c["pbo_famille"]) <= float(a3["max_pbo"])
    p = c.get("p_holm") if confirmatory else c.get("p_romano_wolf")
    out["tests_multiples"] = p is not None and float(p) <= float(a3["holm_alpha"] if confirmatory else a3["rw_alpha"])
    return out


def exit_unseal_verdict(
    sealed: Mapping[str, Any], discovery: Mapping[str, Any], th: Mapping[str, Any], label: str
) -> dict[str, Any]:
    """The single opening of the old replication for a paired exit candidate: Δ > 0 there (the sign of the
    discovery) with an interval low above ``replication_min_ci_low``. ``label``: propre / semi-propre (the shared
    period already opened by registration n° 4 on another day: the ADR then enters at half risk, shadow first)."""
    d_s, d_d = sealed.get("delta_moyen"), discovery.get("mean_excess")
    ic = sealed.get("ic") or [None, None]
    ok = bool(d_s is not None and d_d is not None and float(d_s) > 0 and float(d_d) > 0 and ic[0] is not None
              and float(ic[0]) > float(th["paired"]["replication_min_ci_low"]))  # fmt: skip
    text = "non confirmé"
    if ok:
        text = f"confirmé ({label}) — ADR « règle de sortie de la famille tendance »"
        if label != "propre":
            text += " à demi-risque, en ombre d'abord"
    return {"n": sealed.get("n"), "delta_moyen": d_s, "ic": list(ic), "decouverte_delta_moyen": d_d,
            "replication": label, "verdict_a": ok, "verdict": text}  # fmt: skip


def _drop_private(stats: Mapping[str, dict[str, Any]]) -> None:
    for s in stats.values():
        for k in ("_se", "_w", "_lag"):
            s.pop(k, None)


def _cell_stats(c: Cell, arms: Mapping[str, Trades], prereg: Prereg, slots: int) -> dict[str, Any]:
    s: dict[str, Any] = {"id": c.id, "famille": c.family, "declencheur": c.trigger, "h": c.h, "h_reference": c.ref_h,
                         "sortie": c.exit, "sortie_reference": c.baseline_exit, "stop_initial": c.stop or default_stop(c.exit, c.trigger),
                         "politique": c.entry, "validite": c.validity, "unite": c.unit, "tier": c.tier, "role": _role(c),
                         "diagnostique": c.diagnostic, "comptes": list(c.accounts), "duree_max": max(c.h, c.ref_h),
                         "signaux": 0, "n": 0}  # fmt: skip
    if c.baseline_exit is None:  # the excursion table
        for k, v in arms.items():
            if k.startswith("excursions_"):
                s[k] = excursion_summary(v)
            elif k.startswith("bras_"):
                s[k] = arm_summary(v, prereg)
        ex = arms.get(f"excursions_{c.entry}")
        s["n"] = ex.n if ex is not None else 0
        return s
    a, b = arms.get("sortie"), arms.get("reference")
    s["bras"] = {k: arm_summary(v, prereg) for k, v in arms.items() if k in ("sortie", "reference")}
    if a is None or b is None or not a.n:
        return s
    s["signaux"] = a.n
    pa = prereg.thresholds["paired"]
    s["hors_cadre_reference"] = int((a["filled"].astype(bool) & b["invalid"].astype(bool)).sum())
    blk = lock_cell(c)
    s.update(E.paired(a, b, blk, prereg, prereg.seed))
    s["mean_excess"], s["t_week"] = s.get("delta_moyen"), s.get("t_newey_west")
    pea = E.paired(a, b, blk, prereg, prereg.seed + 1, "value_pea")
    stress = E.paired(a, b, blk, prereg, prereg.seed + 2, "value_stress")
    s["delta_pea"] = {"n": pea.get("n"), "delta_moyen": pea.get("delta_moyen"), "ic": pea.get("ic")}
    s["delta_couts_stresses"] = {"n": stress.get("n"), "delta_moyen": stress.get("delta_moyen")}
    by_rule: dict[str, float | None] = {}
    for rule in RULES:
        sfx = "" if rule == "stop_first" else f"_{rule}"
        ar, br = arms.get("sortie" + sfx), arms.get("reference" + sfx)
        by_rule[rule] = (
            _f(_mean((ar["value"] - br["value"])[_both(ar, br)])) if ar is not None and br is not None else None
        )
    s["delta_par_regle"] = by_rule
    if c.unit != "eur_300":  # the € view of every paired cell (information; X7 is judged in €)
        m = np.isfinite(a["eur"]) & np.isfinite(b["eur"])
        s["delta_eur_300"] = _f(_mean((a["eur"] - b["eur"])[m]), 2)
    s["recul_max"] = _dd(a, b, c.unit, slots, float(pa["max_dd_worse_ratio"]))
    ws = s.get("_w")
    if ws is not None and ws[1].size > 2:
        sr, sk, ku = S.moments(ws[1])
        s["_sr"], s["_sk"], s["_ku"], s["_nobs"] = sr, sk, ku, int(ws[1].size)
    return s


def run_exit_registration(
    panels: Sequence[Panel], prereg: Prereg, st: Settings, prod: Mapping[str, Any], stages: str = "all",
    holdout_cells: Sequence[str] | None = None, progress: E.Progress | None = None,
) -> dict[str, Any]:  # fmt: skip
    """The registration of an exit-rule plan: discovery (every cell, tests, classification, stage 2) or the single
    unseal of the old replication for the frozen candidates (their cells only)."""
    seg, th, inf = prereg.segments, prereg.thresholds, prereg.inference
    st = dataclasses.replace(st, seal_start=seg.holdout_start)
    ep, xp = entry_params(prereg, prod), exit_params(prereg)
    holdout = holdout_cells is not None
    window = prereg.sealed_window if holdout else seg.discovery
    cells = [c for c in prereg.cells if not holdout or c.id in set(holdout_cells or ())]
    groups = (prepare_group(k, p, st) for k, p in enumerate(panels) if p.shape[1])
    trades, info = run_exit_cells(groups, cells, st, prod, ep, xp, window, seg.end_margin_sessions, True, progress)
    slots = int(th["a13"]["slots"])
    stats = {c.id: _cell_stats(c, trades.get(c.id, {}), prereg, slots) for c in cells}
    out: dict[str, Any] = {"segment": "scelle" if holdout else "decouverte", "cellules": stats, "lignes_portefeuille": slots,
                           "avertissements": [], "taux_placebo": info["taux_placebo"], "setups_par_groupe": info["setups"]}  # fmt: skip
    if info["couverture"]:
        out["couverture_resultats"] = info["couverture"]
    tested = [c for c in cells if not c.diagnostic and c.baseline_exit is not None]
    conf = [c for c in tested if c.tier == "confirmatory"]
    expl = [c for c in tested if c.tier != "confirmatory"]
    if holdout:
        for s in stats.values():
            for k in ("_sr", "_sk", "_ku", "_nobs"):
                s.pop(k, None)
        _drop_private(stats)
        out["regles_de_sortie"] = {"ordre_de_lecture": reading_order(cells)}
        return out

    def pval(c: Cell) -> float:
        p = stats[c.id].get("p_unilateral")
        return float(p) if p is not None else 1.0

    for c, ph in zip(conf, S.holm(np.array([pval(c) for c in conf])), strict=True):
        stats[c.id]["p_holm"] = _f(float(ph), 6)
    z_rw = float(th["a3"]["min_t"])
    empty = (np.zeros(0, dtype=np.int64), np.zeros(0))
    if expl:
        series = [stats[c.id].get("_w") or empty for c in expl]
        weeks = np.unique(np.concatenate([w for w, _ in series]))
        mat = np.full((weeks.size, len(expl)), np.nan)
        for j, (w, v) in enumerate(series):
            mat[np.searchsorted(weeks, w), j] = v
        lags = np.array([int(stats[c.id].get("_lag") or 2) for c in expl], dtype=np.int64)
        block = max(E._block(lock_cell(c), int(inf["block_weeks"])) for c in expl)
        rw = S.romano_wolf_full(mat, block, int(inf["bootstrap_draws"]), prereg.seed, lags)
        out["romano_wolf"] = {
            "bloc_semaines": rw.block,
            "t_max_critique_5pct": _f(rw.max_t_crit, 3),
            "cellules": len(expl),
        }
        for j, c in enumerate(expl):
            stats[c.id]["p_romano_wolf"] = _f(float(rw.p_adj[j]), 6)
        if math.isfinite(rw.max_t_crit):
            z_rw = max(z_rw, rw.max_t_crit)
        fams: dict[str, list[int]] = defaultdict(list)
        for j, c in enumerate(expl):
            fams[c.family].append(j)
        out["pbo_familles"] = {}
        for fam, idx in sorted(fams.items()):
            pbo = S.pbo_cscv(mat[:, idx], int(inf["pbo_blocks"]))
            out["pbo_familles"][fam] = _f(pbo, 3)
            for j in idx:
                stats[expl[j].id]["pbo_famille"] = _f(pbo, 3)
    for c, q in zip(tested, S.bh(np.array([pval(c) for c in tested])), strict=True):
        stats[c.id]["q_bh"] = _f(float(q), 6)
    # DSR: N = the exits already seen for d7 on this window (plan n° 1 F0(c), n° 4) + this plan's cells of the trigger
    by_trig: dict[str, list[Cell]] = defaultdict(list)
    for c in tested:
        by_trig[c.trigger].append(c)
    out["dsr_essais"] = {}
    for trig, cs in by_trig.items():
        n_trials = len(cs) + (xp.dsr_prior_trials if trig == "d7x" else 0)
        srs = [float(stats[c.id]["_sr"]) for c in cs if math.isfinite(float(stats[c.id].get("_sr", math.nan)))]
        var_sr = float(np.var(srs, ddof=1)) if len(srs) > 1 else 0.0
        out["dsr_essais"][trig] = n_trials
        for c in cs:
            s = stats[c.id]
            s["dsr"] = _f(S.deflated_sharpe(float(s.get("_sr", math.nan)), n_trials, var_sr, int(s.get("_nobs", 0)),
                                            float(s.get("_sk", 0.0)), float(s.get("_ku", 3.0))), 4)  # fmt: skip
            s["dsr_essais"] = n_trials
    z_holm = S.norm_ppf(1 - float(th["a3"]["holm_alpha"]) / max(1, len(conf)))
    pa_min_t = float(th["paired"]["min_t_newey_west_weekly"])
    for c in tested:
        s = stats[c.id]
        z = max(z_holm if c.tier == "confirmatory" else z_rw, pa_min_t)
        se = float(s.get("_se") or math.nan)
        scale = 300.0 if c.unit == "eur_300" else 1.0  # an edge of x R is x × 300 € for 300 € of risk budget
        s["puissance"] = E.power(se, z, [float(e) * scale for e in inf["power_edges"]])
        s["puissance_decision"] = s["puissance"].get(f"{float(inf['power_decision_edge']) * scale:+.2f}")
        s["effet_minimal_detectable"] = _f((z + 0.8416) * se, 4) if math.isfinite(se) else None
        s["seuil_t_puissance"] = _f(z, 3)
    # placebo: the mechanical part of Δ, printed; the verdict (a) is on the total Δ (what the book earns)
    for c in cells:
        pl = _placebo_of(c, cells) if c.trigger != "t00" else None
        if pl is None or "_w" not in stats[c.id] or "_w" not in stats[pl.id]:
            continue
        blk, draws = E._block(lock_cell(c), int(inf["block_weeks"])), int(inf["bootstrap_draws"])
        stats[c.id]["exces_sur_placebo"] = {
            "placebo": pl.id,
            **E.aligned_excess(stats[c.id]["_w"], stats[pl.id]["_w"], blk, draws, prereg.seed + 3),
        }
    # stage 2: 6-line replays of the confirmatory cells' arms (tested exits and the shared baseline)
    stage2: dict[str, Any] = {}
    budget = prereg.stage2_max_cells if stages in ("2", "all") else 0
    for c in conf:
        arms = trades.get(c.id, {})
        for name, code in (("reference", c.baseline_exit), ("sortie", c.exit)):
            if name in arms and code not in stage2 and len(stage2) < budget:
                stage2[str(code)] = {**replay(arms[name].closed, slots), "cellule_id": c.id,
                                     "r_par_ligne_mois": (stats[c.id]["bras"].get(name) or {}).get("r_par_ligne_mois")}  # fmt: skip
    out["etape2"] = stage2
    # classification
    for c in cells:
        s = stats[c.id]
        for k in ("_sr", "_sk", "_ku", "_nobs"):
            s.pop(k, None)
        if c.diagnostic or c not in tested:
            s["niveau"], s["criteres"] = Tier.DIAGNOSTIC.value, {}
            if (
                c.entry == "P2" and c.baseline_exit is not None
            ):  # X9: the absolute A2 of the P2 arm, information for n° 4 (b)
                s["a2_absolu_sortie"] = a2_met(
                    dict(((s.get("bras") or {}).get("sortie") or {}).get("oos") or {}), dict(th)
                )
            continue
        crit = exit_criteria(s, th, c.tier == "confirmatory")
        s["criteres"] = crit
        ok = all(crit.values())
        s["niveau"] = (
            Tier.CANDIDATE if ok and c.tier == "confirmatory" else Tier.WATCHLIST if ok else Tier.REJECT
        ).value
        if c.tier == "confirmatory":
            s["decision_a"] = "règle de sortie : " + (
                "CANDIDAT" if ok else "NON CONCLUANT (A1)" if not crit["A1"] else "REJET"
            )
    _drop_private(stats)
    cands = [c.id for c in conf if stats[c.id]["niveau"] == Tier.CANDIDATE.value][: int(th["candidates_max"])]
    out["candidats"] = cands
    out["surveillance"] = [c.id for c in expl if stats[c.id]["niveau"] == Tier.WATCHLIST.value]
    fams2: dict[str, list[Cell]] = defaultdict(list)
    for c in cells:
        fams2[c.family if c.tier != "confirmatory" else c.id].append(c)
    verdicts: dict[str, str] = {}
    for fam, cs in fams2.items():
        levels = {stats[c.id]["niveau"] for c in cs}
        if all(c.diagnostic or c.baseline_exit is None for c in cs):
            verdicts[fam] = "diagnostic (jamais candidat)"
        elif Tier.CANDIDATE.value in levels:
            verdicts[fam] = (
                f"candidat — à confirmer sur la réplication {prereg.sealed_window[0].year}-{prereg.sealed_window[1].year}"
            )
        elif Tier.WATCHLIST.value in levels:
            verdicts[fam] = "surveillance (exploratoire)"
        elif all(not (stats[c.id].get("criteres") or {}).get("A1", False) for c in cs):
            verdicts[fam] = "non concluant (A1 : trop peu de trades appariés)"
        else:
            pw = max((stats[c.id].get("puissance_decision") or 0.0) for c in cs)
            verdicts[fam] = (
                "pas d'avantage détecté"
                if pw >= float(th["power_min"])
                else "test non concluant (puissance insuffisante)"
            )
    out["verdicts_familles"] = verdicts
    a1_none = bool(conf) and all(not (stats[c.id].get("criteres") or {}).get("A1", False) for c in conf)
    if cands:
        out["titre"] = (f"{len(cands)} RÈGLE(S) DE SORTIE CANDIDATE(S) — À CONFIRMER SUR LA RÉPLICATION "
                        f"{prereg.sealed_window[0].year}-{prereg.sealed_window[1].year}")  # fmt: skip
    else:
        out["titre"] = ("NON CONCLUANT — " if a1_none else "") + HEADLINE_NONE
    out["regles_de_sortie"] = {
        "ordre_de_lecture": reading_order(cells),
        "libelles_sorties": EXIT_FR,
        "libelles_stops": STOP_FR,
    }
    out["comptes_cellules"] = {"confirmatoires": len(conf), "exploratoires": len(expl),
                               "diagnostics": len(cells) - len(tested), "etape2": len(stage2)}  # fmt: skip
    out["comptes_cellules"]["total_execute"] = sum(out["comptes_cellules"].values())
    return out
