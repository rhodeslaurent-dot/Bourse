# ruff: noqa: E501
"""Registration pipeline (docs/31 §3): panels → signals → simulated trades → statistics. Pure, numpy only.

Per venue group: features, then for every entry/exit combination of the plan the **whole eligible panel** is
simulated once (the matched controls and the equal-weighted benchmark come from it), then each cell's
signals (trigger ∧ gate, fixed lockout) are looked up or simulated. A cell's table has one row per signal
(filled or not) so that the monthly top-N selection and the adverse-selection table never use the outcome.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any

import numpy as np
import numpy.typing as npt

from app.domain.backtest import ReplayTrade, portfolio_replay
from app.domain.research import stats as S
from app.domain.research import triggers as T
from app.domain.research.costs import (
    Account,
    Economics,
    LegInputs,
    breakeven_hazard_multiple,
    corwin_schultz,
    delisting_adjustment,
    economics,
    monthly_half_spread,
)
from app.domain.research.entries import (
    ExitModel,
    calendar_of,
    control_mean,
    ew_mean,
    exit_model,
    forward_excess,
    lockout,
    make_orders,
    unit_fraction,
)
from app.domain.research.exits import (
    CLOSED,
    DELISTED,
    DIVIDEND_SUSPECT,
    GAP_STOP,
    SLIPPED_EXITS,
    STOP,
    TRAIL,
    Bars,
    Orders,
    PathRule,
    SimResult,
    bars_from,
    simulate,
)
from app.domain.research.insider_triggers import INSIDER_TRIGGERS, InsiderData, t17_insider
from app.domain.research.panel import (
    F64,
    I8,
    I64,
    Bool,
    FeatureParams,
    Features,
    Packing,
    Panel,
    adj_factor_events,
    adjustment_factor,
    build_features,
    make_packing,
    mass_jump_days,
    pack,
    recent_any,
    rescale_vintage_boundaries,
    unpack,
    unpack_bool,
    valid_bars,
)
from app.domain.research.prereg import Cell, Prereg

Arr = npt.NDArray[Any]
ComboKey = tuple[str, float, int, int, int | None, str | None]  # exit, cap ATR, validity, h, max hold, D2x cap
FWD_H = (1, 3, 5, 20, 60)
# the venue groups of params.yaml (universe.markets_p0/p1/p2), then the one-venue groups of registration n° 2
VENUE_GROUPS = ("P0", "P1", "P2", "US", "LSE", "SW")


def venue_code(group: str) -> int:
    """Index of a panel's venue group in ``VENUE_GROUPS`` (−1 when unknown) — never the enumeration index of
    the panels, which shifts when a group has no instrument."""
    return VENUE_GROUPS.index(group) if group in VENUE_GROUPS else -1


@dataclass(frozen=True)
class Settings:
    features: FeatureParams
    slippage: float  # risk.slippage_pct
    risk_budget_eur: float  # capital_pilote_eur × max_risk_per_trade_pct
    notional_cap_eur: float  # capital_pilote_eur × max_position_pct_of_capital
    fx_haircut: float
    cto: Account
    pea: Account
    srd: Account
    capacity_pct: float
    ca_jump: float
    div_min: float
    hazard_small: float
    hazard_large: float
    takeover_jump: float
    cost_stress: float
    slip_stress: float
    top_n: int
    slots: int
    seed: int
    detectors: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    universe_label: str = "in_universe"
    max_dividend_yield: float = 0.12  # trailing implied yield above which a dividend is not trusted
    ca_mask_sessions: int = 252  # own sessions after a corporate action without signal (= segments.warmup_sessions)
    dividend_window_sessions: int = 252  # trailing window of the implied dividend yield
    mass_jump_share: float = 0.20  # safety net: share of a group's instruments with a factor jump on one day
    min_turnover_cto_eur: float | None = None  # universe.min_adv_eur_cto: robustness split (plan §1.1)
    seal_start: date | None = None  # segments.holdout_start: no data-quality exclusion crosses it
    min_earnings_coverage: float | None = None  # thresholds.min_earnings_coverage (registration n° 3)
    insiders: InsiderData | None = None  # registration n° 5: AMF declarations and the plan's insider_* rules


# --- group data ------------------------------------------------------------------------------------


@dataclass
class GroupData:
    gid: int
    p: Panel
    f: Features
    bars: Bars
    hs: F64  # half-spread (T, N)
    week: I64  # (T,) Monday-based week number
    month: I64
    year: I64
    suspect_dividend_years: int = 0  # instrument-years with a rejected dividend (their trades excluded)
    div_unreliable: Bool | None = None  # (T, N) rows of those instrument-years
    quality: dict[str, int] = field(default_factory=dict)  # data-quality counts for the report
    _fwd: dict[int, F64] = field(default_factory=dict)
    earn_ok: bool = True  # registration n° 3: earnings coverage of the signal window ≥ the plan's floor

    def fwd(self, h: int) -> F64:
        if h not in self._fwd:
            self._fwd[h] = forward_excess(self.p, self.f, h)
        return self._fwd[h]


def day_ids(days: npt.NDArray[np.datetime64]) -> tuple[I64, I64, I64]:
    d = days.astype("datetime64[D]").astype(np.int64)
    week = (d + 3) // 7  # 1970-01-01 was a Thursday: weeks start on Monday
    month = days.astype("datetime64[M]").astype(np.int64)
    year = days.astype("datetime64[Y]").astype(np.int64) + 1970
    return week, month, year


def prepare_group(gid: int, p: Panel, st: Settings) -> GroupData:
    """Vintage boundaries neutralised, corporate actions and dividends from the adjustment factor, eligibility
    masked for ``ca_mask_sessions`` own sessions after a corporate action (raw-price features span the split),
    dividends checked on a trailing implied yield, then features, bars and half-spreads."""
    p, n_vintage = rescale_vintage_boundaries(p, st.div_min, st.ca_jump)
    t_len = p.shape[0]
    pk = make_packing(valid_bars(p))
    ca, div = adj_factor_events(adjustment_factor(p, pk), p.close, pk, st.ca_jump, st.div_min)
    block = unpack_bool(recent_any(pack(ca.astype(np.float64), pk) == 1.0, st.ca_mask_sessions), pk, t_len)
    f = build_features(p, st.features, exclude=block)
    week, month, year = day_ids(p.days)
    seal_row = int(np.searchsorted(p.days, np.datetime64(st.seal_start, "D"))) if st.seal_start else None
    div, unreliable, n_years, n_rejected = sanitize_dividends(
        div, p.close, f.pk, year, st.max_dividend_yield, st.dividend_window_sessions, seal_row
    )
    mass = mass_jump_days(f.adj_factor, f.pk, st.div_min, st.mass_jump_share)
    bars = bars_from(p, f, ca, div)
    cs = unpack(corwin_schultz(bars.high, bars.low), f.pk, t_len)
    hs = monthly_half_spread(cs, p.days, st.slippage)
    quality = {
        "frontieres_millesime_neutralisees": n_vintage,
        "dividendes_rejetes": n_rejected,
        "jours_sauts_de_masse": int(mass.sum()),
        "evenements_jours_sauts_de_masse": int(((div > 0) | ca)[mass].sum()),
        "operations_sur_titres": int(ca.sum()),
    }
    return GroupData(gid, p, f, bars, hs, week, month, year, n_years, unreliable, quality)


def sanitize_dividends(
    div: F64, close: F64, pk: Packing, year: I64, max_yield: float, sessions: int, seal_row: int | None = None
) -> tuple[F64, Bool, int, int]:  # fmt: skip
    """Each dividend inferred from the adjustment factor is checked on its ex-date against the trailing
    implied yield (accepted amounts of the previous ``sessions`` own sessions + this one, ÷ mean close of
    those sessions — no look-ahead); above ``max_yield`` it is rejected (unreliable factor, e.g. Yahoo for
    Milan) and its instrument-year marked unreliable (its trades are excluded). The marking never crosses
    ``seal_row`` (first row of the sealed period): a dividend rejected in the sealed period cannot exclude a
    discovery trade of the same calendar year, nor the reverse. Returns the amounts, the unreliable mask
    (T, N), the number of unreliable instrument-years (per side of the seal) and of rejected dividends."""
    t_len, n = div.shape
    d = np.nan_to_num(pack(div, pk))
    c = pack(close, pk)
    ok = np.isfinite(c)
    cs = np.cumsum(np.where(ok, c, 0.0), axis=0)
    cn = np.cumsum(ok, axis=0).astype(np.float64)
    cs_p, cn_p = np.zeros_like(cs), np.zeros_like(cn)
    if sessions < cs.shape[0]:
        cs_p[sessions:], cn_p[sessions:] = cs[:-sessions], cn[:-sessions]
    with np.errstate(invalid="ignore", divide="ignore"):
        mean_c = (cs - cs_p) / (cn - cn_p)
    rows, cols = np.nonzero(d > 0)
    rejected = np.zeros(d.shape, dtype=bool)
    accepted: list[tuple[int, float]] = []
    last_col = -1
    for k in np.lexsort((rows, cols)).tolist():
        r, col = int(rows[k]), int(cols[k])
        if col != last_col:
            accepted, last_col = [], col
        amount = float(d[r, col])
        trailing = sum(a for pos, a in accepted if pos > r - sessions) + amount
        m = float(mean_c[r, col])
        if not (math.isfinite(m) and m > 0 and trailing / m <= max_yield):
            rejected[r, col] = True
            d[r, col] = 0.0
        else:
            accepted.append((r, amount))
    rej = unpack_bool(rejected, pk, t_len)
    unreliable = np.zeros((t_len, n), dtype=bool)
    sealed = np.arange(t_len) >= (seal_row if seal_row is not None else t_len + 1)
    bad = sorted({(int(col), int(year[t]), bool(sealed[t])) for t, col in zip(*np.nonzero(rej), strict=True)})
    for col, y, side in bad:
        unreliable[:, col] |= (year == y) & (sealed == side)
    return np.nan_to_num(unpack(d, pk, t_len)), unreliable, len(bad), int(rejected.sum())


def exclude_unreliable(g: GroupData, ts: I64, cols: I64, sim: SimResult) -> SimResult:
    """Closed trades whose signal or exit falls in an instrument-year with a rejected dividend → excluded
    (status ``DIVIDEND_SUSPECT``, counted in the report)."""
    if g.div_unreliable is None or not g.div_unreliable.any() or ts.size == 0:
        return sim
    exit_t = calendar_of(g.f.pk, cols, sim.exit_pos)
    bad = g.div_unreliable[ts, cols] | ((exit_t >= 0) & g.div_unreliable[np.maximum(exit_t, 0), cols])
    m = bad & (sim.status == CLOSED)
    if not m.any():
        return sim
    status = sim.status.copy()
    status[m] = DIVIDEND_SUSPECT
    return replace(sim, status=status)


def signal_rows(days: npt.NDArray[np.datetime64], start: date, end: date, end_margin: int) -> tuple[int, int]:
    """First and last calendar rows of signals in [start, end], and never later than the data end − margin."""
    lo = int(np.searchsorted(days, np.datetime64(start, "D"), side="left"))
    hi = int(np.searchsorted(days, np.datetime64(end, "D"), side="right")) - 1
    return lo, min(hi, days.shape[0] - 1 - end_margin)


# --- trade tables ------------------------------------------------------------------------------------


class Trades:
    """Column table of signals (one row per signal, filled or not) and their trade metrics."""

    def __init__(self, cols: dict[str, Arr]) -> None:
        self.cols = cols

    @property
    def n(self) -> int:
        return int(next(iter(self.cols.values())).shape[0]) if self.cols else 0

    def __getitem__(self, k: str) -> Arr:
        return self.cols[k]

    def __contains__(self, k: str) -> bool:
        return k in self.cols

    def select(self, m: Arr) -> Trades:
        return Trades({k: v[m] for k, v in self.cols.items()})

    @staticmethod
    def concat(parts: Sequence[Trades]) -> Trades:
        parts = [p for p in parts if p.n > 0]
        if not parts:
            return Trades({})
        keys = set(parts[0].cols)
        for p in parts[1:]:
            keys &= set(p.cols)
        return Trades({k: np.concatenate([p.cols[k] for p in parts]) for k in sorted(keys)})

    @property
    def closed(self) -> Trades:
        return self.select(self.cols["closed"]) if self.n else self


@dataclass(frozen=True)
class Signals:
    ts: I64
    cols: I64
    p_max: F64 | None = None
    score: F64 | None = None
    overlay: Bool | None = None


def raw_trigger(g: GroupData, cell: Cell, st: Settings) -> tuple[Bool, F64 | None]:
    p, f, q = g.p, g.f, cell.params
    gate = T.gate_mask(f, cell.gate)
    pmax: F64 | None = None
    if cell.trigger == "t00":
        m = T.t00_placebo(f, float(q.get("rate", 0.0)), int(q["seed"]) * 1000 + g.gid, cell.mode != "event")
    elif cell.trigger == "t01":
        m = T.t01_momentum(f, float(q["rank_min"]), month_end_only=cell.mode != "event")
    elif cell.trigger == "t03":
        m = T.t03_pth(p, f, float(q["pth_min"]), float(q["rs_min"]))
    elif cell.trigger == "c1":
        m = T.t01_momentum(f, float(q["rank_min"]), month_end_only=False) & T.t03_pth(
            p, f, float(q["pth_min"]), float(q["rs_min"])
        )
    elif cell.trigger == "t05":
        m = T.t05_reversal(f, float(q["k_sigma"]))
    elif cell.trigger == "t07":
        m = T.t07_wide_range(p, f, float(q["tr_mult"]), float(q["rvol_min"]))
    elif cell.trigger == "t08":
        m = T.t08_gap(p, f, float(q["gap_min"]), float(q.get("rvol_min", 2.0)))
    elif cell.trigger == "t09":
        m = T.t09_volume_shock(p, f, float(q.get("mult", 3.0)))
    elif cell.trigger in INSIDER_TRIGGERS:  # registration n° 5 (insider_triggers)
        m = t17_insider(p, f, st.insiders, q)
    elif cell.trigger in T.EARNINGS_TRIGGERS and not g.earn_ok:  # coverage below the plan's floor: no signal
        m = np.zeros(p.shape, dtype=np.bool_)
    elif cell.trigger == "t14":
        m = T.t14_earnings_reaction(p, f, float(q.get("k_sigma", 2.0)), float(q.get("rvol_min", 2.0)),
                                    float(q.get("close_top", 0.5)), bool(q.get("require_surprise", False)))  # fmt: skip
    elif cell.trigger == "t15":
        m = T.t15_pre_earnings(p, f, int(q.get("lead", 5)))
    elif cell.trigger == "t16":
        m = T.t16_shock_without_news(p, f, float(q.get("k_sigma", 2.0)), float(q.get("rvol_min", 2.0)),
                                     float(q.get("close_top", 0.5)))  # fmt: skip
    elif cell.trigger == "d2x":
        kind, value = str(cell.cap).split(":")
        m = T.d2_base(p, f, st.detectors.get("d2_breakout") or {})
        pmax = T.d2_cap(p, f, kind, float(value))
    else:
        raise ValueError(f"unknown trigger {cell.trigger}")
    with np.errstate(invalid="ignore"):
        orderable = np.isfinite(f.atr14) & (f.atr14 > 0) & np.isfinite(f.sigma20) & (f.sigma20 > 0)
    return m & gate & orderable, pmax


def cell_signals(g: GroupData, cell: Cell, st: Settings, rows: tuple[int, int], mask: Bool | None = None) -> Signals:
    """Signals of a cell in the rows window; ``mask`` replaces the trigger (tests, planted edges)."""
    if mask is None:
        m, pmax = raw_trigger(g, cell, st)
    else:
        m, pmax = mask & g.f.elig, None
    if cell.mode == "event":
        ts, cols = lockout(m, g.f.pk, rows[0], rows[1], cell.h)
    else:  # monthly: lockout applied at the selection (outcome-free)
        ts, cols = lockout(m, g.f.pk, rows[0], rows[1], 1)
    score = g.f.mom_rank
    if cell.trigger == "t00":  # placebo: a random monthly ranking
        score = T.placebo_score(g.f.mom_rank.shape, int(cell.params["seed"]) * 1000 + g.gid)
    return Signals(
        ts,
        cols,
        pmax[ts, cols] if pmax is not None else None,
        score[ts, cols] if cell.mode != "event" else None,
        g.f.low_vol[ts, cols] if cell.overlay == "t11" else None,
    )


def masked_signal_count(g: GroupData, cell: Cell, st: Settings, rows: tuple[int, int]) -> int:
    """Signal days of a cell in the rows window that the corporate-action mask removed (the trigger evaluated
    with the masked rows eligible again, ranked against the day's population)."""
    cm = g.f.ca_masked
    lo, hi = rows
    if hi < lo or not cm[lo : hi + 1].any():
        return 0
    g2 = replace(g, f=replace(g.f, elig=g.f.elig | cm), _fwd={})
    m, _ = raw_trigger(g2, cell, st)
    return int((m & cm)[lo : hi + 1].sum())


# --- combinations simulated on the whole eligible panel -------------------------------------------------


def combo_model(key: ComboKey) -> ExitModel:
    code, _, _, h, max_hold, _ = key
    return exit_model(code, h, max_hold)


def combo_label(key: ComboKey) -> str:
    cap = f"plafond {key[5]}" if key[5] is not None else f"cap {key[1]}"
    return f"{key[0]} {cap} validité {key[2]} h {key[3]}"


def primary_unit(key: ComboKey) -> str:
    return "r_stop" if key[0] == "X4" else "r_ref"


@dataclass
class Combo:
    key: ComboKey
    model: ExitModel
    index: I64  # (T, N) row of the flat simulation, −1
    od: Orders
    sim: SimResult
    r: F64  # (T, N) primary unit, CTO, dividends gross
    ctrl: F64  # matched control mean (same stratum, same day, instrument excluded)
    ew: F64  # (T,) equal-weighted eligible mean
    t04_mean: tuple[float, int]
    all_mean: tuple[float, int]


def legs(
    g: GroupData, ts: I64, cols: I64, sim: SimResult, model: ExitModel, fx_haircut: float, slippage: float
) -> LegInputs:  # fmt: skip
    pk = g.f.pk
    fill_t = calendar_of(pk, cols, sim.fill_pos)
    exit_t = calendar_of(pk, cols, sim.exit_pos)
    ft, et = np.maximum(fill_t, 0), np.maximum(exit_t, 0)
    fx_in, fx_out = g.p.fx_to_eur[ft, cols], g.p.fx_to_eur[et, cols]
    days = (g.p.days[et] - g.p.days[ft]).astype("timedelta64[D]").astype(np.float64)
    non_eur = g.p.fx_fee_pct[cols] > 0
    return LegInputs(
        entry=sim.entry, exit_px=sim.exit_px, stop_exec=sim.stop_exec_init, div_cash=sim.div_cash,
        fx_in=fx_in, fx_out=fx_out, ttf=g.p.ttf_rate[cols], ftt=g.p.ftt_rate[cols], fx_fee=g.p.fx_fee_pct[cols],
        haircut=np.where(non_eur, fx_haircut, 0.0), hs_in=g.hs[ft, cols], hs_out=g.hs[et, cols], days=days,
        unit_frac=unit_fraction(g.f.sigma20[ts, cols], model.h_ref),
        stop_exit=np.isin(sim.reason, (STOP, GAP_STOP, TRAIL)),
        exit_slip=np.where(np.isin(sim.reason, SLIPPED_EXITS), slippage, 0.0),
        **_partial_legs(g, cols, sim),
    )  # fmt: skip


def _partial_legs(g: GroupData, cols: I64, sim: SimResult) -> dict[str, F64]:
    """Registration n° 6: the partial sale's price, quantity, change rate and half-spread (none: no field)."""
    if sim.partial_qty is None or sim.partial_px is None or sim.partial_pos is None or not (sim.partial_qty > 0).any():
        return {}
    pt = np.maximum(calendar_of(g.f.pk, cols, sim.partial_pos), 0)
    return {"partial_px": sim.partial_px, "partial_qty": sim.partial_qty, "fx_part": g.p.fx_to_eur[pt, cols],
            "hs_part": g.hs[pt, cols]}  # fmt: skip


def unit_value(e: Economics, unit: str, gross: bool = False) -> F64:
    if unit == "r_stop":
        return e.r_stop_gross if gross else e.r_stop
    return e.r_unit_gross if gross else e.r_unit


def simulate_combo(g: GroupData, key: ComboKey, st: Settings, rows: tuple[int, int]) -> Combo:
    model = combo_model(key)
    _, cap, validity, _, _, cap_rule = key
    f = g.f
    m = f.elig.copy()
    m[: rows[0]] = False
    m[rows[1] + 1 :] = False
    with np.errstate(invalid="ignore"):
        m &= np.isfinite(f.atr14) & (f.atr14 > 0) & np.isfinite(f.sigma20) & (f.sigma20 > 0)
    ts, cols = np.nonzero(m)
    ts, cols = ts.astype(np.int64), cols.astype(np.int64)
    p_max = None
    if cap_rule is not None:  # D2x: the controls enter with the cell's own cap rule (no pivot → no order)
        kind, value = cap_rule.split(":")
        p_max = T.d2_cap(g.p, f, kind, float(value))[ts, cols]
    od = make_orders(g.p, f, ts, cols, model, validity, cap, p_max)
    sim = exclude_unreliable(g, ts, cols, simulate(g.bars, od, model.spec, st.slippage))
    lg = legs(g, ts, cols, sim, model, st.fx_haircut, st.slippage)
    e = economics(lg, st.cto, st.risk_budget_eur, st.notional_cap_eur)
    unit = primary_unit(key)
    val = np.where(sim.status == CLOSED, unit_value(e, unit), np.nan)
    shape = g.p.shape
    r = np.full(shape, np.nan)
    r[ts, cols] = val
    index = np.full(shape, -1, dtype=np.int64)
    index[ts, cols] = np.arange(ts.size)
    ctrl = control_mean(r, f.stratum)
    ew = ew_mean(r, f.elig)
    ok = np.isfinite(r)
    t04 = ok & f.t04
    return Combo(
        key, model, index, od, sim, r, ctrl, ew,
        (float(np.nansum(np.where(t04, r, 0.0))), int(t04.sum())), (float(np.nansum(r)), int(ok.sum())),
    )  # fmt: skip


def _take_sim(sim: SimResult, idx: I64) -> SimResult:
    return SimResult(*(getattr(sim, k)[idx] for k in SimResult.__dataclass_fields__))


def trade_rows(
    g: GroupData, cell: Cell, sig: Signals, od: Orders, sim: SimResult, model: ExitModel, combo: Combo | None,
    st: Settings, unit: str,
) -> Trades:  # fmt: skip
    """Full metrics of a cell's signals (NaN where not closed)."""
    ts, cols = sig.ts, sig.cols
    n = ts.size
    sim = exclude_unreliable(g, ts, cols, sim)
    closed = sim.status == CLOSED
    lg = legs(g, ts, cols, sim, model, st.fx_haircut, st.slippage)
    cto = economics(lg, st.cto, st.risk_budget_eur, st.notional_cap_eur)
    net_div = economics(lg, st.cto, st.risk_budget_eur, st.notional_cap_eur, div_rate=1 - st.cto.dividend_tax)
    no_div = economics(lg, st.cto, st.risk_budget_eur, st.notional_cap_eur, div_rate=0.0)
    stress = economics(
        lg, st.cto, st.risk_budget_eur, st.notional_cap_eur, fee_mult=st.cost_stress,
        spread_mult=st.slip_stress, extra_exit_slip=st.slippage * (st.slip_stress - 1),
    )  # fmt: skip
    delisted = closed & (sim.reason == DELISTED)
    if delisted.any():  # survivorship stress: the delisted exit loses 100 % of the notional
        dl = replace(lg, exit_px=np.where(delisted, 0.0, lg.exit_px), div_cash=np.where(delisted, 0.0, lg.div_cash))
        r_dl = unit_value(economics(dl, st.cto, st.risk_budget_eur, st.notional_cap_eur), unit)
    else:
        r_dl = unit_value(cto, unit)
    pea_ok = g.p.pea_eligible[cols]
    pea = economics(lg, st.pea, st.risk_budget_eur, st.notional_cap_eur)
    srd = economics(lg, st.srd, st.risk_budget_eur, st.notional_cap_eur)

    def cl(x: F64) -> F64:
        return np.where(closed, x, np.nan)

    r = cl(unit_value(cto, unit))
    pk = g.f.pk
    fill_t = calendar_of(pk, cols, sim.fill_pos)
    exit_t = calendar_of(pk, cols, sim.exit_pos)
    hold = np.where(closed, sim.exit_pos - sim.fill_pos + 1, 0)
    uf = lg.unit_frac if unit == "r_ref" else sim.risk_fraction
    hz = delisting_adjustment(
        cto.gross_ret, hold / 252.0, uf, g.p.small_cap[cols], st.hazard_small, st.hazard_large, st.takeover_jump
    )
    scale = 300.0 / st.risk_budget_eur if st.risk_budget_eur > 0 else 1.0
    with np.errstate(invalid="ignore"):
        cap_ok = cto.notional_frac <= st.capacity_pct * g.f.turnover_med20[ts, cols]
    ctrl = combo.ctrl[ts, cols] if combo is not None else np.full(n, np.nan)
    ew = combo.ew[ts] if combo is not None else np.full(n, np.nan)
    fwd = g.fwd(cell.h)[ts, cols]
    days = g.p.days
    return Trades(
        {
            "group": np.full(n, g.gid, dtype=np.int64),
            "venue": np.full(n, venue_code(g.p.group), dtype=np.int64),
            "inst": g.gid * 1_000_000 + cols,
            "t": ts,
            "day": days[ts].astype("datetime64[D]").astype(np.int64),
            "week": g.week[ts],
            "month": g.month[ts],
            "year": g.year[ts],
            "status": sim.status.astype(np.int64),
            "closed": closed,
            "entry_day": np.where(closed, days[np.maximum(fill_t, 0)].astype(np.int64), -1),
            "exit_day": np.where(closed, days[np.maximum(exit_t, 0)].astype(np.int64), -1),
            "hold": hold.astype(np.int64),
            "reason": sim.reason.astype(np.int64),
            "ambiguous": sim.ambiguous & closed,
            "r": r,
            "r_gross": cl(unit_value(cto, unit, gross=True)),
            "r_ref": cl(cto.r_unit),
            "r_stop": cl(cto.r_stop),
            "pct": cl(cto.net_ret),
            "eur": cl(cto.net_eur * scale),
            "r_div_net": cl(unit_value(net_div, unit)),
            "r_div_excl": cl(unit_value(no_div, unit)),
            "r_stress": cl(unit_value(stress, unit)),
            "eur_stress": cl(stress.net_eur * scale),
            "r_pea": np.where(pea_ok, cl(unit_value(pea, unit)), np.nan),
            "eur_pea": np.where(pea_ok, cl(pea.net_eur * scale), np.nan),
            "r_srd": cl(unit_value(srd, unit)),
            "ctrl": np.where(closed, ctrl, np.nan),
            "ew": np.where(closed, ew, np.nan),
            "hazard": cl(hz),
            "delisted": delisted,
            "r_delist_loss": cl(r_dl),
            "capacity_ok": cap_ok & closed,
            "small": g.p.small_cap[cols],
            "liq": g.f.liq_terc[ts, cols].astype(np.int64),
            "liq_cto": (
                np.nan_to_num(g.f.turnover_med60[ts, cols], nan=-1.0) >= st.min_turnover_cto_eur
                if st.min_turnover_cto_eur is not None
                else np.zeros(n, dtype=bool)
            ),
            "t13": g.f.t13[ts, cols],
            "in_universe": g.p.in_universe[cols],
            "pea_eligible": pea_ok,
            "fwd": fwd,
            "score": sig.score if sig.score is not None else np.full(n, np.nan),
            "overlay": sig.overlay if sig.overlay is not None else np.zeros(n, dtype=bool),
        }
    )


def cell_model(cell: Cell) -> ExitModel:
    return exit_model(cell.exit, cell.h, cell.max_hold)


def evaluate_cell(
    g: GroupData, cell: Cell, combo: Combo, st: Settings, rows: tuple[int, int], mask: Bool | None = None,
    path_rule: PathRule = "stop_first",
) -> Trades:  # fmt: skip
    """Signals of a cell, simulated (looked up in the combination when the orders are the same)."""
    sig = cell_signals(g, cell, st, rows, mask)
    model = cell_model(cell)
    if sig.ts.size == 0:
        return Trades({})
    cap = cell.entry_cap_atr
    if sig.p_max is None and path_rule == "stop_first":
        idx = combo.index[sig.ts, sig.cols]
        if (idx >= 0).all():
            od = combo.od.take(idx)
            sim = _take_sim(combo.sim, idx)
            return trade_rows(g, cell, sig, od, sim, model, combo, st, cell.unit)
    od = make_orders(g.p, g.f, sig.ts, sig.cols, model, cell.validity, cap, sig.p_max)
    sim = simulate(g.bars, od, model.spec, st.slippage, path_rule)
    return trade_rows(g, cell, sig, od, sim, model, combo, st, cell.unit)


# --- monthly selection (rank triggers) ----------------------------------------------------------------


def select_top(tr: Trades, n: int, hold_months: int, seed: int, extra: Bool | None = None) -> Trades:
    """Top ``n`` signals of each month by score (ties: score then a seeded draw), an instrument selected in
    the previous ``hold_months − 1`` months skipped (its trade is still open). Outcome-free."""
    if tr.n == 0:
        return tr
    rng = np.random.default_rng(seed)
    base = np.ones(tr.n, dtype=bool) if extra is None else extra
    tie = rng.random(tr.n)
    keep = np.zeros(tr.n, dtype=bool)
    last: dict[int, int] = {}
    month, score, inst = tr["month"], tr["score"], tr["inst"]
    for mth in np.unique(month[base]).tolist():
        idx = np.nonzero(base & (month == mth))[0]
        order = idx[np.lexsort((tie[idx], -np.nan_to_num(score[idx], nan=-1.0)))]
        taken = 0
        for k in order.tolist():
            i = int(inst[k])
            if i in last and mth - last[i] < hold_months:
                continue
            keep[k] = True
            last[i] = int(mth)
            taken += 1
            if taken >= n:
                break
    return tr.select(keep)


# --- statistics of a cell --------------------------------------------------------------------------


def _mean(x: Arr) -> float | None:
    x = x[np.isfinite(x)]
    return float(x.mean()) if x.size else None


def _f(x: float | None, nd: int = 4) -> float | None:
    if x is None or not math.isfinite(x):
        return None
    return round(float(x), nd)


def _window(tr: Trades, a: date, b: date) -> Arr:
    d = tr["day"]
    return (d >= np.datetime64(a, "D").astype(np.int64)) & (d <= np.datetime64(b, "D").astype(np.int64))


def series_of(tr: Trades, monthly: bool, value: str = "excess") -> tuple[I64, F64]:
    """Weekly (event) or monthly (rank) equal-weighted series of a column, keyed by week id."""
    v = tr[value]
    if monthly:
        u, s = S.weekly_series(tr["month"], v)
        first_week = np.array([int(tr["week"][tr["month"] == m].min()) for m in u.tolist()], dtype=np.int64)
        return first_week, s
    return S.weekly_series(tr["week"], v)


SESSIONS_PER_WEEK = 5
SESSIONS_PER_MONTH = 21


def _median_hold(tr: Trades) -> float:
    h = tr["hold"][tr["closed"].astype(bool)] if tr.n and "closed" in tr else np.zeros(0)
    return float(np.median(h)) if h.size else 0.0


def nw_lag_of(tr: Trades, cell: Cell) -> int:
    """Newey–West lag of a cell's own series: monthly cells max(2, ⌈h/21⌉) months, event cells
    max(2, ⌈median hold / 5⌉) weeks (the overlap of the holds)."""
    if cell.mode != "event":
        return max(2, math.ceil(cell.h / SESSIONS_PER_MONTH))
    return S.nw_lag(_median_hold(tr))


def weekly_lag_of(tr: Trades, cell: Cell) -> int:
    """The same lag in weeks — the rows of the cross-cell weekly matrix (a monthly cell's months there)."""
    lag = nw_lag_of(tr, cell)
    return math.ceil(lag * SESSIONS_PER_MONTH / SESSIONS_PER_WEEK) if cell.mode != "event" else lag


def max_hold_of(cell: Cell) -> int:
    return int(cell_model(cell).spec.max_hold)


def block_of(cell: Cell, block_weeks: int) -> int:
    """Bootstrap block of a cell's own series: at least the plan's ``block_weeks`` and at least the longest
    hold (⌈max hold / 5⌉ weeks; monthly series: ⌈max hold / 21⌉ months)."""
    per = SESSIONS_PER_MONTH if cell.mode != "event" else SESSIONS_PER_WEEK
    return max(int(block_weeks), math.ceil(max_hold_of(cell) / per))


def weekly_block_of(cell: Cell, block_weeks: int) -> int:
    """Block in weeks for a weekly-row matrix: ⌈max hold / 5⌉ weeks, at least ``block_weeks``."""
    return max(int(block_weeks), math.ceil(max_hold_of(cell) / SESSIONS_PER_WEEK))


def add_excess(tr: Trades, monthly: bool) -> Trades:
    if tr.n == 0:
        return tr
    cols = dict(tr.cols)
    cols["excess_ctrl"] = tr["r"] - tr["ctrl"]
    cols["excess_ew"] = tr["r"] - tr["ew"]
    cols["excess"] = cols["excess_ew"] if monthly else cols["excess_ctrl"]
    return Trades(cols)


def cell_stats(tr_all: Trades, cell: Cell, prereg: Prereg, st: Settings) -> dict[str, Any]:
    """Discovery statistics of one cell (rows = signals of the discovery window)."""
    th, inf = prereg.thresholds, prereg.inference
    monthly = cell.mode != "event"
    out: dict[str, Any] = {"id": cell.id, "famille": cell.family, "declencheur": cell.trigger, "h": cell.h,
                           "sortie": cell.exit, "unite": cell.unit, "mode": cell.mode, "signaux": tr_all.n}  # fmt: skip
    if tr_all.n == 0:
        out.update(n=0)
        return out
    status = tr_all["status"]
    out["executes"] = int((status == CLOSED).sum())
    out["non_executes"] = int((status == 0).sum())
    out["annules"] = int((status == 1).sum())
    out["issue_inconnue"] = int((status == 3).sum())
    out["operations_sur_titres"] = int((status == 4).sum())
    out["exclus_dividendes_douteux"] = int((status == DIVIDEND_SUSPECT).sum())
    out["sorties_cotation_interrompue"] = int(tr_all["delisted"].sum()) if "delisted" in tr_all else 0
    fwd = tr_all["fwd"]
    # round 2 (26/10/2026): each group with its n (masked under report_min_n), and all signals together — the
    # « not filled » group is conditioned on its future path (the low never reached the limit), so the gap between
    # the two groups is not a missed gain; the all-signals mean is the unconditioned reference
    sel = {"executes": tr_all["closed"], "non_executes": status == 0, "tous_signaux": np.isfinite(fwd)}
    out["selection_adverse"] = {
        k: {"n": int(np.isfinite(fwd[m]).sum()), "exces_futur": _f(_mean(fwd[m]))} for k, m in sel.items()
    }
    tr = add_excess(tr_all.closed, monthly)
    n = tr.n
    out["n"] = n
    if n == 0:
        return out
    r, ex = tr["r"], tr["excess"]
    out["mean_r"] = _f(_mean(r))
    out["median_r"] = _f(float(np.nanmedian(r)))
    out["hit_rate"] = _f(float((r > 0).mean()))
    out["profit_factor"] = _f(S.profit_factor(r[np.isfinite(r)]))
    out["mean_r_brut"] = _f(_mean(tr["r_gross"]))
    out["cout_moyen_r"] = _f(_mean(tr["r_gross"] - r))
    out["mean_eur_300"] = _f(_mean(tr["eur"]), 2)
    out["non_financables"] = int((~np.isfinite(tr["eur"])).sum())
    out["mean_pct_notionnel"] = _f(_mean(tr["pct"]), 5)
    out["mean_excess"] = _f(_mean(ex))
    out["mean_excess_ctrl"] = _f(_mean(tr["excess_ctrl"]))
    out["mean_excess_ew"] = _f(_mean(tr["excess_ew"]))
    out["duree_mediane"] = _f(float(np.median(tr["hold"])), 1)
    lag = nw_lag_of(tr, cell)
    block = block_of(cell, int(inf["block_weeks"]))
    unit = "mois" if monthly else "semaines"
    out["inference"] = {"lag_newey_west": lag, "bloc_bootstrap": block, "unite": unit}
    wk, ws = series_of(tr, monthly)
    out["semaines"] = int(wk.size)
    out["mois"] = int(np.unique(tr["month"]).size)
    t_week = S.newey_west_t(ws, lag) if ws.size > 2 else math.nan
    out["t_week"] = _f(t_week, 3)
    _, wr = series_of(tr, monthly, "r")
    out["t_week_r"] = _f(S.newey_west_t(wr, lag) if wr.size > 2 else math.nan, 3)
    out["design_effect"] = _f(S.design_effect(ws, lag), 3)
    out["se_grappes_mois"] = _f(S.cluster_se(ex, tr["month"]), 4)
    out["se_grappes_mois_valeur"] = _f(S.cluster_se(ex, tr["month"] * 10_000_000 + tr["inst"] % 10_000_000), 4)
    seed = prereg.seed
    lo, hi = S.block_bootstrap_ci(ws, block, int(inf["bootstrap_draws"]), seed)
    out["excess_ic"] = [_f(lo), _f(hi)]
    ce = tr["excess_ctrl"]
    _, cs = series_of(Trades({**tr.cols, "excess": ce}), monthly)
    clo, chi = S.block_bootstrap_ci(cs, block, int(inf["bootstrap_draws"]), seed + 1)
    out["excess_ctrl_ic"] = [_f(clo), _f(chi)]
    t_ctrl = S.newey_west_t(cs, lag) if cs.size > 2 else math.nan
    out["t_week_controle"] = _f(t_ctrl, 3)
    _, rs = series_of(tr, monthly, "r")
    rlo, rhi = S.block_bootstrap_ci(rs, block, int(inf["bootstrap_draws"]), seed + 2)
    out["r_ic"] = [_f(rlo), _f(rhi)]
    out["p_unilateral"] = _f(S.one_sided_p(t_week if math.isfinite(t_week) else None), 6)
    sr, sk, ku = S.moments(ws)
    out["_sr"], out["_skew"], out["_kurt"], out["_nobs"] = sr, sk, ku, int(ws.size)
    # stage-0 gate: excess over the matched control in every mode (monthly cells too, not the EW benchmark)
    out["gate"] = bool(
        math.isfinite(t_ctrl) and t_ctrl >= float(th["gate_t_week"]) and (_mean(tr["excess_ctrl"]) or 0) > 0
    )
    # walk-forward (test years) and folds
    folds: list[dict[str, Any]] = []
    oos = np.zeros(n, dtype=bool)
    for a, b in prereg.segments.wf_test:
        w = _window(tr, a, b)
        oos |= w
        folds.append({"annee": a.year, "n": int(w.sum()), "mean_r": _f(_mean(r[w])), "mean_excess": _f(_mean(ex[w]))})
    out["plis"] = folds
    out["plis_positifs"] = int(sum(1 for x in folds if (x["mean_r"] or 0) > 0))
    out["pli_min_n"] = int(min(x["n"] for x in folds)) if folds else 0
    o = tr.select(oos)
    out["oos"] = _summary(o)
    # costs and accounts
    out["mean_r_stress"] = _f(_mean(tr["r_stress"]))
    cost = float(np.nanmean(tr["r_gross"] - r))
    out["multiplicateur_couts_equilibre"] = _f(float(np.nanmean(tr["r_gross"])) / cost if cost > 0 else math.inf, 2)
    pea = tr["r_pea"]
    if "pea" in cell.accounts or cell.h >= 20:
        out["pea"] = {"n": int(np.isfinite(pea).sum()), "mean_r": _f(_mean(pea)),
                      "mean_eur_300": _f(_mean(tr["eur_pea"]), 2),
                      "oos": _summary(Trades({**o.cols, "r": o["r_pea"], "eur": o["eur_pea"]}))}  # fmt: skip
    if cell.h <= 10:
        out["srd"] = {"mean_r": _f(_mean(tr["r_srd"]))}
    # dividends
    signs = [np.sign(_mean(tr[k]) or 0.0) for k in ("r", "r_div_net", "r_div_excl")]
    out["dividendes"] = {"brut": out["mean_r"], "net": _f(_mean(tr["r_div_net"])),
                         "exclus": _f(_mean(tr["r_div_excl"])), "fragile": len(set(signs)) > 1}  # fmt: skip
    # regime
    t13 = tr["t13"].astype(bool)
    out["regime"] = {"t13_vrai": {"n": int(t13.sum()), "mean_r": _f(_mean(r[t13]))},
                     "t13_faux": {"n": int((~t13).sum()), "mean_r": _f(_mean(r[~t13]))}}  # fmt: skip
    # survivorship
    lam = breakeven_hazard_multiple(r, tr["hazard"])
    out["survie"] = {"multiple_risque_equilibre": _f(lam, 2) if math.isfinite(lam) else "infini",
                     "sorties_cotation_interrompue": int(tr["delisted"].sum()) if "delisted" in tr else 0,
                     "mean_r_radiees_perte_totale": _f(_mean(tr["r_delist_loss"])) if "r_delist_loss" in tr else None,
                     "hors_univers_actuel": {"n": int((~tr["in_universe"].astype(bool)).sum()),
                                             "mean_r": _f(_mean(r[~tr["in_universe"].astype(bool)]))},
                     "univers_actuel": {"n": int(tr["in_universe"].astype(bool).sum()),
                                        "mean_r": _f(_mean(r[tr["in_universe"].astype(bool)]))}}  # fmt: skip
    # capacity
    cap = tr["capacity_ok"].astype(bool)
    out["capacite"] = {"part_ok": _f(float(cap.mean()), 3), "oos": _summary(o.select(o["capacity_ok"].astype(bool)))}
    out["concentration"] = concentration(tr)
    out["splits"] = splits(tr, st.min_turnover_cto_eur is not None)
    out["ambigus"] = int(tr["ambiguous"].sum())
    return out


def _summary(tr: Trades) -> dict[str, Any]:
    if tr.n == 0:
        return {"n": 0}
    r = tr["r"][np.isfinite(tr["r"])]
    return {
        "n": int(r.size),
        "mean_r": _f(_mean(r)),
        "mean_eur_300": _f(_mean(tr["eur"]), 2),
        "profit_factor": _f(S.profit_factor(r)) if r.size else None,
        "mean_pct_notionnel": _f(_mean(tr["pct"]), 5) if "pct" in tr else None,
    }


def concentration(tr: Trades) -> dict[str, Any]:
    r = np.nan_to_num(tr["r"])
    inst = tr["inst"]
    u, inv = np.unique(inst, return_inverse=True)
    by = np.bincount(inv, weights=r)
    total = float(r.sum())
    order = np.argsort(-by)
    top10 = float(by[order[:10]].sum())
    top5 = set(u[order[:5]].tolist())
    rest = ~np.isin(inst, list(top5))
    wk, ws = S.weekly_series(tr["week"], tr["r"])
    fin = np.isfinite(tr["r"])
    counts = np.bincount(np.searchsorted(wk, tr["week"][fin]), minlength=wk.size)[: wk.size]
    w_by = np.sort(ws * counts)[::-1]
    return {
        "valeurs": int(u.size),
        "part_top10_valeurs": _f(top10 / total, 3) if total > 0 else None,
        "part_top5_semaines": _f(float(w_by[:5].sum()) / total, 3) if total > 0 else None,
        "mean_r_hors_top5_valeurs": _f(_mean(tr["r"][rest])),
        "gini_valeurs": _f(S.gini(by), 3),
    }


def splits(tr: Trades, cto_split: bool = False) -> dict[str, Any]:
    r = tr["r"]

    def sm(m: Arr) -> dict[str, Any]:
        return {"n": int(m.sum()), "mean_r": _f(_mean(r[m]))}

    venue = tr["venue"]  # the venue group of the trade (P0 = 0), not the panel's enumeration index
    out: dict[str, Any] = {"P0": sm(venue == 0), "P1_P2": sm((venue == 1) | (venue == 2))}
    for k in range(3):
        out[f"liquidite_t{k + 1}"] = sm(tr["liq"] == k)
    if cto_split and "liq_cto" in tr:  # plan §1.1: the CTO liquidity floor (60-session median turnover)
        lc = tr["liq_cto"].astype(bool)
        out["liquidite_seuil_cto_atteint"] = sm(lc)
        out["liquidite_sous_seuil_cto"] = sm(~lc)
    sc = tr["small"].astype(bool)
    out["petites_capitalisations"] = sm(sc)
    out["grandes_capitalisations"] = sm(~sc)
    out["annees"] = {int(y): sm(tr["year"] == y) for y in np.unique(tr["year"]).tolist()}
    return out


# --- multiple testing across cells -------------------------------------------------------------------


def weekly_matrix(series: Sequence[tuple[I64, F64]]) -> tuple[I64, F64]:
    weeks = np.unique(np.concatenate([w for w, _ in series])) if series else np.zeros(0, dtype=np.int64)
    mat = np.full((weeks.size, len(series)), np.nan)
    for j, (w, v) in enumerate(series):
        mat[np.searchsorted(weeks, w), j] = v
    return weeks, mat


def family_walk_forward(cells: Sequence[Cell], trades: Mapping[str, Trades], prereg: Prereg) -> dict[str, Any]:
    """Selection walk-forward of a family: for each test year, the variant with the best train mean excess
    (closed trades from the discovery start to the test start minus the purge) is evaluated on the test year.
    The concatenated test-year trades of the chosen variants are the family's out-of-sample series — the only
    one on which A2 (effect) and A5 (folds) of an exploratory cell are judged (a cell's own test years were
    also seen by the selection across the family)."""
    seg = prereg.segments
    purge_days = int(seg.purge_sessions * 7 / 5) + 1
    picked: list[Trades] = []
    choices: list[dict[str, Any]] = []
    folds: list[dict[str, Any]] = []
    for a, b in seg.wf_test:
        best, best_v = None, -math.inf
        train_end = np.datetime64(a, "D").astype(np.int64) - purge_days
        for c in cells:
            tr = trades.get(c.id)
            if tr is None or tr.n == 0:
                continue
            cl = tr.closed
            m = cl["day"] <= train_end
            v = _mean(cl["excess"][m])
            if v is not None and int(m.sum()) >= 20 and v > best_v:
                best, best_v = c, v
        if best is None:
            folds.append({"annee": a.year, "variante": None, "n": 0, "mean_r": None})
            continue
        cl = trades[best.id].closed
        test = cl.select(_window(cl, a, b))
        picked.append(test)
        choices.append({"annee": a.year, "variante": best.id})
        folds.append({"annee": a.year, "variante": best.id, **_summary(test)})
    oos = Trades.concat(picked)
    x = oos["excess"] if oos.n else np.zeros(0)
    pea = Trades({**oos.cols, "r": oos["r_pea"], "eur": oos["eur_pea"]}) if oos.n and "r_pea" in oos else oos
    return {
        "choix": choices,
        "annees_test": len(seg.wf_test),
        "n": int(np.isfinite(x).sum()),
        "mean_excess_oos": _f(_mean(x)),
        "oos": _summary(oos),
        "oos_pea": _summary(pea),
        "oos_capacite": _summary(oos.select(oos["capacity_ok"].astype(bool))) if oos.n else {"n": 0},
        "plis": folds,
        "plis_positifs": int(sum(1 for f in folds if (f.get("mean_r") or 0) > 0)),
    }


def walk_forward_choice(cell_id: str, wf: Mapping[str, Any]) -> bool:
    """The cell is the family's walk-forward choice in every test year (a choice in each one)."""
    ch = wf.get("choix") or []
    return len(ch) == int(wf.get("annees_test", 0)) > 0 and all(x.get("variante") == cell_id for x in ch)


# --- permutation and power ------------------------------------------------------------------------------


@dataclass
class DenseExcess:
    """Excess of every eligible (day, instrument) of a combination in one group, on the signal rows only
    (float32): ``exc`` = r − matched control (event cells), ``exc_ew`` = r − EW mean (monthly cells)."""

    exc: npt.NDArray[np.float32]
    exc_ew: npt.NDArray[np.float32] | None
    stratum: I8
    rows: tuple[int, int]

    def values(self, monthly: bool) -> npt.NDArray[np.float32]:
        return self.exc_ew if monthly and self.exc_ew is not None else self.exc


def date_shift_null(
    tr: Trades, dense: Mapping[int, DenseExcess], monthly: bool, draws: int, seed: int
) -> F64:  # fmt: skip
    """Null distribution of the mean excess: each instrument's signal days shifted by one random circular
    offset inside the signal window (clusters and seasonality of each instrument kept)."""
    rng = np.random.default_rng(seed)
    total = np.zeros(draws)
    count = np.zeros(draws)
    for gid, de in sorted(dense.items()):
        m = tr["group"] == gid
        if not m.any():
            continue
        lo, hi = de.rows
        width = hi - lo + 1
        if width <= 1:
            continue
        t = tr["t"][m] - lo
        col = tr["inst"][m] % 1_000_000
        arr = de.values(monthly)
        u, inv = np.unique(col, return_inverse=True)
        for d in range(draws):
            shift = rng.integers(1, width, size=u.size)[inv]
            v = arr[(t + shift) % width, col].astype(np.float64)
            ok = np.isfinite(v)
            total[d] += v[ok].sum()
            count[d] += ok.sum()
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(count > 0, total / count, np.nan)


def planted_power(
    tr: Trades, dense: Mapping[int, DenseExcess], monthly: bool, edge: float, reps: int, seed: int,
    min_t: float, min_excess: float, lag: int,
) -> float:  # fmt: skip
    """Share of replications where pseudo-trades (a random member of the same day and stratum, plus
    ``edge``) pass t ≥ ``min_t`` and mean excess ≥ ``min_excess`` — the power of the test on this design."""
    rng = np.random.default_rng(seed)
    pools: dict[int, tuple[I64, I64, I64]] = {}
    for gid, de in dense.items():
        arr = de.values(monthly)
        ok = np.isfinite(arr) & (de.stratum >= 0)
        tt, cc = np.nonzero(ok)
        key = tt.astype(np.int64) * 18 + de.stratum[tt, cc].astype(np.int64)
        order = np.argsort(key, kind="stable")
        pools[gid] = (key[order], tt[order].astype(np.int64), cc[order].astype(np.int64))
    if tr.n == 0 or not pools:
        return math.nan
    passed = 0
    for _ in range(reps):
        vals = np.full(tr.n, np.nan)
        for gid, (keys, tt, cc) in sorted(pools.items()):
            de = dense[gid]
            m = np.nonzero(tr["group"] == gid)[0]
            if m.size == 0 or keys.size == 0:
                continue
            t = tr["t"][m] - de.rows[0]
            col = tr["inst"][m] % 1_000_000
            inside = (t >= 0) & (t < de.stratum.shape[0])
            t = np.clip(t, 0, de.stratum.shape[0] - 1)
            st_ = de.stratum[t, col].astype(np.int64)
            k = t * 18 + st_
            a = np.searchsorted(keys, k, side="left")
            b = np.searchsorted(keys, k, side="right")
            has = (b > a) & (st_ >= 0) & inside
            pick = a + np.floor(rng.random(m.size) * np.maximum(b - a, 1)).astype(np.int64)
            pick = np.minimum(pick, keys.size - 1)
            v = de.values(monthly)[tt[pick], cc[pick]].astype(np.float64) + edge
            vals[m[has]] = v[has]
        fake = Trades({**tr.cols, "excess": vals})
        _, ws = series_of(fake, monthly)
        if ws.size < 3:
            continue
        t_ = S.newey_west_t(ws, lag)
        if math.isfinite(t_) and t_ >= min_t and float(np.nanmean(vals)) >= min_excess:
            passed += 1
    return passed / reps if reps else math.nan


# --- portfolio replay (stage 2) -----------------------------------------------------------------------


def replay(tr: Trades, slots: int, value: str = "r") -> dict[str, Any]:
    ok = tr["closed"].astype(bool) & np.isfinite(tr[value])
    if not ok.any():
        return {"taken": 0}
    ed, xd, v = tr["entry_day"][ok], tr["exit_day"][ok], tr[value][ok]
    base = date(1970, 1, 1).toordinal()
    rt = [ReplayTrade(date.fromordinal(base + int(a)), date.fromordinal(base + int(b)), float(x))
          for a, b, x in zip(ed.tolist(), xd.tolist(), v.tolist(), strict=True)]  # fmt: skip
    res = portfolio_replay(rt, slots)
    return res.as_dict()


# --- stage-0 over the groups ------------------------------------------------------------------------


@dataclass
class StageResult:
    trades: dict[str, Trades]
    dense: dict[ComboKey, dict[int, DenseExcess]]
    baselines: dict[ComboKey, dict[str, float | None]]
    signals_rows: dict[int, tuple[int, int]]
    suspect_dividend_years: int = 0
    masked_signals: dict[str, int] = field(default_factory=dict)  # per cell: signal days masked after a CA
    quality: dict[str, int] = field(default_factory=dict)  # data-quality counts summed over the groups
    coverage: dict[str, dict[str, Any]] = field(default_factory=dict)  # earnings coverage per venue group (n° 3)


def run_cells(
    groups: Iterable[GroupData], cells: Sequence[Cell], st: Settings, window: tuple[date, date], end_margin: int,
    keep_dense: bool = True, progress: Callable[[str], None] | None = None,
    hook: Callable[[GroupData, tuple[int, int]], None] | None = None,
) -> StageResult:  # fmt: skip
    """Simulate every cell of the list on every group (built lazily, one in memory at a time) for signals in
    ``window``; ``hook`` runs on each group too (F0 diagnostics)."""
    by_combo: dict[ComboKey, list[Cell]] = defaultdict(list)
    for c in cells:
        by_combo[c.combo_key].append(c)
    parts: dict[str, list[Trades]] = defaultdict(list)
    dense: dict[ComboKey, dict[int, DenseExcess]] = defaultdict(dict)
    base_acc: dict[ComboKey, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])
    rows_by: dict[int, tuple[int, int]] = {}
    suspect = 0
    masked: dict[str, int] = defaultdict(int)
    quality: dict[str, int] = defaultdict(int)
    coverage: dict[str, dict[str, Any]] = {}
    for g in groups:
        rows = signal_rows(g.p.days, window[0], window[1], end_margin)
        rows_by[g.gid] = rows
        suspect += g.suspect_dividend_years
        for k, v in g.quality.items():
            quality[k] += v
        if rows[1] < rows[0]:
            continue
        if any(c.trigger in T.EARNINGS_TRIGGERS for c in cells):
            cov = earnings_coverage(g, rows, st.min_earnings_coverage)
            g.earn_ok = bool(cov["compte_dans_le_verdict"])
            coverage[g.p.group] = cov
        quality["observations_masquees_ost"] += int(g.f.ca_masked[rows[0] : rows[1] + 1].sum())
        for c in cells:
            masked[c.id] += masked_signal_count(g, c, st, rows)
        if hook is not None:
            hook(g, rows)
        for key, cs in sorted(by_combo.items(), key=lambda kv: str(kv[0])):
            if progress:
                progress(f"{g.p.group} : combinaison {combo_label(key)}")
            combo = simulate_combo(g, key, st, rows)
            acc = base_acc[key]
            acc[0] += combo.all_mean[0]
            acc[1] += combo.all_mean[1]
            acc[2] += combo.t04_mean[0]
            acc[3] += combo.t04_mean[1]
            if keep_dense:
                lo, hi = rows
                monthly = any(c.mode != "event" for c in cs)
                dense[key][g.gid] = DenseExcess(
                    (combo.r - combo.ctrl)[lo : hi + 1].astype(np.float32),
                    (combo.r - combo.ew[:, None])[lo : hi + 1].astype(np.float32) if monthly else None,
                    g.f.stratum[lo : hi + 1].copy(),
                    rows,
                )
            for c in cs:
                parts[c.id].append(evaluate_cell(g, c, combo, st, rows))
            del combo
    baselines = {
        k: {"univers_ew": v[0] / v[1] if v[1] else None, "tendance_t04": v[2] / v[3] if v[3] else None}
        for k, v in base_acc.items()
    }
    return StageResult(
        {k: Trades.concat(v) for k, v in parts.items()}, dict(dense), baselines, rows_by, suspect, dict(masked),
        dict(quality), coverage,
    )  # fmt: skip


INSUFFICIENT_COVERAGE = "couverture insuffisante"


def earnings_coverage(g: GroupData, rows: tuple[int, int], floor: float | None) -> dict[str, Any]:
    """Earnings coverage of a group on the signal window: the share of the eligible observations (value ×
    session) inside the known span of the value's announcements (the plan's ``min_earnings_coverage`` is
    judged on it), and the values eligible / covered at least once. Below the floor the group is reported
    « couverture insuffisante » and its earnings cells give no signal (it does not count in the verdict)."""
    lo, hi = rows
    el = g.f.elig[lo : hi + 1]
    kn = g.p.earn_known[lo : hi + 1] if g.p.earn_known is not None else np.zeros_like(el)
    react = g.p.earn_react[lo : hi + 1] if g.p.earn_react is not None else np.zeros_like(el)
    obs = int(el.sum())
    share = float((el & kn).sum()) / obs if obs else 0.0
    ok = floor is None or share >= float(floor)
    return {
        "part_observations_couvertes": _f(share, 3),
        "valeurs_eligibles": int(el.any(axis=0).sum()),
        "valeurs_couvertes": int((el & kn).any(axis=0).sum()),
        "seances_de_reaction_eligibles": int((react & el).sum()),
        "seuil": floor,
        "compte_dans_le_verdict": ok,
        "statut": "suffisante" if ok else INSUFFICIENT_COVERAGE,
    }


def finalize_monthly(tr: Trades, cell: Cell, st: Settings) -> Trades:
    """Monthly cells: top-N selection (and the overlay pair of H8 as a paired monthly difference)."""
    if cell.mode == "event" or tr.n == 0:
        return tr
    hm = max(1, math.ceil(cell.h / 21))
    base = select_top(tr, st.top_n, hm, st.seed)
    if cell.mode == "monthly_overlay":
        ov = select_top(tr, st.top_n, hm, st.seed, extra=tr["overlay"].astype(bool))
        return paired_overlay(add_excess(base.closed, True), add_excess(ov.closed, True), base, ov)
    return base


def paired_overlay(b: Trades, o: Trades, base_all: Trades, ov_all: Trades) -> Trades:
    """H8: monthly difference (overlay top-N − base top-N) of the mean excess, as one row per month."""
    if b.n == 0 or o.n == 0:
        return Trades({})
    mb, sb = S.weekly_series(b["month"], b["excess"])
    mo, so = S.weekly_series(o["month"], o["excess"])
    common = np.intersect1d(mb, mo)
    d = so[np.searchsorted(mo, common)] - sb[np.searchsorted(mb, common)]
    k = common.size
    first = np.array([int(o["week"][o["month"] == m].min()) for m in common.tolist()], dtype=np.int64)
    day = np.array([int(o["day"][o["month"] == m].min()) for m in common.tolist()], dtype=np.int64)
    year = np.array([int(o["year"][o["month"] == m].min()) for m in common.tolist()], dtype=np.int64)
    nan = np.full(k, np.nan)
    cols: dict[str, Arr] = {key: nan.copy() for key in ("r", "r_gross", "r_ref", "r_stop", "pct", "eur", "r_div_net",
                            "r_div_excl", "r_stress", "r_pea", "eur_pea", "r_srd", "hazard", "fwd", "score",
                            "r_delist_loss")}  # fmt: skip
    cols.update(
        group=np.zeros(k, dtype=np.int64), venue=np.full(k, -1, dtype=np.int64), inst=np.arange(k, dtype=np.int64),
        t=np.zeros(k, dtype=np.int64),
        day=day, week=first, month=common, year=year, status=np.full(k, CLOSED, dtype=np.int64),
        closed=np.ones(k, dtype=bool), entry_day=day, exit_day=day, hold=np.full(k, 21, dtype=np.int64),
        reason=np.full(k, -1, dtype=np.int64), ambiguous=np.zeros(k, dtype=bool), ctrl=np.zeros(k), ew=np.zeros(k),
        capacity_ok=np.ones(k, dtype=bool), small=np.zeros(k, dtype=bool), liq=np.zeros(k, dtype=np.int64),
        t13=np.zeros(k, dtype=bool), in_universe=np.ones(k, dtype=bool), pea_eligible=np.ones(k, dtype=bool),
        overlay=np.ones(k, dtype=bool), delisted=np.zeros(k, dtype=bool),
    )  # fmt: skip
    cols["r"] = d
    cols["r_delist_loss"] = d
    cols["r_gross"] = d
    cols["r_stress"] = d
    cols["r_div_net"] = d
    cols["r_div_excl"] = d
    cols["hazard"] = np.zeros(k)
    return Trades(cols)
