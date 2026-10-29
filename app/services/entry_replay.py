# ruff: noqa: E501
"""docs/34 §34.10 — replay of the entry policies (user request of 29/10/2026), exploratory.

Why: on 29/10/2026 every opportunity's limit (maximum entry price, net reward/risk ≥ ``admission.min_reward_risk``
to the FIRST target) was below the price — « limite posée en attente d'un repli »: nothing is ever bought. Which
ENTRY POLICY turns the detectors' setups into trades with the best net expectancy, on the history stored on the PC?

Mode « setups » (default): every setup the detectors D2, D3, D4, D7 produce (all of them, enabled or not — the
production policy is printed with each; d8 needs the forum radar of the day) on each session of the European values
(groups P0 / P1 / P2 — New York, London and Zurich stay unread until registration n° 2 has run). Detection exactly as
the backtest (``app.services.backtest``): production parameters, RS rank on the session's population, zones on the
days a detector fires, admission (R4/R5). Setups without exploitable target, with a too wide stop or a degraded
configuration are counted and left out (production never orders them). The six policies of
``app.domain.entry_policies`` are simulated independently on each setup, with the same exit logic and the full
round-trip costs of the backtest (CTO cash; PEA when the value is eligible).

Mode « proposals »: the proposals actually stored since the first one (``proposals`` + ``decision_snapshots``:
limit, stop, targets, creation time, detector of the signal), replayed with the same policies on the bars after their
creation — what would have happened to what the tool proposed.

Point-in-time: indicators, zones and ranks on data up to the setup day; fills from the next session; the sessions are
the value's own stored bars (its venue's calendar). Prices as the backtest: stored (unadjusted), a setup whose span
meets a corporate action (adjustment factor jump) is left out. Prices are read per venue group by chunks (server-side
cursor) into arrays, bars are built one value at a time. The reports hold aggregates only: no price leaves the PC.
"""

from __future__ import annotations

import bisect
import json
import logging
import random
import statistics
import time as _time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import LoadedConfig
from app.domain import entry_policies as ep
from app.domain.admission import AdmissionInput, Status, admit
from app.domain.calendar import MarketCalendars
from app.domain.costs import CostParams, round_trip_fee_fraction
from app.domain.detectors import DETECTORS, DetectorContext, Setup
from app.domain.indicators import Bar, DailyFeatures, compute_daily_features
from app.domain.zones import detect_zones

log = logging.getLogger("bourse.entry_replay")
Progress = Callable[[str], None]
HOLDING_DAYS = {"days": 5, "swing_2_6w": 20, "semaines": 40}  # as the backtest
WINDOW = 280  # sessions given to the detectors (as the backtest)
MIN_BARS = 260  # D7 needs 200 + its slope; the RS rank needs 250 sessions (load_history keeps ≥ 260 rows)
LOOKBACK_DAYS = 420  # calendar days read before the start (as load_history)
CA_JUMP = 0.2  # change of the adjustment factor (adj_close / close) between two sessions → corporate action
SKIPPED_DETECTORS = ("d8_buzz_precoce",)  # needs the forum radar of the day: not replayable
EXCURSION_SESSIONS = (5, 10, 20)
STATUS_FR = {
    "admis": "cours ≤ prix maximal à la clôture du signal",
    "refuse": "cours > prix maximal (limite en attente d'un repli)",
}
MIN_CELL_N = 20  # a mean, share or statistic over fewer observations is masked
POSITION_FR = {
    "au_dessus": "cours au-dessus de la limite à la création",
    "au_dessous": "cours au niveau de la limite ou dessous",
}


# --- context ----------------------------------------------------------------------------------------------


@dataclass
class Ctx:
    rp: Any  # app.services.moves.Replay: detectors, admission and sizing values of params.yaml
    pp: ep.PolicyParams
    det_params: dict[str, dict[str, Any]]  # the detectors replayed
    policy_of: dict[str, str]
    standing_max: int
    max_lines: int
    max_pos_pct: float
    start: date
    end: date

    @property
    def months(self) -> float:
        return ((self.end - self.start).days + 1) / 30.4375


def _context(config: LoadedConfig, detectors: list[str] | None, start: date, end: date) -> Ctx:
    from app.services.moves import _replay_context

    rp = _replay_context(config)
    p = config.params
    dets = {k: v for k, v in rp.det_params.items() if k not in SKIPPED_DETECTORS and (not detectors or k in detectors)}
    risk = p.risk
    caps = getattr(risk, "max_open_risk_pct_by_gate", None) or {}
    open_risk = float(caps.get("ouvert") or risk.max_open_risk_pct)
    entry = getattr(p, "entry", None) or {}
    return Ctx(
        rp=rp,
        pp=ep.PolicyParams(slippage_pct=rp.slip, min_reward_risk=rp.k_rr, expiry_sessions=rp.expiry),
        det_params=dets,
        policy_of={
            d: "désactivé (ADR-014)"
            if d in rp.disabled
            else "surveillance seulement (ADR-013)"
            if d in rp.watch_only
            else "actif"
            for d in dets
        },
        standing_max=int(entry.get("standing_orders_max", 4)),
        max_lines=max(1, int(open_risk / float(risk.max_risk_per_trade_pct) + 1e-9)),
        max_pos_pct=float(risk.max_position_pct_of_capital),
        start=start,
        end=end,
    )


def _notional(ctx: Ctx, price: float, stop: float) -> float:
    """Notional sized on the maximum entry price and the slipped stop (CLAUDE.md rule 7), capped per position."""
    rp = ctx.rp
    cap = float(rp.capital) * ctx.max_pos_pct
    stop_exec = stop * (1 - ctx.pp.slippage_pct)
    if price <= stop_exec:
        return cap
    return min(cap, float(rp.risk_budget) * price / (price - stop_exec))


def _admission_fee(ctx: Ctx, st_p_max: float, st_stop: float, cto: CostParams) -> float:
    """Round-trip fee fraction of the admission, exactly as the backtest (notional on the detector's cap)."""
    rp = ctx.rp
    notional = min(rp.capital * ctx.max_pos_pct, rp.risk_budget / max(1e-9, (st_p_max - st_stop) / st_p_max))
    return round_trip_fee_fraction(notional, cto)


def _may_fire(name: str, dp: dict[str, Any], window: list[Bar], rs: float | None) -> bool:
    """Exact necessary conditions of the detectors, checked before their full feature computation (speed only:
    a False here is a case where ``detect`` returns nothing — tests/unit/test_entry_policies.py checks it)."""
    m = dp.get("rs_rank_min")
    if m is not None and (rs is None or rs < float(m)):
        return False  # D2, D3, D4 and D7 all require the RS rank
    if len(window) < 2:
        return False
    last, prev = window[-1], window[-2]
    if name == "d2_breakout":
        if len(window) < 210:
            return False
        piv = max(b.high for b in window[-61:-1])  # indicators.pivot(bars, 60)
        return last.close > piv and prev.close <= piv
    if name == "d3_range_expansion":
        return prev.close > 0 and last.close / prev.close >= float(dp["close_ratio_min"]) and last.volume > prev.volume
    if name in ("d4_pullback", "d7_trend_quality"):
        return last.close > last.open or last.close > prev.close
    return True


def _detect(ctx: Ctx, window: list[Bar], rs: float | None) -> tuple[list[tuple[str, Setup]], DailyFeatures | None]:
    """The setups of the session, as the backtest: each detector without zones, then with the zones of the day."""
    rp = ctx.rp
    base = DetectorContext(rs, [], rp.wick, rp.stop_buffer)
    found: list[tuple[str, Setup]] = []
    f: DailyFeatures | None = None
    zctx: DetectorContext | None = None
    for name, dp in ctx.det_params.items():
        if not _may_fire(name, dp, window, rs) or not DETECTORS[name](dp).detect(window, base):
            continue
        if zctx is None:
            f = compute_daily_features(window)
            zp = rp.zone_p
            zones = detect_zones(
                window[-int(zp.get("lookback_sessions", 250)) :], f.atr14 or 0.0, int(zp.get("pivot_window", 5)),
                float(zp.get("merge_atr_mult", 0.5)), int(zp.get("min_touches", 2)),
            )  # fmt: skip
            zctx = DetectorContext(rs, zones, rp.wick, rp.stop_buffer)
        found += [(name, x) for x in DETECTORS[name](dp).detect(window, zctx)]
    return found, f


# --- one value --------------------------------------------------------------------------------------------


@dataclass
class ValueInput:
    isin: str
    group: str
    days: list[date]
    bars: list[Bar]
    ranks: list[float | None]
    ca: list[int]  # sorted indices of the sessions after a corporate action
    cto: CostParams
    pea: CostParams | None


@dataclass
class PolicyResult:
    order: ep.Order | None
    reason: str | None  # no order: why (ep.NO_ORDER_FR)
    outcome: ep.Outcome | None
    fee_cto: float = 0.0  # round-trip fee fraction (CTO) at the policy's notional
    net_cto: float | None = None
    net_pea: float | None = None
    cf_net: float | None = None  # counterfactual bought at the setup close (CTO)
    span: tuple[int, int, int | None, int | None] | None = None  # (first, end, fill, exit) session ordinals

    @property
    def per_signal(self) -> float | None:
        """R of the signal: 0 without order or without fill, the net R CTO of the trade, None when unknown."""
        if self.outcome is None or not self.outcome.filled:
            return 0.0
        return self.net_cto


@dataclass
class SetupRecord:
    detector: str
    day: date
    status: str  # admis | refuse (close vs maximum entry price at the setup close)
    view: ep.SetupView
    hold: int
    results: dict[str, PolicyResult]
    index: int = -1  # session index of the setup in the value's bars


def corporate_actions(bars_adj: list[float | None], closes: list[float]) -> list[int]:
    """Sessions after a corporate action, as ``backtest.load_history``: the adjustment factor jumps by > 20 %."""
    out: list[int] = []
    prev: float | None = None
    for k, (a, c) in enumerate(zip(bars_adj, closes, strict=True)):
        f = (a / c) if a and c else None
        if f and prev and abs(f / prev - 1) > CA_JUMP:
            out.append(k)
        prev = f or prev
    return out


def _within(sorted_idx: list[int], lo: int, hi: int) -> bool:
    k = bisect.bisect_left(sorted_idx, lo)
    return k < len(sorted_idx) and sorted_idx[k] <= hi


def _policy(ctx: Ctx, v: ValueInput, i: int, sv: ep.SetupView, pol: str, after: list[Bar], hold: int,
            spans: bool = True) -> PolicyResult:  # fmt: skip
    o = ep.plan(pol, sv, ctx.pp)
    if isinstance(o, str):
        return PolicyResult(None, o, None)
    out = ep.simulate(o, after, ctx.pp, hold)
    notional = _notional(ctx, o.max_price, o.planned_stop())
    fc = round_trip_fee_fraction(notional, v.cto)
    res = PolicyResult(o, None, out, fc)
    if out.exit is not None:
        res.net_cto = ep.net_r(out.exit.gross_return, out.exit.risk_fraction, fc)
        if v.pea is not None:
            res.net_pea = ep.net_r(
                out.exit.gross_return, out.exit.risk_fraction, round_trip_fee_fraction(notional, v.pea)
            )
    cf = ep.counterfactual(o, sv.close, after, ctx.pp, hold)
    if cf is not None:
        res.cf_net = ep.net_r(cf.gross_return, cf.risk_fraction, fc)
    if spans and after:

        def od(k: int) -> int:
            return v.days[i + 1 + k].toordinal()

        fill = od(out.fill.index) if out.fill is not None else None
        exit_ = od(out.exit.index) if out.exit is not None else (ep.NEVER if out.fill is not None else None)
        res.span = (od(0), od(max(out.pending_end, 0)), fill, exit_)
    return res


def _span(ctx: Ctx, hold: int) -> int:
    return ctx.pp.max_valid + max(hold, ctx.pp.time_only_hold)


def replay_value(ctx: Ctx, v: ValueInput, excluded: Counter[str]) -> list[SetupRecord]:
    """Every setup of one value on the period and the policies on it. Point-in-time: the detectors and the
    indicators see ``bars[: i + 1]`` only; the orders work from ``bars[i + 1]``."""
    out: list[SetupRecord] = []
    rp = ctx.rp
    for i in range(MIN_BARS - 1, len(v.bars) - 1):
        day = v.days[i]
        if day < ctx.start or day > ctx.end:
            continue
        window = v.bars[max(0, i - WINDOW + 1) : i + 1]
        found, f = _detect(ctx, window, v.ranks[i])
        for name, st in found:
            if not st.targets:
                excluded[f"{name}:sans_objectif"] += 1
                continue
            if st.warnings:
                excluded[f"{name}:stop_trop_large"] += 1
                continue
            last = window[-1]
            frac = _admission_fee(ctx, st.p_max_technical, st.stop, v.cto)
            adm = admit(
                AdmissionInput(
                    last.close, st.targets[0], st.stop, st.kind, st.level_low, frac, rp.k_rr, st.p_max_technical
                )
            )
            if adm.status in (Status.DEGRADED, Status.NOT_TESTABLE) or adm.p_max is None:
                excluded[f"{name}:configuration_degradee"] += 1
                continue
            hold = int(ctx.det_params[name].get("time_stop_sessions") or HOLDING_DAYS.get(st.horizon, 20))
            span = _span(ctx, hold)
            if _within(v.ca, i, i + span):
                excluded[f"{name}:operation_sur_titres"] += 1
                continue
            assert f is not None
            pivot = st.evidence.get("pivot_60") if name == "d2_breakout" else None
            sv = ep.SetupView(
                close=last.close, high=last.high, atr=f.atr14, mm20=f.mm20, high_52w=f.high_52w, trigger=st.entry_level,
                p_max_technical=st.p_max_technical, stop=st.stop, targets=tuple(st.targets), fee_fraction=frac,
                pivot=float(pivot) if pivot else None,
            )  # fmt: skip
            after = v.bars[i + 1 : i + 1 + span]
            results = {pol: _policy(ctx, v, i, sv, pol, after, hold) for pol in ep.POLICIES}
            status = "admis" if adm.status == Status.ADMITTED else "refuse"
            out.append(SetupRecord(name, day, status, sv, hold, results, i))
    return out


def placebo_value(ctx: Ctx, v: ValueInput, real: list[SetupRecord], seed: int) -> list[SetupRecord]:
    """Placebo arm: each real setup moved to a random session of the same value (seeded per value, so the same
    number of signals per value and detector), its geometry kept (every level at the same distance from the close in
    %, ATR included), the same orders and exits. What a policy does mechanically, whatever the detector said."""
    eligible = [i for i in range(MIN_BARS - 1, len(v.bars) - 1) if ctx.start <= v.days[i] <= ctx.end]
    if not eligible:
        return []
    rng = random.Random(f"{seed}:{v.isin}")
    out: list[SetupRecord] = []
    for r in real:
        span = _span(ctx, r.hold)
        for _ in range(5):  # a draw whose span meets a corporate action is drawn again
            i = rng.choice(eligible)
            if not _within(v.ca, i, i + span) and v.bars[i].close > 0:
                break
        else:
            continue
        sv = r.view.scaled(v.bars[i].close / r.view.close)
        after = v.bars[i + 1 : i + 1 + span]
        results = {pol: _policy(ctx, v, i, sv, pol, after, r.hold, spans=False) for pol in ep.POLICIES}
        out.append(SetupRecord(r.detector, v.days[i], r.status, sv, r.hold, results, i))
    return out


# --- accumulation -----------------------------------------------------------------------------------------

CellKey = tuple[str, str, str, str, int, str]  # arm, policy, detector, venue group, year, status
ARMS = ("reel", "placebo")


@dataclass
class Accumulator:
    seed: int = 7
    cells: dict[CellKey, ep.CellStats] = field(default_factory=dict)
    spans: dict[str, list[tuple[str, ep.OrderSpan]]] = field(default_factory=lambda: defaultdict(list))
    setups: Counter[str] = field(default_factory=Counter)
    placebos: Counter[str] = field(default_factory=Counter)
    excluded: Counter[str] = field(default_factory=Counter)
    holds: dict[str, int] = field(default_factory=dict)  # detector → longest holding horizon seen
    values: dict[str, int] = field(default_factory=dict)
    rng: random.Random = field(init=False)

    def __post_init__(self) -> None:
        self.rng = random.Random(self.seed)

    def add(self, isin: str, group: str, recs: Iterable[SetupRecord], arm: str = "reel") -> None:
        vid = self.values.setdefault(isin, len(self.values))
        for r in recs:
            (self.setups if arm == "reel" else self.placebos)[r.detector] += 1
            self.holds[r.detector] = max(self.holds.get(r.detector, 0), r.hold)
            week = ep.week_index(r.day.toordinal())
            base = r.results["P0"].per_signal
            for pol, res in r.results.items():
                cell = self.cells.setdefault((arm, pol, r.detector, group, r.day.year, r.status), ep.CellStats())
                cell.add_setup(res.outcome, res.reason)
                sig = res.per_signal
                if sig is not None:
                    cell.add_signal(sig, week)
                    if pol != "P0" and base is not None:
                        cell.add_delta(sig - base, week)
                if res.outcome is None:
                    continue
                ex = res.outcome.exit
                if ex is not None and res.net_cto is not None:
                    cell.add_trade(ex, res.net_cto, res.net_pea, res.fee_cto, week)
                if res.cf_net is not None:
                    cell.add_counterfactual(res.outcome.filled, res.cf_net)
                if arm == "reel" and res.span is not None:
                    first, end, fill, exit_ = res.span
                    span = ep.OrderSpan(vid, first, end, fill, exit_, res.net_cto, self.rng.random())
                    self.spans[pol].append((r.detector, span))


# --- data -------------------------------------------------------------------------------------------------


@dataclass
class _Panel:
    group: str
    days: np.ndarray  # datetime64[D], the sessions present in the group's data
    isins: list[str]
    open: np.ndarray  # (T, N)
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    adj: np.ndarray
    keep: np.ndarray  # (N,) at least MIN_BARS bars (as load_history)


def _read_group(s: Session, group: str, isins: list[str], lo: date, end: date) -> _Panel | None:
    from app.services.research import _read_price_panel

    days, cols, a = _read_price_panel(s, isins, lo, end)  # chunks through a server-side cursor
    if not cols:
        return None
    a.pop("vintage", None)
    keep = np.isfinite(a["close"]).sum(axis=0) >= MIN_BARS
    return _Panel(group, days, cols, a["open"], a["high"], a["low"], a["close"], a["volume"], a["adj_close"], keep)


def _rank_matrix(panels: list[_Panel]) -> tuple[np.ndarray, list[np.ndarray], np.ndarray]:
    """RS rank (0–100) of every kept value on every session, as ``backtest.rs_ranks``: score 0.4·r12m + 0.2·r6m +
    0.2·r3m + 0.2·r1m on the union of the population's sessions, cross-sectional percentile of the session.
    Returns (sessions, rank column of each kept value per panel, ranks)."""
    import pandas as pd

    days_u = np.unique(np.concatenate([p.days[np.isfinite(p.close[:, p.keep]).any(axis=1)] for p in panels]))
    closes = np.full((days_u.size, int(sum(int(p.keep.sum()) for p in panels))), np.nan)
    cols: list[np.ndarray] = []
    k = 0
    for p in panels:
        js = np.nonzero(p.keep)[0]
        cols.append(np.arange(k, k + js.size))
        pos = np.minimum(np.searchsorted(days_u, p.days), max(days_u.size - 1, 0))
        ok = np.nonzero(days_u[pos] == p.days)[0] if days_u.size else np.array([], dtype=np.int64)
        if js.size and ok.size:
            closes[np.ix_(pos[ok], np.arange(k, k + js.size))] = p.close[np.ix_(ok, js)]
        k += js.size
    df = pd.DataFrame(closes)
    del closes

    def r(n: int) -> pd.DataFrame:
        return df / df.shift(n) - 1

    score = 0.4 * r(250) + 0.2 * r(125) + 0.2 * r(63) + 0.2 * r(21)
    ranks = (score.rank(axis=1, pct=True) * 100).to_numpy(dtype=np.float32)
    return days_u, cols, ranks


def _value_input(p: _Panel, j: int, costs: tuple[CostParams, CostParams | None], days_u: np.ndarray,
                 rank_col: int, ranks: np.ndarray) -> ValueInput:  # fmt: skip
    idx = np.nonzero(np.isfinite(p.close[:, j]))[0]
    vdays = p.days[idx]
    o, h, lo_, c, vol = (x[idx, j].tolist() for x in (p.open, p.high, p.low, p.close, p.volume))
    adj = [None if x != x else x for x in p.adj[idx, j].tolist()]
    bars = [
        Bar(oo if oo == oo else cc, hh if hh == hh else cc, ll if ll == ll else cc, cc, int(vv) if vv == vv else 0)
        for oo, hh, ll, cc, vv in zip(o, h, lo_, c, vol, strict=True)
    ]
    rk = ranks[np.searchsorted(days_u, vdays), rank_col].tolist()
    return ValueInput(
        p.isins[j], p.group, [d.item() for d in vdays.astype("datetime64[D]")], bars,
        [None if x != x else float(x) for x in rk], corporate_actions(adj, c), costs[0], costs[1],
    )  # fmt: skip


# --- mode « setups » --------------------------------------------------------------------------------------


def run_setups(
    s: Session,
    config: LoadedConfig,
    calendars: MarketCalendars,
    start: date,
    end: date,
    detectors: list[str] | None = None,
    max_values: int | None = None,
    progress: Progress | None = None,
    seed: int = 7,
    universe: str = "all",
) -> dict[str, Any]:
    from app.services.moves import _costs
    from app.services.research import select_instruments

    t0 = _time.monotonic()
    say = progress or (lambda m: None)
    ctx = _context(config, detectors, start, end)
    by_group, without = select_instruments(s, config, calendars, universe, max_values)  # P0 / P1 / P2 only
    insts = {i.isin: i for grp in by_group.values() for i in grp}
    lo = start - timedelta(days=LOOKBACK_DAYS)
    panels: list[_Panel] = []
    for grp in sorted(by_group):
        say(f"{grp} : lecture des cours de {len(by_group[grp])} valeur(s) depuis {lo}")
        p = _read_group(s, grp, [i.isin for i in by_group[grp]], lo, end)
        if p is not None:
            panels.append(p)
    n_kept = int(sum(int(p.keep.sum()) for p in panels))
    say(f"{n_kept} valeur(s) avec au moins {MIN_BARS} séances ; rangs de force relative…")
    acc = Accumulator(seed)
    if n_kept:
        days_u, rank_cols, ranks = _rank_matrix(panels)
        done = 0
        for p, cols in zip(panels, rank_cols, strict=True):
            for rank_col, j in zip(cols.tolist(), np.nonzero(p.keep)[0].tolist(), strict=True):
                inst = insts[p.isins[j]]
                v = _value_input(p, j, _costs(config, inst), days_u, rank_col, ranks)
                real = replay_value(ctx, v, acc.excluded)
                acc.add(v.isin, v.group, real)
                acc.add(v.isin, v.group, placebo_value(ctx, v, real, seed), "placebo")
                done += 1
                if done % 50 == 0 or done == n_kept:
                    say(f"{done}/{n_kept} valeurs rejouées · {sum(acc.setups.values())} configurations · "
                        f"{(_time.monotonic() - t0) / 60:.1f} min")  # fmt: skip
        del ranks
    del panels
    meta = {
        "mode": "setups",
        "debut": start.isoformat(),
        "fin": end.isoformat(),
        "univers": universe,
        "valeurs_selectionnees": len(insts),
        "valeurs_rejouees": n_kept,
        "groupes": sorted(by_group),
        "sans_groupe": without,
        "mois": round(ctx.months, 1),
        "graine": seed,
        "n_minimum": MIN_CELL_N,
        "detecteurs": ctx.policy_of,
        "configurations": dict(acc.setups),
        "placebos": dict(acc.placebos),
        "exclues": dict(sorted(acc.excluded.items())),
        "parametres": _parameters(ctx),
        "duree_minutes": round((_time.monotonic() - t0) / 60, 1),
    }
    say("agrégation…")
    return build_report(acc, ctx, meta)


def _parameters(ctx: Ctx) -> dict[str, Any]:
    rp = ctx.rp
    return {
        "politiques": asdict(ctx.pp),
        "ordres_en_attente_max": ctx.standing_max,
        "lignes_de_risque_max": ctx.max_lines,
        "capital_eur": rp.capital,
        "budget_risque_eur": rp.risk_budget,
        "position_max_pct": ctx.max_pos_pct,
        "horizons_seances": {d: int(dp.get("time_stop_sessions") or 0) or None for d, dp in ctx.det_params.items()},
    }


def _months_in_year(ctx: Ctx, year: int) -> float:
    lo, hi = max(ctx.start, date(year, 1, 1)), min(ctx.end, date(year, 12, 31))
    return max(0, (hi - lo).days + 1) / 30.4375


def _rollup(cells: dict[CellKey, ep.CellStats], keyf: Callable[[CellKey], Any]) -> dict[Any, ep.CellStats]:
    """Cells merged by ``keyf`` in one pass (a key of None leaves the cell out)."""
    out: dict[Any, ep.CellStats] = {}
    for k, c in cells.items():
        key = keyf(k)
        if key is not None:
            out.setdefault(key, ep.CellStats()).merge(c)
    return out


def build_report(acc: Accumulator, ctx: Ctx, meta: dict[str, Any]) -> dict[str, Any]:
    cells = acc.cells
    months = ctx.months
    seed = acc.seed
    pp = ctx.pp

    def lag(det: str | None) -> int:
        """Newey-West lag and bootstrap block in weeks: the longest holding of the cell (overlapping trades)."""
        holds = [acc.holds.get(det, 20)] if det else list(acc.holds.values()) or [20]
        return max(1, -(-max(max(holds), pp.time_only_hold) // 5))

    def d(c: ep.CellStats, pol: str, det: str | None, m: float | None = months) -> dict[str, Any]:
        return c.as_dict(m, lag(det), seed, MIN_CELL_N, delta=pol != "P0")

    dets = sorted({k[2] for k in cells})
    rep: dict[str, Any] = {"meta": meta, "politiques": dict(ep.POLICY_FR)}
    pooled = _rollup(cells, lambda k: k[:2])
    by_det = _rollup(cells, lambda k: k[:3])
    rep["synthese"] = {
        arm: {
            pol: {
                "tous": d(pooled.get((arm, pol), ep.CellStats()), pol, None),
                "par_detecteur": {x: d(by_det.get((arm, pol, x), ep.CellStats()), pol, x) for x in dets},
            }
            for pol in ep.POLICIES
        }
        for arm in ARMS
    }
    real = {k: c for k, c in cells.items() if k[0] == "reel"}
    st_all = _rollup(real, lambda k: (k[1], k[5]))
    st_det = _rollup(real, lambda k: (k[1], k[2], k[5]))
    rep["par_statut"] = {
        pol: {
            "tous": {st: d(st_all[(pol, st)], pol, None) for st in ("admis", "refuse") if (pol, st) in st_all},
            "par_detecteur": {
                x: {st: d(st_det[(pol, x, st)], pol, x) for st in ("admis", "refuse") if (pol, x, st) in st_det}
                for x in dets
            },
        }
        for pol in ep.POLICIES
    }
    grp = _rollup(real, lambda k: (k[1], k[3]))
    rep["par_groupe"] = {
        pol: {g: d(c, pol, None) for (p_, g), c in sorted(grp.items()) if p_ == pol} for pol in ep.POLICIES
    }
    yr = _rollup(real, lambda k: (k[1], k[4]))
    rep["par_annee"] = {
        pol: {str(y): d(c, pol, None, _months_in_year(ctx, y)) for (p_, y), c in sorted(yr.items()) if p_ == pol}
        for pol in ep.POLICIES
    }
    grid = _rollup(real, lambda k: k[1:5])
    rep["cellules"] = [
        {"politique": pol, "detecteur": x, "groupe": g, "annee": y, **d(c, pol, x, _months_in_year(ctx, y))}
        for (pol, x, g, y), c in sorted(grid.items(), key=lambda kv: (ep.POLICIES.index(kv[0][0]), kv[0][1:]))
    ]
    rep["essais"] = len(rep["cellules"])
    rep["rythme"] = {}
    for pol in ep.POLICIES:
        sp = acc.spans.get(pol, [])
        allsp = [x for _, x in sp]
        rep["rythme"][pol] = {
            "parametres": ep.pace(allsp, ctx.standing_max, ctx.max_lines, months, MIN_CELL_N),
            "emplacements_lignes": ep.pace(allsp, ctx.max_lines, ctx.max_lines, months, MIN_CELL_N),
            "par_detecteur": {
                x: ep.pace([o for dd, o in sp if dd == x], ctx.standing_max, ctx.max_lines, months, MIN_CELL_N)
                for x in dets
            },
        }
    rep["notes"] = NOTES_SETUPS + [
        f"{sum(v for k, v in acc.excluded.items() if k.endswith('operation_sur_titres'))} configuration(s) exclue(s) : "
        "opération sur titres (division…) entre le signal et la sortie possible, cours non ajustés"
    ]
    return rep


NOTES_SETUPS = [
    "exploratoire : aucune règle ne change ; une politique ne s'adopte que par un plan pré-enregistré confirmé sur la "
    "période scellée (docs/31, docs/34 §34.10)",
    "biais de survie : univers actuel, les valeurs européennes radiées manquent — résultats optimistes",
    "ni lettres, ni nouvelles, ni fondamentaux, ni score, ni portail de marché, ni gel avant résultats, ni niveau de "
    "risque du jour ; capitalisation, TTF et éligibilité PEA d'aujourd'hui",
    "chaque configuration est simulée indépendamment : les signaux d'une même valeur sur des séances voisines se "
    "chevauchent — lire les t par semaine (erreur groupée ; Newey-West pour le R par signal et l'écart à P0)",
    "exécution d'une limite dès que le plus bas l'atteint (le backtest exige qu'il la traverse) : légèrement favorable "
    "aux politiques à limite (P0, P3, P4) ; aucun écart acheteur-vendeur au-delà du glissement du stop (voir le "
    "stress des coûts × 1,5)",
    "placebo : chaque configuration réelle déplacée à une séance tirée au hasard de la même valeur (graine), mêmes "
    "distances en % (niveaux, ATR), mêmes ordres et sorties — l'effet mécanique de la politique sans le détecteur",
    "P2' : seuil = pivot × 1,003 (D2, soit le déclencheur de docs/06 §6.3) ou déclencheur du détecteur × 1,003 "
    "(autres), limite = prix maximal de production (R4), valable la seule séance suivante",
    "le rythme rejoue les ordres dans l'ordre chronologique avec au plus `entry.standing_orders_max` ordres en "
    "attente (puis autant d'emplacements que de lignes de risque) et « risque ouvert ÷ risque par trade » lignes, une "
    "seule par valeur ; le départ entre ordres du même jour est tiré au hasard (graine)",
    "toute moyenne, part ou statistique calculée sur moins de 20 observations est masquée (« n<20 »)",
]


# --- mode « proposals » -----------------------------------------------------------------------------------


@dataclass
class ProposalStats:
    n: int = 0
    orders: int = 0
    fills: int = 0
    closed: int = 0
    wins: int = 0
    net: ep.Moments = field(default_factory=ep.Moments)
    running: int = 0
    latent: ep.Moments = field(default_factory=ep.Moments)
    outcomes: Counter[str] = field(default_factory=Counter)
    no_order: Counter[str] = field(default_factory=Counter)
    no_fill: Counter[str] = field(default_factory=Counter)

    def add(self, res: PolicyResult) -> None:
        self.n += 1
        if res.outcome is None:
            self.no_order[res.reason or "?"] += 1
            return
        self.orders += 1
        out = res.outcome
        if not out.filled:
            self.no_fill[out.no_fill or "?"] += 1
            return
        self.fills += 1
        if out.exit is None or res.net_cto is None:
            self.outcomes["inconnue"] += 1
            return
        self.outcomes[out.exit.reason] += 1
        if out.exit.closed:
            self.closed += 1
            self.wins += res.net_cto > 0
            self.net.add(res.net_cto)
        else:
            self.running += 1
            self.latent.add(res.net_cto)

    def as_dict(self, min_n: int = MIN_CELL_N) -> dict[str, Any]:

        def r3(x: float | None, n: int) -> float | None:
            return None if x is None or n < min_n else round(x, 3)

        return {
            "propositions": self.n,
            "ordres": self.orders,
            "executes": self.fills,
            "taux_execution": round(self.fills / self.orders, 4) if self.orders >= min_n else None,
            "clotures": self.closed,
            "gagnants": round(self.wins / self.closed, 4) if self.closed >= min_n else None,
            "r_net_cto_moyen": r3(self.net.mean, self.net.n),
            "en_cours": self.running,
            "r_net_latent_moyen": r3(self.latent.mean, self.latent.n),
            "issues": dict(self.outcomes),
            "sans_ordre": dict(self.no_order),
            "non_executes": dict(self.no_fill),
        }


def _f(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None


def _venue_times(calendars: MarketCalendars, mic: str | None) -> tuple[ZoneInfo, time, time]:
    try:
        v = calendars.get(mic or "")
        return v.tz, v.open_time, v.close_time
    except (KeyError, ValueError, AttributeError):
        return ZoneInfo("Europe/Paris"), time(9, 0), time(17, 30)


def session_indices(days: list[date], created: datetime, tz: ZoneInfo, open_t: time, close_t: time) -> tuple[int, int]:
    """(index of the setup session, index of the first session the orders may work). The setup session is the last
    one **completed** before the creation; the first working session is the first one that **opens** after it (a
    proposal created during a session skips the rest of that session: what happened before the creation is unknown
    on daily bars)."""
    local = (created if created.tzinfo else created.replace(tzinfo=UTC)).astimezone(tz)
    d, t = local.date(), local.time().replace(tzinfo=None)
    setup = bisect.bisect_right(days, d) - 1 if t >= close_t else bisect.bisect_left(days, d) - 1
    first = bisect.bisect_left(days, d) if t < open_t else bisect.bisect_right(days, d)
    return setup, first


def run_proposals(
    s: Session,
    config: LoadedConfig,
    calendars: MarketCalendars,
    end: date,
    progress: Progress | None = None,
    earliest: date = date(2017, 1, 2),
    min_n: int = MIN_CELL_N,
) -> dict[str, Any]:
    from app.db.models import DecisionSnapshot, Instrument, PriceEod, Proposal, Signal
    from app.services.moves import _costs
    from app.services.research import mic_groups

    say = progress or (lambda m: None)
    ctx = _context(config, None, earliest, end)
    groups_of = mic_groups(config, calendars)
    props = list(s.scalars(select(Proposal).order_by(Proposal.created_at, Proposal.id)))
    say(f"{len(props)} proposition(s) enregistrée(s)")
    excluded: Counter[str] = Counter()
    groups: dict[str, dict[str, dict[str, ProposalStats]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(ProposalStats))
    )
    exc: dict[str, dict[str, dict[int, list[tuple[float, float]]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )
    bars_cache: dict[str, tuple[list[date], list[Bar], list[int]]] = {}
    kept = 0
    created_days: list[date] = []
    for p in props:
        if p.deleted_at is not None:
            excluded["retiree"] += 1
            continue
        inst = s.get(Instrument, p.isin)
        if inst is None:
            excluded["valeur_inconnue"] += 1
            continue
        if inst.mic not in groups_of:
            excluded["hors_perimetre_europeen"] += 1  # New York, London, Zurich: test data of registration n° 2
            continue
        sig = s.get(Signal, p.signal_id)
        snap = s.scalar(select(DecisionSnapshot).where(DecisionSnapshot.proposal_id == p.id))
        d = p.details or {}
        conf = d.get("configuration") or {}
        stop = _f(p.stop) or (_f(sig.stop_initial) if sig else None)
        raw_t = p.targets or (sig.targets if sig else None) or []
        targets = tuple(sorted(t for t in (_f(x) for x in raw_t) if t is not None))
        p_max = _f(d.get("p_max")) or (_f(sig.entry_high) if sig else None) or _f((p.entry_zone or {}).get("limite"))
        if stop is None or not targets or p_max is None:
            excluded["niveaux_incomplets"] += 1
            continue
        if p.isin not in bars_cache:
            lo = max(
                earliest - timedelta(days=LOOKBACK_DAYS),
                (p.created_at.date() if p.created_at else end) - timedelta(days=LOOKBACK_DAYS),
            )
            rows = s.execute(
                select(
                    PriceEod.day,
                    PriceEod.open,
                    PriceEod.high,
                    PriceEod.low,
                    PriceEod.close,
                    PriceEod.volume,
                    PriceEod.adj_close,
                )
                .where(PriceEod.isin == p.isin, PriceEod.day >= lo, PriceEod.day <= end)
                .order_by(PriceEod.day)
            ).all()
            days_v = [r[0] for r in rows]
            bars_v = [Bar(float(r[1]), float(r[2]), float(r[3]), float(r[4]), int(r[5] or 0)) for r in rows]
            bars_cache[p.isin] = (
                days_v,
                bars_v,
                corporate_actions([_f(r[6]) for r in rows], [float(r[4]) for r in rows]),
            )
        days_v, bars_v, ca = bars_cache[p.isin]
        tz, open_t, close_t = _venue_times(calendars, inst.mic)
        created = p.created_at if p.created_at.tzinfo else p.created_at.replace(tzinfo=UTC)
        setup_i, first = session_indices(days_v, created, tz, open_t, close_t)
        if setup_i < 20:
            excluded["historique_insuffisant"] += 1
            continue
        if first >= len(days_v):
            excluded["aucune_seance_depuis"] += 1
            continue
        det = sig.detector if sig else "?"
        horizon = p.horizon or (sig.horizon if sig else None)
        hold = int(
            (ctx.det_params.get(det) or ctx.rp.det_params.get(det) or {}).get("time_stop_sessions")
            or HOLDING_DAYS.get(horizon or "", 20)
        )
        span = ctx.pp.max_valid + hold
        if _within(ca, setup_i, first + span):
            excluded["operation_sur_titres"] += 1
            continue
        window = bars_v[max(0, setup_i - WINDOW + 1) : setup_i + 1]
        feats = (snap.features if snap else None) or {}
        f = compute_daily_features(window)
        last = window[-1]
        cto, pea = _costs(config, inst)
        pmt = _f(conf.get("p_max_technique")) or p_max
        trigger = (_f(sig.entry_low) if sig else None) or _f(conf.get("declencheur")) or p_max
        sv = ep.SetupView(
            close=last.close, high=last.high, atr=_f(feats.get("atr14")) or f.atr14, mm20=_f(feats.get("mm20")) or f.mm20,
            high_52w=_f(feats.get("high_52w")) or f.high_52w, trigger=trigger, p_max_technical=pmt, stop=stop,
            targets=targets, fee_fraction=_admission_fee(ctx, pmt, stop, cto) if pmt > stop else 0.0, p_max_stored=p_max,
            pivot=_f(((sig.evidence or {}) if sig else {}).get("pivot_60")) if det == "d2_breakout" else None,
        )  # fmt: skip
        quotes = (snap.quotes if snap else None) or {}
        q = quotes.get(p.isin) or (next(iter(quotes.values()), {}) if quotes else {})
        price = _f(q.get("last") if isinstance(q, dict) else None) or last.close
        position = "au_dessus" if price > p_max else "au_dessous"
        after_all = bars_v[first:]
        after = after_all[:span]
        v = ValueInput(p.isin, groups_of[inst.mic], days_v, bars_v, [], ca, cto, pea)
        keys = {"tous": "tous", "detecteur": det, "position": position, "action": p.action}
        for pol in ep.POLICIES:
            o = ep.plan(pol, sv, ctx.pp)
            if isinstance(o, str):
                res = PolicyResult(None, o, None)
            else:
                out = ep.simulate(o, after, ctx.pp, hold, open_ok=True)
                res = PolicyResult(o, None, out)
                if out.exit is not None:
                    fc = round_trip_fee_fraction(_notional(ctx, o.max_price, o.planned_stop()), v.cto)
                    res.net_cto = ep.net_r(out.exit.gross_return, out.exit.risk_fraction, fc)
            for dim, key in keys.items():
                groups[dim][key][pol].add(res)
        for n in EXCURSION_SESSIONS:
            x = ep.excursions(after_all, price, n)
            if x is not None:
                for dim, key in keys.items():
                    exc[dim][key][n].append(x)
        kept += 1
        if p.created_at:
            created_days.append(created.date())
    say(f"{kept} proposition(s) rejouée(s)")

    def excursion_dict(xs: dict[int, list[tuple[float, float]]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for n in EXCURSION_SESSIONS:
            v_ = xs.get(n) or []
            out[str(n)] = {
                "n": len(v_),
                "mfe_mediane": round(statistics.median(a for a, _ in v_), 4) if len(v_) >= min_n else None,
                "mae_mediane": round(statistics.median(b for _, b in v_), 4) if len(v_) >= min_n else None,
            }
        return out

    rep: dict[str, Any] = {
        "meta": {
            "mode": "proposals",
            "fin": end.isoformat(),
            "propositions": len(props),
            "rejouees": kept,
            "exclues": dict(sorted(excluded.items())),
            "premiere_creation": min(created_days).isoformat() if created_days else None,
            "derniere_creation": max(created_days).isoformat() if created_days else None,
            "detecteurs": ctx.policy_of,
            "parametres": _parameters(ctx),
            "n_minimum": min_n,
        },
        "politiques": dict(ep.POLICY_FR),
    }
    for dim in ("tous", "detecteur", "position", "action"):
        rep[f"par_{dim}"] = {
            key: {
                "politiques": {pol: groups[dim][key][pol].as_dict(min_n) for pol in ep.POLICIES},
                "excursions": excursion_dict(exc[dim][key]),
            }
            for key in sorted(groups[dim])
        }
    rep["notes"] = NOTES_PROPOSALS
    return rep


NOTES_PROPOSALS = [
    "exploratoire : ce que seraient devenues les propositions enregistrées, sur les séances qui suivent leur création "
    "(une proposition créée pendant une séance commence à la séance suivante : l'ordre des prix avant la création est "
    "inconnu sur des barres quotidiennes)",
    "P0 rejoue la limite publiée par l'outil (prix maximal enregistré) ; les autres politiques partent de la même "
    "configuration (stop, objectifs) ; ATR, MM20 et plus haut 52 semaines de l'instantané de décision, sinon recalculés "
    "sur les cours jusqu'à la séance du signal",
    "« en cours » : la durée de détention n'est pas écoulée — résultat latent à la dernière clôture, jamais mêlé aux "
    "trades clôturés",
    "excursions : plus haut et plus bas des 5, 10 et 20 séances suivantes rapportés au cours à la création",
    "places européennes seulement (New York, Londres et Zurich : données de test de l'enregistrement n° 2)",
]


# --- report -----------------------------------------------------------------------------------------------

MASK = "n<20"


def _pct(x: Any, nd: int = 1) -> str:
    return "—" if x is None else f"{x * 100:.{nd}f} %".replace(".", ",")


def _num(x: Any, fmt: str = "+.3f") -> str:
    return "—" if x is None else format(x, fmt)


def _mk(v: dict[str, Any], x: Any, fmt: str = "+.3f", pct: bool = False) -> str:
    """A statistic of a cell, « n<20 » when the cell is masked."""
    if x is None and v.get("masque"):
        return MASK
    return _pct(x) if pct else _num(x, fmt)


def _ws(w: dict[str, Any] | None, key: str, fmt: str = "+.3f") -> str:
    if not w:
        return "—"
    if w.get("masque"):
        return MASK
    x = w.get(key)
    if key == "ic95":
        return "—" if not x else f"[{x[0]:+.3f} ; {x[1]:+.3f}]"
    if key == "plis":
        return "—" if not x else f"{''.join(x)} ({w.get('plis_positifs')}/5)"
    return _num(x, fmt)


TRADE_HEAD = ["config.", "ordres", "exécution", "à l'ouverture", "délai", "trades", "gagnants", "R brut", "coût R",
              "R net CTO", "R net ×1,5 coûts", "R net PEA", "t sem.", "PF", "durée", "objectif", "stop"]  # fmt: skip
SIGNAL_HEAD = ["R / signal", "t NW", "Δ vs P0", "t NW", "IC95 bootstrap", "plis +", "placebo R / signal",
               "placebo Δ vs P0", "placebo t NW"]  # fmt: skip


def _table(first: str, head: list[str]) -> list[str]:
    return ["| " + " | ".join([first, *head]) + " |", "|" + "---|" * (len(head) + 1)]


def _trade_row(label: str, v: dict[str, Any]) -> str:
    cells = [
        label, str(v["setups"]), str(v["ordres"]), _pct(v["taux_execution"]), _pct(v["part_execution_ouverture"]),
        _num(v["delai_execution_moyen"], ".1f"), str(v["trades"]), _mk(v, v["gagnants"], pct=True),
        _mk(v, v["r_brut_moyen"]), _mk(v, v["cout_r_moyen"], ".3f"), _mk(v, v["r_net_cto"]),
        _mk(v, v["r_net_cto_couts_x1_5"]), _num(v["r_net_pea"]), _mk(v, v["t_net_cto_semaine"], ".1f"),
        _mk(v, v["profit_factor"], ".2f"),
        MASK if v.get("masque") else "—" if v["duree_mediane_seances"] is None else f"{v['duree_mediane_seances']:g}",
        _mk(v, v["part_objectif"], pct=True), _mk(v, v["part_stop"], pct=True),
    ]  # fmt: skip
    return "| " + " | ".join(cells) + " |"


def _signal_row(label: str, v: dict[str, Any], plac: dict[str, Any] | None) -> str:
    sig, dl = v.get("r_par_signal"), v.get("delta_vs_p0")
    ps, pd_ = (plac or {}).get("r_par_signal"), (plac or {}).get("delta_vs_p0")
    cells = [label, _ws(sig, "moyenne"), _ws(sig, "t_nw", ".1f"), _ws(dl, "moyenne"), _ws(dl, "t_nw", ".1f"),
             _ws(dl, "ic95"), _ws(dl, "plis"), _ws(ps, "moyenne"), _ws(pd_, "moyenne"), _ws(pd_, "t_nw", ".1f")]  # fmt: skip
    return "| " + " | ".join(cells) + " |"


def _adverse_row(label: str, v: dict[str, Any]) -> str:
    a = v["selection_adverse"]

    def cell(x: Any, n: int) -> str:
        return f"{MASK if x is None and n else _num(x)} (n {n})"

    return (f"| {label} | {cell(a['cf_executes'], a['n_executes'])} | {cell(a['cf_non_executes'], a['n_non_executes'])} | "
            f"{cell(a['cf_tous'], a['n_tous'])} | {_num(a['ecart'])} |")  # fmt: skip


def _pace_row(label: str, x: dict[str, Any]) -> str:
    rn = MASK if x["r_net_moyen"] is None and x["trades"] else _num(x["r_net_moyen"])
    rm = MASK if x["r_net_par_mois"] is None and x["trades"] else _num(x["r_net_par_mois"], "+.2f")
    return (f"| {label} | {x['emplacements']} / {x['lignes']} | {x['ordres_possibles']} | {_num(x['ordres_par_mois'], '.1f')} | "
            f"{_num(x['executes_par_mois'], '.1f')} | {_num(x['trades_par_mois'], '.1f')} | {rn} | {rm} |")  # fmt: skip


def _policy_block(L: list[str], rows: dict[str, dict[str, Any]], plac: dict[str, dict[str, Any]] | None) -> None:
    L.append("")
    L.extend(_table("politique", TRADE_HEAD))
    for pol, v in rows.items():
        L.append(_trade_row(pol, v))
    L.append("")
    L.extend(_table("politique", SIGNAL_HEAD))
    for pol, v in rows.items():
        L.append(_signal_row(pol, v, (plac or {}).get(pol)))


def render_fr(rep: dict[str, Any]) -> str:
    if rep["meta"].get("mode") == "proposals":
        return _render_proposals(rep)
    m = rep["meta"]
    pp = m["parametres"]["politiques"]
    L: list[str] = []
    w = L.append
    w("# Rejeu des politiques d'entrée (docs/34 §34.10) — exploratoire")
    w("")
    w(f"Période {m['debut']} → {m['fin']} ({m['mois']} mois) · places européennes {', '.join(m['groupes'])} "
      f"(New York, Londres, Zurich exclues : enregistrement n° 2) · univers « {m['univers']} » · "
      f"{m['valeurs_rejouees']} valeurs rejouées sur {m['valeurs_selectionnees']} · graine {m['graine']} · "
      f"{m['duree_minutes']} min")  # fmt: skip
    w("")
    w("**Aucune règle ne change avec ce rapport.** Une politique ne s'adopte qu'au terme d'un plan pré-enregistré "
      "(n° 4), confirmé sur la période scellée. Agrégats seulement : aucun cours, aucune valeur citée ; toute "
      "statistique sur moins de 20 observations est masquée (« n<20 »).")  # fmt: skip
    w("")
    w("## Politiques comparées (mêmes configurations, même sortie, coût complet)")
    w("")
    for k, v in rep["politiques"].items():
        w(f"- **{k}** — {v}")
    w("")
    w(f"Sortie commune : stop de la configuration glissé de {_pct(pp['slippage_pct'], 2)}, objectif propre à la politique, "
      f"sortie au temps après l'horizon du détecteur (P5' : {pp['time_only_hold']} séances). Validités : limite "
      f"P0/P3 {pp['expiry_sessions']} séances, stop d'achat P2 {pp['confirm_sessions']}, P2' 1, repli P4 "
      f"{pp['pullback_sessions']} ; ratio minimal {pp['min_reward_risk']:g}. Coûts : CTO comptant (Saxo) comme le "
      f"backtest ; PEA (BoursoBank) quand la valeur est éligible.")  # fmt: skip
    w("")
    w("Détecteurs rejoués (tous, quelle que soit la politique de production) : "
      + " · ".join(f"{d} — {pol} ({m['configurations'].get(d, 0)} configurations, {m['placebos'].get(d, 0)} placebos)"
                   for d, pol in m["detecteurs"].items()))  # fmt: skip
    if m.get("exclues"):
        w("")
        w(
            "Configurations écartées (la production ne pose pas d'ordre) : "
            + " · ".join(f"{k} {v}" for k, v in m["exclues"].items())
        )
    syn, plac = rep["synthese"]["reel"], rep["synthese"]["placebo"]
    w("")
    w("## 1. Synthèse par politique (tous détecteurs)")
    _policy_block(L, {p: v["tous"] for p, v in syn.items()}, {p: v["tous"] for p, v in plac.items()})
    w("")
    w("### Sélection adverse : achat à la clôture du signal, même stop et même objectif")
    w("")
    w("| politique | ordres exécutés | ordres non exécutés | tous les ordres | écart (non exécutés − exécutés) |")
    w("|---|---|---|---|---|")
    for pol, v in syn.items():
        w(_adverse_row(pol, v["tous"]))
    w("")
    w("### Rythme au niveau du livre d'ordres (tous détecteurs)")
    w("")
    w(
        "| politique · plafond | ordres / lignes | ordres possibles | ordres placés / mois | exécutés / mois | trades / mois | R net moyen | R net / mois |"
    )
    w("|---|---|---|---|---|---|---|---|")
    for pol, v in rep["rythme"].items():
        w(_pace_row(f"{pol} · paramètres", v["parametres"]))
        w(_pace_row(f"{pol} · {v['emplacements_lignes']['emplacements']} emplacements", v["emplacements_lignes"]))
    w("")
    w("## 2. Par détecteur")
    for d in sorted(m["detecteurs"]):
        w("")
        w(f"### {d} — {m['detecteurs'][d]}")
        _policy_block(L, {p: v["par_detecteur"].get(d, {}) for p, v in syn.items() if d in v["par_detecteur"]},
                      {p: v["par_detecteur"].get(d, {}) for p, v in plac.items()})  # fmt: skip
        w("")
        w("| politique | ordres exécutés | ordres non exécutés | tous les ordres | écart |")
        w("|---|---|---|---|---|")
        for pol, v in syn.items():
            if d in v["par_detecteur"]:
                w(_adverse_row(pol, v["par_detecteur"][d]))
        w("")
        w(
            "| politique · plafond | ordres / lignes | ordres possibles | ordres placés / mois | exécutés / mois | trades / mois | R net moyen | R net / mois |"
        )
        w("|---|---|---|---|---|---|---|---|")
        for pol, v in rep["rythme"].items():
            if d in v["par_detecteur"]:
                w(_pace_row(pol, v["par_detecteur"][d]))
    w("")
    w("## 3. Cours au-dessus ou au-dessous du prix maximal à la clôture du signal")
    w("")
    w("« refuse » : le cours dépassait le prix maximal (cas du 29/10/2026 : limite posée en attente d'un repli).")
    for st in ("admis", "refuse"):
        w("")
        w(f"### {st} — {STATUS_FR[st]}")
        _policy_block(L, {p: v["tous"][st] for p, v in rep["par_statut"].items() if st in v["tous"]}, None)
    w("")
    w("## 4. Par groupe de places")
    w("")
    L.extend(_table("politique · groupe", TRADE_HEAD))
    for pol, v in rep["par_groupe"].items():
        for g, x in v.items():
            w(_trade_row(f"{pol} · {g}", x))
    w("")
    w("## 5. Par année (stabilité)")
    w("")
    L.extend(
        _table("politique · année", ["config.", "exécution", "trades", "R net CTO", "t sem.", "R / signal", "Δ vs P0"])
    )
    for pol, v in rep["par_annee"].items():
        for y, x in v.items():
            w(f"| {pol} · {y} | {x['setups']} | {_pct(x['taux_execution'])} | {x['trades']} | {_mk(x, x['r_net_cto'])} | "
              f"{_mk(x, x['t_net_cto_semaine'], '.1f')} | {_ws(x.get('r_par_signal'), 'moyenne')} | "
              f"{_ws(x.get('delta_vs_p0'), 'moyenne')} |")  # fmt: skip
    w("")
    w("## 6. Comment lire ce rapport")
    w("")
    w(
        "- **exécution** : part des ordres exécutés ; **à l'ouverture** : part des exécutions à l'ouverture (écart), le "
        "reste en séance ; **délai** : séances entre le signal et l'exécution (1 = la séance suivante) ;"
    )
    w(
        "- **trades** : ordres exécutés dont l'issue est connue (une issue inconnue en fin d'historique n'est jamais "
        "comptée) ; **R** : résultat rapporté au risque prévu (entrée − stop glissé) ; **R net** après l'aller-retour "
        "complet (commission, TTF, taxe étrangère, change) ; **coût R** : ce que l'aller-retour coûte en R ; "
        "**×1,5 coûts** : le même R net avec des coûts majorés de moitié ;"
    )
    w(
        "- **R / signal** : moyenne sur toutes les configurations, un ordre non posé ou non exécuté comptant 0 R — "
        "c'est la grandeur qui compare des politiques au taux d'exécution différent ; **Δ vs P0** : écart apparié "
        "(même configuration, politique moins P0), t de Newey-West sur les agrégats hebdomadaires, intervalle par "
        "rééchantillonnage par blocs de semaines, signe sur 5 plis chronologiques ;"
    )
    w(
        "- **t sem.** : t du R net par trade avec une erreur groupée par semaine ; une règle « découverte » exige t ≥ 3 "
        f"(Harvey, Liu & Zhu) et ce rapport compte **{rep['essais']} cellules** (politique × détecteur × groupe × année) ;"
    )
    w(
        "- **placebo** : les mêmes ordres sur des dates tirées au hasard de la même valeur ; si une politique fait aussi "
        "bien sur le placebo, son effet est mécanique (exécution, sortie), pas dû au signal ;"
    )
    w(
        "- **sélection adverse** : achat à la clôture du signal avec le même stop et le même objectif, pour les ordres "
        "exécutés et non exécutés ; un écart positif dit que la politique laisse passer les meilleures configurations ;"
    )
    w(
        "- **rythme** : ordres rejoués dans l'ordre chronologique avec les plafonds du livre d'ordres ; la grandeur utile "
        "est le **R net par mois**."
    )
    w("")
    w("**Adoption** : une politique qui ressort ici n'est qu'une hypothèse. Elle doit être figée dans un plan "
      "pré-enregistré (n° 4 : politique, paramètres, cellules, seuils), exécutée sur des données jamais lues pour la "
      "choisir, puis confirmée sur la période scellée 2007-2015, avant tout ADR.")  # fmt: skip
    w("")
    w("## Limites")
    w("")
    for n in rep.get("notes") or []:
        w(f"- {n}")
    w("")
    w("## Annexe — grille complète (politique × détecteur × groupe × année)")
    w("")
    w(
        "| politique | détecteur | groupe | année | config. | exécution | trades | R net CTO | t sem. | R / signal | Δ vs P0 | trades/mois |"
    )
    w("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for c in rep["cellules"]:
        w(f"| {c['politique']} | {c['detecteur']} | {c['groupe']} | {c['annee']} | {c['setups']} | "
          f"{_pct(c['taux_execution'])} | {c['trades']} | {_mk(c, c['r_net_cto'])} | {_mk(c, c['t_net_cto_semaine'], '.1f')} | "
          f"{_ws(c.get('r_par_signal'), 'moyenne')} | {_ws(c.get('delta_vs_p0'), 'moyenne')} | {_num(c['trades_par_mois'], '.2f')} |")  # fmt: skip
    return "\n".join(L) + "\n"


def _render_proposals(rep: dict[str, Any]) -> str:
    m = rep["meta"]
    L: list[str] = []
    w = L.append
    w("# Rejeu des politiques d'entrée sur les propositions enregistrées (docs/34 §34.10) — exploratoire")
    w("")
    w(f"{m['rejouees']} proposition(s) rejouée(s) sur {m['propositions']} · créées du {m['premiere_creation'] or '—'} "
      f"au {m['derniere_creation'] or '—'} · cours jusqu'au {m['fin']} · agrégats seulement, moins de "
      f"{MIN_CELL_N} observations : « n<20 »")  # fmt: skip
    if m.get("exclues"):
        w("")
        w("Écartées : " + " · ".join(f"{k} {v}" for k, v in m["exclues"].items()))
    w("")
    for k, v in rep["politiques"].items():
        w(f"- **{k}** — {v}")
    titles = {
        "tous": "1. Toutes les propositions",
        "detecteur": "2. Par détecteur",
        "position": "3. Cours au-dessus ou au-dessous de la limite à la création",
        "action": "4. Par action proposée",
    }
    for dim, title in titles.items():
        w("")
        w(f"## {title}")
        for key, v in rep[f"par_{dim}"].items():
            w("")
            w(f"### {POSITION_FR.get(key, key)}")
            w("")
            w(
                "| politique | propositions | ordres | exécution | clôturés | gagnants | R net CTO | en cours | R net latent | issues |"
            )
            w("|---|---|---|---|---|---|---|---|---|---|")
            for pol, x in v["politiques"].items():
                iss = ", ".join(f"{a} {b}" for a, b in sorted(x["issues"].items())) or "—"
                w(f"| {pol} | {x['propositions']} | {x['ordres']} | {_pct(x['taux_execution'])} | {x['clotures']} | "
                  f"{_pct(x['gagnants'])} | {_num(x['r_net_cto_moyen'])} | {x['en_cours']} | "
                  f"{_num(x['r_net_latent_moyen'])} | {iss} |")  # fmt: skip
            e = v["excursions"]
            w("")
            w("Excursions depuis le cours à la création (médianes, plus haut / plus bas) : " + " · ".join(
                f"{n} séances : {_pct(x['mfe_mediane'])} / {_pct(x['mae_mediane'])} (n {x['n']})" for n, x in e.items()))  # fmt: skip
    w("")
    w("## Limites")
    w("")
    for n in rep.get("notes") or []:
        w(f"- {n}")
    return "\n".join(L) + "\n"


def write(rep: dict[str, Any], out_dir: Path) -> tuple[Path, Path]:
    from app.services.research import _json_default

    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    j, md = out_dir / f"entry_replay_{stamp}.json", out_dir / f"entry_replay_{stamp}.md"
    j.write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=_json_default), encoding="utf-8")
    md.write_text(render_fr(rep), encoding="utf-8")
    return j, md
