# ruff: noqa: E501
"""Registration n° 4 (docs/34 §34.11): entry policies replayed on the same signals, paired per signal. Pure.

Signals are setups known at the close of ``t`` (production detectors with their zones, R1 stop and exploitable
targets; letters with an explicit trigger; t00 / t14). Each policy turns a setup into one order working from the
open of ``t + 1`` (P0 limit at the production maximum price, P1 E_open(1), P2 buy stop-limit on the confirmation,
P3 limit with R4 on the farthest target, P4 shallow pullback limit), filled and exited by the vectorised engine
(``exits.simulate``). Each signal and policy gives one value: the trade's net R on its exit's slipped initial stop
(``r_stop``), **0** when the policy placed no order, never filled or was cancelled, NaN when the outcome is unknown
(the pair is then dropped). The tested quantity is Δ = value(policy) − value(baseline), per signal (what the book
earns), its weekly series, Newey–West t and block-bootstrap interval; the placebo X1 (the same chain on random
days) measures the mechanical part of Δ. Aggregates only leave this module (no price, no instrument).
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import numpy as np

from app.domain.admission import Setup as SetupKind
from app.domain.costs import CostParams, round_trip_fee_fraction
from app.domain.detectors import DETECTORS, DetectorContext, Setup, _stop, _targets
from app.domain.indicators import Bar, compute_daily_features
from app.domain.letter_levels import canonical_levels
from app.domain.research import stats as S
from app.domain.research import triggers as T
from app.domain.research.classify import Tier, a2_met
from app.domain.research.entries import ExitModel, exit_model, lockout
from app.domain.research.exits import CANCELLED, CLOSED, INVALID, UNFILLED, ExitSpec, Orders, simulate
from app.domain.research.f0 import COLUMN_CHUNK, _pack_cols
from app.domain.research.panel import F64, I64, Bool, Panel
from app.domain.research.pipeline import (
    GroupData,
    Settings,
    Signals,
    Trades,
    _f,
    _mean,
    _summary,
    _window,
    concentration,
    earnings_coverage,
    prepare_group,
    raw_trigger,
    replay,
    signal_rows,
    trade_rows,
)
from app.domain.research.prereg import SETUP_TRIGGERS, Cell, Prereg
from app.domain.timeutil import PARIS
from app.domain.zones import detect_zones

Progress = Callable[[str], None]
DETECTOR_OF = {"d7x": "d7_trend_quality", "d2x": "d2_breakout", "d8x": "d8_buzz_precoce"}
POLICY_FR = {
    "P0": "production : limite au prix maximal (R4 sur l'objectif 1)",
    "P1": "E_open(1) : limite clôture + 1 ATR, validité 1",
    "P2": "stop d'achat à plage sur le franchissement confirmé",
    "P3": "limite au prix maximal, R4 sur l'objectif le plus lointain",
    "P4": "repli léger : limite clôture − 0,5 ATR (ou MM20)",
    "CF": "contrefactuel : acheté à l'ouverture suivante, même sortie",
}
NO_TRADE = (UNFILLED, CANCELLED, INVALID)  # no order, never filled, cancelled, or a fill outside its frame: 0 R
HEADLINE_NONE = "AUCUNE POLITIQUE D'ENTRÉE RETENUE"
WINDOW_BARS = 280  # bars handed to a detector (as F0 g/h)


def tick_size(price: F64) -> F64:
    """Approximate quotation step: the power of ten nearest under 5 bp of the price (0.01 at 20–200)."""
    x = np.asarray(price, dtype=np.float64)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(x > 0, 10.0 ** np.floor(np.log10(x * 0.0005)), np.nan)


def p_max_rr_vec(target: F64, stop: F64, fee: F64, k: float) -> F64:
    """Vectorised ``admission.p_max_rr``: the price at which (target − c) / (c − stop) = k, less the fees."""
    return (target + k * stop) / (1 + k) * (1 - fee)


@dataclass(frozen=True)
class EntryParams:
    min_reward_risk: float  # admission.min_reward_risk (R4)
    expiry_sessions: int  # entry.expiry_sessions (P0, P3)
    next_open_cap_atr: float  # P1
    pullback_atr: float  # P4
    confirm_cap_atr: float  # P2 (a cell may set its own)
    letters_max_pct: float  # letters: technical maximum price above the trigger


def entry_params(prereg: Prereg, prod: Mapping[str, Any]) -> EntryParams:
    a = prereg.assumptions
    return EntryParams(
        float(prod.get("min_reward_risk", 2.0)), int(prod.get("expiry_sessions", 3)), float(a["next_open_cap_atr"]),
        float(a["pullback_atr"]), float(a["confirm_cap_atr"]), float(a.get("letters_entry_max_pct_above_trigger", 0.01)),
    )  # fmt: skip


# --- setups ---------------------------------------------------------------------------------------------


def letter_config(levels: Mapping[str, Any]) -> tuple[float, float, tuple[float, ...]] | None:
    """(trigger, stop, targets ascending) of a letter's levels read under the closed keys of
    ``letter_levels.canonical_levels``; None unless a trigger, a stop under it and a target are all stated."""
    nums = {str(k): float(v) for k, v in levels.items() if isinstance(v, int | float) and not isinstance(v, bool)}
    lv, _ = canonical_levels(nums)
    trig, stop = lv.get("declencheur"), lv.get("stop")
    targets = tuple(sorted(v for k, v in lv.items() if k.startswith("objectif") and v > 0))
    if trig is None or stop is None or not targets or not (0 < stop < trig):
        return None
    return trig, stop, targets


def letter_known_day(received_at: datetime, after_paris: str = "17:30") -> date:
    """The first day at whose close a letter is known: its Paris date, or the next day when it arrived at or
    after ``after_paris`` (Paris time). A naive timestamp is UTC."""
    ts = received_at if received_at.tzinfo is not None else received_at.replace(tzinfo=UTC)
    local = ts.astimezone(PARIS)
    hh, mm = (int(x) for x in after_paris.split(":"))
    return local.date() + timedelta(days=1) if (local.hour, local.minute) >= (hh, mm) else local.date()


@dataclass(frozen=True)
class SetupTable:
    """Levels of each signal (t, col) known at the close of t; NaN when the trigger has no setup (t14)."""

    ts: I64
    cols: I64
    trigger: F64  # the detector's (or letter's) entry level
    p_max: F64  # technical maximum entry price
    stop: F64  # structural stop (R1) or the letter's invalidation
    t1: F64  # first exploitable target
    t_far: F64  # farthest target (last target or 52-week high)
    fee: F64  # round-trip fee fraction used by the admission (CTO)

    @property
    def size(self) -> int:
        return int(self.ts.size)

    def take(self, idx: I64) -> SetupTable:
        return SetupTable(*(getattr(self, k)[idx] for k in SetupTable.__dataclass_fields__))

    @staticmethod
    def of(rows: Sequence[tuple[float, ...]], ts: Sequence[int], cols: Sequence[int]) -> SetupTable:
        arr = np.array(rows, dtype=np.float64).reshape(-1, 6)
        return SetupTable(np.array(ts, dtype=np.int64), np.array(cols, dtype=np.int64),
                          *(np.ascontiguousarray(arr[:, k]) for k in range(6)))  # fmt: skip


def bare_setups(ts: I64, cols: I64) -> SetupTable:
    nan = np.full(ts.size, np.nan)
    return SetupTable(ts.astype(np.int64), cols.astype(np.int64), nan, nan.copy(), nan.copy(), nan.copy(), nan.copy(),
                      np.zeros(ts.size))  # fmt: skip


def d7_geometry(bars: list[Bar], ctx: DetectorContext, params: Mapping[str, Any]) -> list[Setup]:
    """The levels of ``D7TrendQuality`` (entry, maximum price, R1 stop, exploitable targets) WITHOUT its
    conditions: the structure of a random day of the placebo X1 (unit-tested equal to the detector's levels)."""
    f = compute_daily_features(bars)
    if not (f.atr14 and f.adr20) or len(bars) < 20:
        return []
    last = bars[-1]
    entry = last.high * 1.002
    p_max = last.high * (1 + float(params["entry_max_pct_above_high"]))
    lb = int(params.get("stop_lookback_low", 10))
    stop, basis, warn = _stop(entry, min(b.low for b in bars[-lb:]), f.atr14, ctx,
                              float(params["stop_max_adr_mult"]) * f.adr20 * entry)  # fmt: skip
    targets = _targets(p_max, ctx, [f.high_52w] if f.high_52w else [])
    low = min(b.low for b in bars[-lb:])
    return [Setup("d7_geometry", SetupKind.PULLBACK, entry, p_max, stop, basis, low, targets, "semaines", {}, warn)]


def detector_setups(
    g: GroupData, st: Settings, prod: Mapping[str, Any], name: str, ts: I64, cols: I64, buzz: bool = False,
    geometry: Callable[[list[Bar], DetectorContext, Mapping[str, Any]], list[Setup]] | None = None,
) -> SetupTable:  # fmt: skip
    """The production setup of detector ``name`` on each (t, col) (one ``detect`` call, zones computed once per
    day and instrument, as F0 g/h), or ``geometry`` instead of ``detect``; kept when it has an exploitable target
    and no warning (what production could turn into an order). Fees: CTO round trip at the size of the risk
    budget on the technical maximum price (as F0)."""
    p, f = g.p, g.f
    params = dict(st.detectors.get(name) or {})
    det = DETECTORS[name](params)
    zp = prod.get("zones") or {}
    wick, buf = float(prod.get("wick_buffer_pct", 0.005)), float(prod.get("stop_buffer_atr_mult", 0.25))
    out_rows: list[tuple[float, ...]] = []
    out_t: list[int] = []
    out_c: list[int] = []
    order = np.lexsort((ts, cols))
    ts, cols = ts[order], cols[order]
    ucols = np.unique(cols)
    for a0 in range(0, ucols.size, COLUMN_CHUNK):
        chunk = ucols[a0 : a0 + COLUMN_CHUNK]
        o, h, lo, c, v = (_pack_cols(x, f.pk, chunk) for x in (p.open, p.high, p.low, p.close, p.volume))
        local = {int(col): j for j, col in enumerate(chunk.tolist())}
        for i in np.nonzero(np.isin(cols, chunk))[0].tolist():
            t, col = int(ts[i]), int(cols[i])
            j, kk = local[col], int(f.pk.pos[t, col])
            if kk < 0:
                continue
            window = [Bar(float(o[r, j]), float(h[r, j]), float(lo[r, j]), float(c[r, j]), int(v[r, j]))
                      for r in range(max(0, kk - WINDOW_BARS + 1), kk + 1)]  # fmt: skip
            ff = compute_daily_features(window)
            zones = detect_zones(window[-int(zp.get("lookback_sessions", 250)) :], ff.atr14 or 0.0,
                                 int(zp.get("pivot_window", 5)), float(zp.get("merge_atr_mult", 0.5)),
                                 int(zp.get("min_touches", 2)))  # fmt: skip
            rank = f.rs_rank[t, col]
            ctx = DetectorContext(float(rank) if np.isfinite(rank) else None, zones, wick, buf, buzz=buzz)
            found = geometry(window, ctx, params) if geometry is not None else det.detect(window, ctx)
            s_ = next((x for x in found if x.targets and not x.warnings), None)
            if s_ is None or not s_.p_max_technical > s_.stop:
                continue
            cp = CostParams(st.cto.fees, ttf_rate=float(p.ttf_rate[col]), ftt_rate=float(p.ftt_rate[col]),
                            fx_fee_pct=float(p.fx_fee_pct[col]))  # fmt: skip
            notional = min(
                st.notional_cap_eur, st.risk_budget_eur / ((s_.p_max_technical - s_.stop) / s_.p_max_technical)
            )
            far = max(s_.targets[-1], ff.high_52w or -math.inf)
            out_rows.append((s_.entry_level, s_.p_max_technical, s_.stop, s_.targets[0], far,
                             round_trip_fee_fraction(notional, cp)))  # fmt: skip
            out_t.append(t)
            out_c.append(col)
        del o, h, lo, c, v
    return SetupTable.of(out_rows, out_t, out_c)


def orderable(g: GroupData) -> Bool:
    f = g.f
    with np.errstate(invalid="ignore"):
        return np.asarray(f.elig & np.isfinite(f.atr14) & (f.atr14 > 0) & np.isfinite(f.sigma20) & (f.sigma20 > 0),
                          dtype=np.bool_)  # fmt: skip


def _in_rows(m: Bool, rows: tuple[int, int]) -> Bool:
    m = m.copy()
    m[: rows[0]] = False
    m[rows[1] + 1 :] = False
    return m


def setup_prefilter(g: GroupData, trigger: str, params: Mapping[str, Any], st: Settings) -> Bool:
    """Vectorised necessary conditions of a production-setup trigger (the detector checks the rest)."""
    p, f, det = g.p, g.f, st.detectors
    if trigger == "d7x":
        return T.d7_base(p, f, det.get("d7_trend_quality") or {})
    if trigger == "d2x":
        return T.d2_base(p, f, det.get("d2_breakout") or {})
    # d8x: d8 without the radar — a session ≥ +ret_min on ≥ rvol_min × the median volume of the 50 previous
    # sessions, closing in the top ``close_top`` of its range
    rng = p.high - p.low
    with np.errstate(invalid="ignore", divide="ignore"):
        pos = np.where(rng > 0, (p.close - p.low) / rng, np.nan)
        return np.asarray(
            f.elig & (p.close / f.prev_close - 1 >= float(params.get("ret_min", 0.03)))
            & (p.volume >= float(params.get("rvol_min", 2.0)) * f.vol_prev_med50) & (f.vol_prev_med50 > 0)
            & (pos >= 1.0 - float(params.get("close_top", 1 / 3))), dtype=np.bool_,
        )  # fmt: skip


def letter_setups(g: GroupData, st: Settings, ep: EntryParams, rows: tuple[int, int], min_history: int) -> SetupTable:
    """Letter configurations with an explicit trigger, stop and target (``Panel.letters``) on the first own
    session of the value on or after the day the letter is known (``known_day``), after ``min_history`` own
    sessions; eligible and orderable that day, close above the letter's stop. Technical maximum price =
    trigger × (1 + ``letters_max_pct``); targets above it. One signal per (session, value): the earliest letter."""
    p, f = g.p, g.f
    ok = orderable(g)
    col_of = {x: k for k, x in enumerate(p.isins)}
    seen: set[tuple[int, int]] = set()
    out_rows: list[tuple[float, ...]] = []
    out_t: list[int] = []
    out_c: list[int] = []
    for lt in sorted(p.letters or (), key=lambda x: (x.known_day, x.isin)):
        col = col_of.get(lt.isin)
        if col is None:
            continue
        n = int(f.pk.n_bars[col])
        own = p.days[f.pk.order[:n, col]]
        k = int(np.searchsorted(own, np.datetime64(lt.known_day, "D"), side="left"))
        if k >= n or k < min_history:
            continue
        t = int(f.pk.order[k, col])
        if not (rows[0] <= t <= rows[1]) or not ok[t, col] or (t, col) in seen:
            continue
        close = float(p.close[t, col])
        p_max = lt.trigger * (1 + ep.letters_max_pct)
        tg = [x for x in lt.targets if x > p_max]
        if not (lt.stop < close and lt.stop < lt.trigger) or not tg:
            continue
        seen.add((t, col))
        cp = CostParams(st.cto.fees, ttf_rate=float(p.ttf_rate[col]), ftt_rate=float(p.ftt_rate[col]),
                        fx_fee_pct=float(p.fx_fee_pct[col]))  # fmt: skip
        notional = min(st.notional_cap_eur, st.risk_budget_eur / ((p_max - lt.stop) / p_max))
        hi52 = float(f.high_250[t, col]) if np.isfinite(f.high_250[t, col]) else -math.inf
        out_rows.append((lt.trigger, p_max, lt.stop, tg[0], max(tg[-1], hi52), round_trip_fee_fraction(notional, cp)))
        out_t.append(t)
        out_c.append(col)
    return SetupTable.of(out_rows, out_t, out_c)


class GroupSetups:
    """Setups of every trigger of a group on the signal rows, computed once (before any lockout)."""

    def __init__(
        self, g: GroupData, st: Settings, prod: Mapping[str, Any], ep: EntryParams, rows: tuple[int, int]
    ) -> None:
        self.g, self.st, self.prod, self.ep, self.rows = g, st, prod, ep, rows
        self._cache: dict[str, SetupTable] = {}
        self.rates: dict[str, float] = {}

    def table(self, cell: Cell) -> SetupTable:
        key = f"{cell.trigger}:{json.dumps(cell.params, sort_keys=True, default=str)}"
        if key not in self._cache:
            self._cache[key] = self._build(cell)
        return self._cache[key]

    def _build(self, cell: Cell) -> SetupTable:
        g, st, q, rows = self.g, self.st, cell.params, self.rows
        if cell.trigger in SETUP_TRIGGERS:
            m = _in_rows(setup_prefilter(g, cell.trigger, q, st) & orderable(g), rows)
            ts, cols = np.nonzero(m)
            return detector_setups(g, st, self.prod, DETECTOR_OF[cell.trigger], ts.astype(np.int64),
                                   cols.astype(np.int64), buzz=cell.trigger == "d8x")  # fmt: skip
        if cell.trigger == "t00":
            rate = q.get("rate", 0.0)
            if isinstance(rate, str):  # the rate of another trigger of the plan, in this group and window
                ref = self.table(Cell(id="_rate", tier="exploratory", family="_", trigger=rate))
                base = int(_in_rows(orderable(g), rows).sum())
                rate = ref.size / base if base else 0.0
            self.rates[cell.id] = float(rate)
            m = _in_rows(T.t00_placebo(g.f, float(rate), int(q["seed"]) * 1000 + g.gid, False) & orderable(g), rows)
            ts, cols = np.nonzero(m)
            if q.get("geometry") != "d7":
                return bare_setups(ts.astype(np.int64), cols.astype(np.int64))
            return detector_setups(g, st, self.prod, "d7_trend_quality", ts.astype(np.int64), cols.astype(np.int64),
                                   geometry=d7_geometry)  # fmt: skip
        if cell.trigger == "letters_trigger":
            return letter_setups(g, st, self.ep, rows, int(q.get("min_history_sessions", 60)))
        m = _in_rows(raw_trigger(g, cell, st)[0], rows)  # t14 (coverage floor applied by raw_trigger)
        ts, cols = np.nonzero(m)
        return bare_setups(ts.astype(np.int64), cols.astype(np.int64))

    def signals(self, cell: Cell) -> SetupTable:
        """The cell's signals: its trigger's setups under the fixed lockout of ``h`` own sessions per value."""
        tab = self.table(cell)
        if tab.size == 0:
            return tab
        m = np.zeros(self.g.p.shape, dtype=np.bool_)
        m[tab.ts, tab.cols] = True
        ts, cols = lockout(m, self.g.f.pk, self.rows[0], self.rows[1], cell.h)
        width = self.g.p.shape[1]
        where = {
            int(t) * width + int(c): k for k, (t, c) in enumerate(zip(tab.ts.tolist(), tab.cols.tolist(), strict=True))
        }
        return tab.take(np.array([where[int(t) * width + int(c)] for t, c in zip(ts.tolist(), cols.tolist(), strict=True)],
                                 dtype=np.int64))  # fmt: skip


# --- orders and arms -----------------------------------------------------------------------------------------


def exit_of(cell: Cell) -> ExitModel:
    """X8 (the bench's 2.5 ATR stop, no target) and X3 with a time exit at ``h``; XP = production (absolute R1
    stop and first target, time exit at ``h``, a fill outside stop_exec < entry < target is no trade)."""
    if cell.exit == "XP":
        return ExitModel("XP", ExitSpec("XP", cell.h, bracket_at_fill=True), "atr", 0.0, cell.h)
    return exit_model(cell.exit, cell.h, max_hold=cell.h)


def policy_orders(policy: str, g: GroupData, su: SetupTable, cell: Cell, ep: EntryParams) -> Orders:
    """The orders of ``policy`` for the signals ``su`` (NaN limit: no order), with the stop and target of the
    cell's exit. A setup invalidates the order when the session opens at or under its structural stop; without
    setup (t14) the invalidation is the exit's stop applied to the order's limit (as ``make_orders``)."""
    p, f = g.p, g.f
    ts, cols, n = su.ts, su.cols, su.size
    close, high, atr, mm20 = p.close[ts, cols], p.high[ts, cols], f.atr14[ts, cols], f.sma20[ts, cols]
    has_setup = np.isfinite(su.stop)
    level: F64 | None = None
    validity = int(cell.validity)
    with np.errstate(invalid="ignore"):
        if policy in ("P0", "P3"):
            tgt = su.t1 if policy == "P0" else su.t_far
            lim = np.minimum(p_max_rr_vec(tgt, su.stop, su.fee, ep.min_reward_risk), su.p_max)
            lim = np.where(lim > su.stop, lim, np.nan)
            validity = ep.expiry_sessions
        elif policy == "P1":
            lim, validity = close + ep.next_open_cap_atr * atr, 1
        elif policy == "P4":
            lim = close - ep.pullback_atr * atr
            lim = np.where((lim < mm20) & (mm20 < close), mm20, lim)
            lim = np.where(~has_setup | (lim > su.stop), lim, np.nan)
        elif policy == "P2":
            base = np.where(np.isfinite(su.trigger), np.fmax(su.trigger, high), high)
            lv = base + tick_size(base)
            cap = float(cell.confirm_cap_atr if cell.confirm_cap_atr is not None else ep.confirm_cap_atr)
            no = has_setup & ~(lv < su.t1)  # a trigger at or above the first target: no order
            level = np.where(no, np.nan, lv)
            lim = np.where(no, np.nan, lv * (1 + cap * atr / close))
        elif policy == "CF":
            lim, validity = np.full(n, np.inf), 1
        else:
            raise ValueError(f"politique inconnue : {policy}")
    model = exit_of(cell)
    nan, zero, one = np.full(n, np.nan), np.zeros(n), np.ones(n)
    if cell.exit == "XP":
        s_mult, s_off, t_mult, t_off = zero, -su.stop, zero.copy(), su.t1.copy()
    elif model.stop_kind == "atr":  # X8
        s_mult, s_off, t_mult, t_off = one, model.stop_k * atr, zero, nan
    else:  # X3: 3 σ20 √h under the fill
        s_mult, s_off, t_mult, t_off = (
            np.exp(-model.stop_k * f.sigma20[ts, cols] * math.sqrt(model.h_ref)),
            zero,
            zero.copy(),
            nan,
        )
    cancel = np.where(
        has_setup, su.stop, np.where(np.isfinite(lim), lim, np.nan) * s_mult if model.stop_kind == "sigma" else nan
    )
    if policy == "CF":
        cancel = nan.copy()
    return Orders(cols, f.pk.pos[ts, cols], lim.astype(np.float64), cancel.astype(np.float64), s_mult, s_off, t_mult,
                  t_off, nan.copy(), validity, level)  # fmt: skip


def arm(g: GroupData, cell: Cell, su: SetupTable, policy: str, st: Settings, ep: EntryParams) -> Trades:
    """One policy on the cell's signals: the harness's trade table (unit ``r_stop``) plus the value per signal
    (net R, 0 without trade, NaN unknown), the order and fill flags, the fill delay."""
    od = policy_orders(policy, g, su, cell, ep)
    model = exit_of(cell)
    sim = simulate(g.bars, od, model.spec, st.slippage)
    tr = trade_rows(g, cell, Signals(su.ts, su.cols), od, sim, model, None, st, "r_stop")
    cols = dict(tr.cols)
    status = tr["status"]
    filled = np.isin(status, (CLOSED, 3, 4, 6))
    zero = np.isin(status, NO_TRADE)
    closed = tr["closed"].astype(bool)
    pea = tr["pea_eligible"].astype(bool)
    cols["value"] = np.where(closed, tr["r"], np.where(zero, 0.0, np.nan))
    cols["value_pea"] = np.where(pea, np.where(closed, tr["r_pea"], np.where(zero, 0.0, np.nan)), np.nan)
    cols["value_stress"] = np.where(closed, tr["r_stress"], np.where(zero, 0.0, np.nan))
    cols["ordered"] = np.isfinite(od.limit)
    cols["filled"] = filled
    cols["at_open"] = sim.at_open & filled
    cols["delay"] = np.where(filled & (sim.fill_pos >= 0), sim.fill_pos - od.sig_pos, -1).astype(np.int64)
    return Trades(cols)


def arm_names(cell: Cell) -> dict[str, str]:
    """Arm label → policy: the tested policy, its baseline, and for a diagnostic table the counterfactual."""
    out: dict[str, str] = {}
    if cell.entry is not None:
        out["politique"] = cell.entry
    if cell.baseline_entry is not None:
        out["reference"] = cell.baseline_entry
    if cell.baseline_entry is None:
        out["contrefactuel"] = "CF"
    return out


def run_entry_cells(
    groups: Iterable[GroupData], cells: Sequence[Cell], st: Settings, prod: Mapping[str, Any], ep: EntryParams,
    window: tuple[date, date], end_margin: int, progress: Progress | None = None,
) -> tuple[dict[str, dict[str, Trades]], dict[str, Any]]:  # fmt: skip
    """Every cell's arms over the groups (one group in memory at a time); placebo rates and counts per group."""
    parts: dict[str, dict[str, list[Trades]]] = defaultdict(lambda: defaultdict(list))
    info: dict[str, Any] = {"taux_placebo": {}, "setups": defaultdict(dict), "couverture": {}}
    earnings = any(c.trigger in T.EARNINGS_TRIGGERS for c in cells)
    for g in groups:
        rows = signal_rows(g.p.days, window[0], window[1], end_margin)
        if rows[1] < rows[0]:
            continue
        if earnings:  # t14 (X7): the coverage floor of registration n° 3, per group
            cov = earnings_coverage(g, rows, st.min_earnings_coverage)
            g.earn_ok = bool(cov["compte_dans_le_verdict"])
            info["couverture"][g.p.group] = cov
        gs = GroupSetups(g, st, prod, ep, rows)
        for c in cells:
            if progress:
                progress(f"{g.p.group} : {c.id}")
            su = gs.signals(c)
            info["setups"][g.p.group][c.trigger] = gs.table(c).size
            if su.size == 0:
                continue
            for name, pol in arm_names(c).items():
                parts[c.id][name].append(arm(g, c, su, pol, st, ep))
        for cid, r in gs.rates.items():
            info["taux_placebo"].setdefault(cid, {})[g.p.group] = _f(r, 6)
    trades = {cid: {k: Trades.concat(v) for k, v in d.items()} for cid, d in parts.items()}
    info["setups"] = dict(info["setups"])
    return trades, info


# --- statistics ----------------------------------------------------------------------------------------------


def _weekly(week: I64, x: F64) -> tuple[I64, F64]:
    return S.weekly_series(week, x)


def _block(cell: Cell, block_weeks: int) -> int:
    return max(int(block_weeks), math.ceil(cell.h / 5))


def _median_hold(*arms: Trades) -> float:
    h = np.concatenate([a["hold"][a["closed"].astype(bool)] for a in arms if a.n]) if arms else np.zeros(0)
    return float(np.median(h)) if h.size else 0.0


def paired(a: Trades, b: Trades, cell: Cell, prereg: Prereg, seed: int, col: str = "value") -> dict[str, Any]:
    """Δ = a − b per signal (the same rows), its weekly series, Newey–West t, block interval and folds."""
    inf = prereg.inference
    d = a[col] - b[col]
    ok = np.isfinite(d)
    week, day = a["week"], a["day"]
    out: dict[str, Any] = {"n": int(ok.sum())}
    if not ok.any():
        return out
    wk, ws = _weekly(week, d)
    lag = S.nw_lag(_median_hold(a, b))
    block = _block(cell, int(inf["block_weeks"]))
    t = S.newey_west_t(ws, lag) if ws.size > 2 else math.nan
    lo, hi = S.block_bootstrap_ci(ws, block, int(inf["bootstrap_draws"]), seed)
    folds: list[dict[str, Any]] = []
    sub = Trades({"day": day})
    for x, y in prereg.segments.wf_test:
        m = _window(sub, x, y) & ok
        folds.append({"annee": x.year, "n": int(m.sum()), "delta_moyen": _f(_mean(d[m]))})
    out.update(
        semaines=int(wk.size), delta_moyen=_f(_mean(d)), delta_moyen_hebdo=_f(_mean(ws)), t_newey_west=_f(t, 3),
        ic=[_f(lo), _f(hi)], p_unilateral=_f(S.one_sided_p(t if math.isfinite(t) else None), 6),
        plis=folds, plis_positifs=int(sum(1 for x in folds if (x["delta_moyen"] or 0) > 0)),
        pli_min_n=int(min(x["n"] for x in folds)) if folds else 0,
        inference={"lag_newey_west_semaines": lag, "bloc_bootstrap_semaines": block},
    )  # fmt: skip
    se = S.newey_west_se(ws, lag) if ws.size > 2 else math.nan
    out["_se"], out["_w"], out["_lag"] = se, (wk, ws), lag
    return out


def aligned_excess(x: tuple[I64, F64], y: tuple[I64, F64], block: int, draws: int, seed: int) -> dict[str, Any]:
    """Week-aligned difference of two weekly series (weeks with both): mean and block-bootstrap interval."""
    common, ia, ib = np.intersect1d(x[0], y[0], return_indices=True)
    d = x[1][ia] - y[1][ib]
    if d.size == 0:
        return {"semaines_communes": 0}
    lo, hi = S.block_bootstrap_ci(d, block, draws, seed)
    return {"semaines_communes": int(common.size), "exces_moyen": _f(float(d.mean())), "ic": [_f(lo), _f(hi)]}


def arm_summary(tr: Trades, prereg: Prereg) -> dict[str, Any]:
    """Orders, fills and the closed trades of one arm (absolute net R, unit r_stop)."""
    n = tr.n
    if n == 0:
        return {"signaux": 0}
    filled = tr["filled"].astype(bool)
    cl = tr.closed
    oos = np.zeros(cl.n, dtype=bool)
    for x, y in prereg.segments.wf_test:
        oos |= _window(cl, x, y)
    out: dict[str, Any] = {
        "signaux": n, "ordres": int(tr["ordered"].sum()), "executes": int(filled.sum()),
        "taux_execution": _f(float(filled.mean()), 4),
        "part_a_l_ouverture": _f(float(tr["at_open"][filled].mean()), 4) if filled.any() else None,
        "delai_moyen_seances": _f(float(tr["delay"][filled].mean()), 2) if filled.any() else None,
        "tous": _summary(cl), "oos": _summary(cl.select(oos)),
        "mean_r_stress": _f(_mean(cl["r_stress"])) if cl.n else None,
        "cout_moyen_r": _f(_mean(cl["r_gross"] - cl["r"])) if cl.n else None,
        "duree_mediane": _f(float(np.median(cl["hold"])), 1) if cl.n else None,
    }  # fmt: skip
    if cl.n:
        out["concentration"] = concentration(cl)
    return out


def adverse_selection(tr: Trades, cf: Trades) -> dict[str, Any]:
    """The counterfactual (bought at the next open, same exit) of the signals the policy filled against those it
    did not (conditioned on their future: never a missed gain), with each group's n and the mean over all."""
    v = cf["value"]
    f = tr["filled"].astype(bool)
    groups = {"executes": f, "non_executes": ~f, "tous_signaux": np.ones(tr.n, dtype=bool)}
    return {k: {"n": int(np.isfinite(v[m]).sum()), "r_contrefactuel": _f(_mean(v[m]))} for k, m in groups.items()}


def power(se: float, z: float, edges: Sequence[float]) -> dict[str, float | None]:
    """Analytic power of a planted Δ per signal (it shifts every weekly mean by the same amount) at the critical
    t of the test that decides — an upper bound (folds, fill rate and A1 are not part of it)."""
    if not (math.isfinite(se) and se > 0 and math.isfinite(z)):
        return {f"{e:+.2f}": None for e in edges}
    return {f"{e:+.2f}": _f(S.norm_cdf(float(e) / se - z), 3) for e in edges}


def entry_criteria(c: Mapping[str, Any], th: Mapping[str, Any], confirmatory: bool) -> dict[str, bool]:
    """Verdict (a) of a paired cell: A1 on the pairs, the paired thresholds and the multiple-testing bar."""
    pa, a1, a3 = th["paired"], th["a1"], th["a3"]

    def ge(x: Any, b: float) -> bool:
        return x is not None and float(x) >= b

    ic = c.get("ic") or [None, None]
    out = {
        "A1": ge(c.get("n"), a1["event_min_n"]) and ge(c.get("semaines"), a1["event_min_weeks"]) and ge(c.get("pli_min_n"), a1["fold_min_trades"]),
        "delta": ge(c.get("delta_moyen"), float(pa["min_delta_r_per_signal"])),
        "t": ge(c.get("t_newey_west"), float(pa["min_t_newey_west_weekly"])),
        "ic": ic[0] is not None and float(ic[0]) > float(pa["min_ci_low"]),
        "plis": ge(c.get("plis_positifs"), float(pa["min_positive_folds"])),
        "execution": ge(((c.get("bras") or {}).get("politique") or {}).get("taux_execution"), float(pa["min_fill_rate"])),
    }  # fmt: skip
    p = c.get("p_holm") if confirmatory else c.get("p_romano_wolf")
    out["tests_multiples"] = p is not None and float(p) <= float(a3["holm_alpha"] if confirmatory else a3["rw_alpha"])
    return out


def entry_unseal_verdict(
    sealed: Mapping[str, Any], discovery: Mapping[str, Any], th: Mapping[str, Any]
) -> dict[str, Any]:
    """The single opening of the old replication for a paired candidate: (a) Δ > 0 there (the sign of the
    discovery) with an interval low above ``replication_min_ci_low``; (b) also needs the production-exit cell's
    tested arm to meet A2 in full there, and its discovery (b) criteria."""
    d_s, d_d = sealed.get("delta_moyen"), discovery.get("mean_excess")
    ic = sealed.get("ic") or [None, None]
    a_ok = bool(d_s is not None and d_d is not None and float(d_s) > 0 and float(d_d) > 0 and ic[0] is not None
                and float(ic[0]) > float(th["paired"]["replication_min_ci_low"]))  # fmt: skip
    arm_all = (((sealed.get("bras") or {}).get("politique") or {}).get("tous")) or {}
    b_disc = discovery.get("decision_b") or {}
    b_ok = bool(a_ok and b_disc.get("applicable") and b_disc.get("toutes") and a2_met(dict(arm_all), dict(th)))
    text = "non confirmé"
    if a_ok:
        text = "confirmé (a) — ADR « mécanique d'entrée »"
        if b_ok:
            text += " ; (b) aussi — ADR « détecteur actionnable » à 50 % du risque par trade, suivi en ombre d'abord"
    return {"n": sealed.get("n"), "delta_moyen": d_s, "ic": list(ic), "decouverte_delta_moyen": d_d,
            "a2_bras_politique": arm_all, "verdict_a": a_ok, "verdict_b": b_ok, "verdict": text}  # fmt: skip


def _placebo_of(cell: Cell, cells: Sequence[Cell]) -> Cell | None:
    """The placebo cell (t00) with the same policies, exit, horizon, validity and cap."""
    for p in cells:
        if p.trigger == "t00" and p is not cell and (p.entry, p.baseline_entry, p.exit, p.h, p.validity, p.confirm_cap_atr) == (
            cell.entry, cell.baseline_entry, cell.exit, cell.h, cell.validity, cell.confirm_cap_atr
        ):  # fmt: skip
            return p
    return None


def _role(c: Cell) -> str:
    if c.trigger == "t00":
        return "placebo (lu en premier)"
    if c.tier == "confirmatory":
        return "confirmatoire"
    if c.trigger == "t14":
        return "diagnostic, lu après le verdict du n° 3"
    return "diagnostic" if c.diagnostic else "exploratoire"


def reading_order(cells: Sequence[Cell]) -> list[str]:
    def rank(c: Cell) -> int:
        if c.trigger == "t00":
            return 0
        if c.tier == "confirmatory":
            return 1
        if not c.diagnostic:
            return 2
        return 4 if c.trigger == "t14" else 3

    return [c.id for c in sorted(cells, key=lambda c: (rank(c), c.id))]


def _drop_private(stats: Mapping[str, dict[str, Any]]) -> None:
    for s in stats.values():
        for k in ("_se", "_w", "_lag"):
            s.pop(k, None)


def run_entry_registration(
    panels: Sequence[Panel], prereg: Prereg, st: Settings, prod: Mapping[str, Any], stages: str = "all",
    holdout_cells: Sequence[str] | None = None, progress: Progress | None = None,
) -> dict[str, Any]:  # fmt: skip
    """The registration of an entry-policy plan: discovery (every cell, tests, classification, stage 2) or the
    single unseal of the old replication for the frozen candidates (their cells only)."""
    import dataclasses

    seg, th, inf = prereg.segments, prereg.thresholds, prereg.inference
    st = dataclasses.replace(st, seal_start=seg.holdout_start)
    ep = entry_params(prereg, prod)
    holdout = holdout_cells is not None
    window = prereg.sealed_window if holdout else seg.discovery
    cells = [c for c in prereg.cells if not holdout or c.id in set(holdout_cells or ())]
    groups = (prepare_group(k, p, st) for k, p in enumerate(panels) if p.shape[1])
    trades, info = run_entry_cells(groups, cells, st, prod, ep, window, seg.end_margin_sessions, progress)
    stats: dict[str, dict[str, Any]] = {}
    for c in cells:
        arms = trades.get(c.id, {})
        s: dict[str, Any] = {"id": c.id, "famille": c.family, "declencheur": c.trigger, "h": c.h, "sortie": c.exit,
                             "unite": c.unit, "tier": c.tier, "role": _role(c), "diagnostique": c.diagnostic,
                             "politique": c.entry, "politique_reference": c.baseline_entry, "validite": c.validity,
                             "plafond_confirmation_atr": c.confirm_cap_atr if c.entry == "P2" else None,
                             "comptes": list(c.accounts), "duree_max": c.h, "signaux": 0, "n": 0}  # fmt: skip
        a, b = arms.get("politique"), arms.get("reference")
        s["bras"] = {k: arm_summary(v, prereg) for k, v in arms.items() if k != "contrefactuel"}
        if a is not None and a.n:
            s["signaux"] = a.n
            s["mean_r"] = (s["bras"]["politique"].get("tous") or {}).get("mean_r")
        if a is not None and b is not None and a.n:
            s.update(paired(a, b, c, prereg, prereg.seed))
            s["mean_excess"] = s.get("delta_moyen")  # the tested quantity under the report's generic key
            s["t_week"] = s.get("t_newey_west")
            pea = paired(a, b, c, prereg, prereg.seed + 1, "value_pea")
            stress = paired(a, b, c, prereg, prereg.seed + 2, "value_stress")
            s["delta_pea"] = {"n": pea.get("n"), "delta_moyen": pea.get("delta_moyen"), "ic": pea.get("ic")}
            s["delta_couts_stresses"] = {"n": stress.get("n"), "delta_moyen": stress.get("delta_moyen")}
        elif a is not None and a.n and "contrefactuel" in arms:  # diagnostic table of one policy
            s["n"] = a.n
            s["selection_adverse"] = adverse_selection(a, arms["contrefactuel"])
        stats[c.id] = s
    out: dict[str, Any] = {"segment": "scelle" if holdout else "decouverte", "cellules": stats,
                           "lignes_portefeuille": int(th["a13"]["slots"]), "avertissements": [],
                           "taux_placebo": info["taux_placebo"], "setups_par_groupe": info["setups"]}  # fmt: skip
    if info["couverture"]:
        out["couverture_resultats"] = info["couverture"]
    if holdout:
        _drop_private(stats)
        out["politiques_entree"] = {"ordre_de_lecture": reading_order(cells)}
        return out
    tested = [c for c in cells if not c.diagnostic and c.entry and c.baseline_entry]
    conf = [c for c in tested if c.tier == "confirmatory"]
    expl = [c for c in tested if c.tier != "confirmatory"]
    pc = np.array(
        [stats[c.id].get("p_unilateral") if stats[c.id].get("p_unilateral") is not None else 1.0 for c in conf]
    )
    for c, ph in zip(conf, S.holm(pc), strict=True):
        stats[c.id]["p_holm"] = _f(float(ph), 6)
    series = [stats[c.id].get("_w") or (np.zeros(0, dtype=np.int64), np.zeros(0)) for c in expl]
    if expl:
        weeks = np.unique(np.concatenate([w for w, _ in series]))
        mat = np.full((weeks.size, len(expl)), np.nan)
        for j, (w, v) in enumerate(series):
            mat[np.searchsorted(weeks, w), j] = v
        lags = np.array([int(stats[c.id].get("_lag") or 2) for c in expl], dtype=np.int64)
        block = max(_block(c, int(inf["block_weeks"])) for c in expl)
        rw = S.romano_wolf_full(mat, block, int(inf["bootstrap_draws"]), prereg.seed, lags)
        out["romano_wolf"] = {
            "bloc_semaines": rw.block,
            "t_max_critique_5pct": _f(rw.max_t_crit, 3),
            "cellules": len(expl),
        }
        for j, c in enumerate(expl):
            stats[c.id]["p_romano_wolf"] = _f(float(rw.p_adj[j]), 6)
        z_rw = max(float(th["a3"]["min_t"]), rw.max_t_crit if math.isfinite(rw.max_t_crit) else math.inf)
    else:
        z_rw = float(th["a3"]["min_t"])
    allp = np.array(
        [stats[c.id].get("p_unilateral") if stats[c.id].get("p_unilateral") is not None else 1.0 for c in tested]
    )
    for c, q in zip(tested, S.bh(allp), strict=True):
        stats[c.id]["q_bh"] = _f(float(q), 6)
    z_holm = S.norm_ppf(1 - float(th["a3"]["holm_alpha"]) / max(1, len(conf)))
    pa_min_t = float(th["paired"]["min_t_newey_west_weekly"])
    for c in tested:
        s = stats[c.id]
        z = max(z_holm if c.tier == "confirmatory" else z_rw, pa_min_t)
        se = float(s.get("_se") or math.nan)
        s["puissance"] = power(se, z, inf["power_edges"])
        s["puissance_decision"] = s["puissance"].get(f"{float(inf['power_decision_edge']):+.2f}")
        s["effet_minimal_detectable"] = _f((z + 0.8416) * se, 4) if math.isfinite(se) else None
        s["seuil_t_puissance"] = _f(z, 3)
    # placebo: the mechanical part of Δ (verdict (a) on the total Δ; (b) reads the tested arm's excess)
    for c in cells:
        pl = _placebo_of(c, cells) if c.trigger != "t00" else None
        if pl is None or "_w" not in stats[c.id] or "_w" not in stats[pl.id]:
            continue
        blk, draws = _block(c, int(inf["block_weeks"])), int(inf["bootstrap_draws"])
        stats[c.id]["exces_sur_placebo"] = {
            "placebo": pl.id,
            **aligned_excess(stats[c.id]["_w"], stats[pl.id]["_w"], blk, draws, prereg.seed + 3),
        }
        a_sig, a_pl = trades[c.id]["politique"].closed, trades[pl.id]["politique"].closed
        if a_sig.n > 2 and a_pl.n > 2:
            stats[c.id]["exces_absolu_politique_sur_placebo"] = aligned_excess(
                _weekly(a_sig["week"], a_sig["r"]), _weekly(a_pl["week"], a_pl["r"]), blk, draws, prereg.seed + 4)  # fmt: skip
    # stage 2: 6-line replays of the confirmatory cells — tested arm, baseline, placebo of the same exit
    slots = int(th["a13"]["slots"])
    stage2: dict[str, Any] = {}
    budget = prereg.stage2_max_cells if stages in ("2", "all") else 0
    for c in conf[:budget]:
        arms = trades.get(c.id, {})
        pl = _placebo_of(c, cells)
        if "politique" not in arms:
            continue
        cand = arms["politique"].closed
        row: dict[str, Any] = {"politique": replay(cand, slots), "reference": replay(arms["reference"].closed, slots)}
        if pl is not None and pl.id in trades:
            row["placebo"] = replay(trades[pl.id]["politique"].closed, slots)
        days = arms["politique"]["day"]
        months = max(1.0, (float(days.max()) - float(days.min())) / 30.4375) if days.size else 1.0
        row["ordres_executes_par_mois"] = _f(float(arms["politique"]["filled"].sum()) / months, 2)
        row["volume_suffisant"] = (row["ordres_executes_par_mois"] or 0) >= float(
            th["paired"]["book_min_orders_per_month"]
        )
        # A13 of decision (b): the placebo takes more trades (random days spread over more values), so the drawdowns
        # are compared per unit of yearly return — return / drawdown at least the placebo's, yearly return > 0
        rod, rod_pl = (
            row["politique"].get("return_over_drawdown"),
            (row.get("placebo") or {}).get("return_over_drawdown"),
        )
        ry = row["politique"].get("r_per_year")
        row["ok"] = bool(rod is not None and rod_pl is not None and ry is not None and ry > 0
                         and rod >= rod_pl * float(th["a13"]["max_dd_ratio_vs_benchmark"]))  # fmt: skip
        stage2[c.id] = row
    out["etape2"] = stage2
    # classification and decision (b)
    for c in cells:
        s = stats[c.id]
        if c.diagnostic or c not in tested:
            s["niveau"], s["criteres"] = Tier.DIAGNOSTIC.value, {}
            continue
        crit = entry_criteria(s, th, c.tier == "confirmatory")
        s["criteres"] = crit
        ok = all(crit.values())
        s["niveau"] = (
            Tier.CANDIDATE if ok and c.tier == "confirmatory" else Tier.WATCHLIST if ok else Tier.REJECT
        ).value
        if c.tier == "confirmatory":
            pol = (s.get("bras") or {}).get("politique") or {}
            conc = pol.get("concentration") or {}
            exa = s.get("exces_absolu_politique_sur_placebo") or {}
            ic = exa.get("ic") or [None, None]
            dec: dict[str, bool] = {
                "applicable": c.exit == "XP",
                "A2_hors_echantillon": a2_met(dict(pol.get("oos") or {}), dict(th)),
                "A4_placebo": exa.get("exces_moyen") is not None and float(exa["exces_moyen"]) >= float(th["a4"]["min_excess"])
                and ic[0] is not None and float(ic[0]) > 0,
                "A10_concentration": conc.get("part_top10_valeurs") is not None and float(conc["part_top10_valeurs"]) < float(th["a10"]["max_top10_share"])
                and (conc.get("mean_r_hors_top5_valeurs") or 0) > 0,
                "A13_portefeuille": bool((stage2.get(c.id) or {}).get("ok")),
            }  # fmt: skip
            dec["toutes"] = bool(ok and dec["applicable"] and all(v for k, v in dec.items() if k != "applicable"))
            s["decision_b"] = dec
    _drop_private(stats)
    cands = [c.id for c in conf if stats[c.id]["niveau"] == Tier.CANDIDATE.value][: int(th["candidates_max"])]
    out["candidats"] = cands
    out["surveillance"] = [c.id for c in expl if stats[c.id]["niveau"] == Tier.WATCHLIST.value]
    fams: dict[str, list[Cell]] = defaultdict(list)
    for c in cells:
        fams[c.family if c.tier != "confirmatory" else c.id].append(c)
    verdicts: dict[str, str] = {}
    for fam, cs in fams.items():
        levels = {stats[c.id]["niveau"] for c in cs}
        if all(c.diagnostic for c in cs):
            verdicts[fam] = "diagnostic (jamais candidat)"
        elif Tier.CANDIDATE.value in levels:
            verdicts[fam] = "candidat — à confirmer sur la réplication 2007-2015"
        elif Tier.WATCHLIST.value in levels:
            verdicts[fam] = "surveillance (exploratoire)"
        else:
            pw = max((stats[c.id].get("puissance_decision") or 0.0) for c in cs)
            verdicts[fam] = (
                "pas d'avantage détecté"
                if pw >= float(th["power_min"])
                else "test non concluant (puissance insuffisante)"
            )
    out["verdicts_familles"] = verdicts
    out["titre"] = (f"{len(cands)} POLITIQUE(S) D'ENTRÉE CANDIDATE(S) — À CONFIRMER SUR LA RÉPLICATION "
                    f"{prereg.sealed_window[0].year}-{prereg.sealed_window[1].year}") if cands else HEADLINE_NONE  # fmt: skip
    out["politiques_entree"] = {"ordre_de_lecture": reading_order(cells), "libelles_politiques": POLICY_FR}
    out["comptes_cellules"] = {"confirmatoires": len(conf), "exploratoires": len(expl),
                               "diagnostics": len(cells) - len(tested), "etape2": len(stage2)}  # fmt: skip
    out["comptes_cellules"]["total_execute"] = sum(out["comptes_cellules"].values())
    return out
