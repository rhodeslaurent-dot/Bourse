"""Triggers of the pre-registered plan (docs/31 §1.2–1.3). Each one is a boolean panel ``(T, N)`` computed
from data known at the close of ``t`` only, and true only on eligible bars. Pure functions."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import numpy as np

from app.domain.research.panel import (
    F64,
    Bool,
    Days,
    Features,
    IndexSeries,
    Packing,
    Panel,
    pack,
    packed_adjusted,
    prior_any,
    rolling_mean,
    shift,
    true_range,
    unpack,
    unpack_bool,
)


def _b(x: object) -> Bool:
    return np.asarray(x, dtype=np.bool_)


def _range_pos(p: Panel) -> F64:
    rng = p.high - p.low
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(rng > 0, (p.close - p.low) / rng, np.nan)


def t01_momentum(f: Features, rank_min: float, month_end_only: bool = True) -> Bool:
    """T01 momentum 12-1: rank of adj[t−21]/adj[t−252] − 1 within the eligible set ≥ ``rank_min``; on the
    last session of each month when ``month_end_only``."""
    with np.errstate(invalid="ignore"):
        cond = f.elig & (np.nan_to_num(f.mom_rank, nan=-1.0) >= rank_min)
    return cond & f.month_end[:, None] if month_end_only else cond


def t03_pth(p: Panel, f: Features, pth_min: float, rs_min: float, quiet_sessions: int = 10) -> Bool:
    """T03 52-week-high proximity: close / max(high, 252) ≥ ``pth_min`` and rs_rank ≥ ``rs_min``, on the first
    day the condition holds after at least ``quiet_sessions`` own sessions without it."""
    with np.errstate(invalid="ignore", divide="ignore"):
        cond = f.elig & (p.close / f.high_252 >= pth_min) & (np.nan_to_num(f.rs_rank, nan=-1.0) >= rs_min)
    before = unpack_bool(prior_any(pack(cond.astype(np.float64), f.pk) == 1.0, quiet_sessions), f.pk, cond.shape[0])
    return cond & ~before


def t00_placebo(f: Features, rate: float, seed: int, month_end_only: bool) -> Bool:
    """T00 placebo (registration n° 2, X1): eligible bars drawn at random with probability ``rate`` (event
    mode), or every eligible month end (monthly mode, the top-N then drawn by ``placebo_score``). Any excess
    it shows is a bias of the measure, not an edge — it runs before the cells it controls."""
    if month_end_only:
        return f.elig & f.month_end[:, None]
    u = np.random.default_rng(seed).random(f.elig.shape)
    return f.elig & (u < rate)


def placebo_score(shape: tuple[int, int], seed: int) -> F64:
    """Random monthly ranking score of T00 (never a price-derived quantity)."""
    return np.random.default_rng(seed + 1).random(shape)


def t04_trend(f: Features) -> Bool:
    return f.elig & f.t04


def t05_reversal(f: Features, k_sigma: float) -> Bool:
    """T05 short-term reversal in an uptrend: T04 and the 5-session log return ≤ −k · σ20 · √5."""
    with np.errstate(invalid="ignore"):
        return f.elig & f.t04 & (f.ret5 <= -k_sigma * f.sigma20 * math.sqrt(5))


def t07_wide_range(p: Panel, f: Features, tr_mult: float, rvol_min: float) -> Bool:
    """T07 wide-range continuation: true range ≥ ``tr_mult`` · ATR14 of the previous session, close in the
    top quartile of the range, RVOL ≥ ``rvol_min``, T04."""
    tr = np.fmax(p.high - p.low, np.fmax(np.abs(p.high - f.prev_close), np.abs(p.low - f.prev_close)))
    with np.errstate(invalid="ignore"):
        return f.elig & f.t04 & (tr >= tr_mult * f.atr14_prev) & (_range_pos(p) >= 0.75) & (f.rvol_med20 >= rvol_min)


def t08_gap(p: Panel, f: Features, gap_min: float, rvol_min: float = 2.0) -> Bool:
    """T08 gap continuation: open ≥ previous close · (1 + gap), close > open, close in the top third of the
    range, RVOL ≥ ``rvol_min``."""
    with np.errstate(invalid="ignore"):
        return _b(
            f.elig
            & (p.open >= f.prev_close * (1 + gap_min))
            & (p.close > p.open)
            & (_range_pos(p) >= 2.0 / 3.0)
            & (f.rvol_med20 >= rvol_min)
        )


def t09_volume_shock(p: Panel, f: Features, mult: float = 3.0) -> Bool:
    """T09 volume shock: volume ≥ the maximum of the 49 previous sessions and ≥ ``mult`` × the median of the
    50 previous sessions, close ≥ open."""
    with np.errstate(invalid="ignore"):
        return f.elig & (p.volume >= f.vol_prev_max49) & (p.volume >= mult * f.vol_prev_med50) & (p.close >= p.open)


# --- information events: earnings announcements (registration n° 3, docs/34 §34.5) ------------------------

EARNINGS_TRIGGERS = frozenset({"t14", "t15", "t16"})
NEWS_WINDOW_SESSIONS = 2  # t16: no reaction session within ± 2 own sessions


def _index_close_on(idx: IndexSeries | None, days: Days) -> F64:
    """The index's last close on or before each day (NaN before its first close or without index)."""
    out = np.full(days.shape[0], np.nan)
    if idx is None or idx.close.size == 0:
        return out
    k = np.searchsorted(idx.days, days, side="right") - 1
    ok = k >= 0
    out[ok] = idx.close[k[ok]]
    return out


def abnormal_log_return(p: Panel, f: Features) -> F64:
    """Log return of the adjusted close from the previous own session J−1 to J0, minus the log return of the
    value's venue index over the same span (CAC 40 for Paris or the group's own index, STOXX 600 otherwise:
    the regime index of the panel). NaN when the index is unknown on either day (never an estimate)."""
    pk = f.pk
    t_len = p.shape[0]
    cac = _index_close_on(p.index_cac, p.days)[:, None]
    stoxx = _index_close_on(p.index_stoxx, p.days)[:, None]
    idx = np.where(p.is_xpar[None, :], cac, stoxx)
    adj = packed_adjusted(p, pk)
    ix = pack(np.asarray(idx, dtype=np.float64), pk)
    with np.errstate(invalid="ignore", divide="ignore"):
        return unpack(np.log(adj / shift(adj, 1)) - np.log(ix / shift(ix, 1)), pk, t_len)


def _earnings(p: Panel) -> tuple[Bool, Bool]:
    """Reaction sessions and known span; without announcement history nothing is known (no event trigger)."""
    if p.earn_react is None or p.earn_known is None:
        z = np.zeros(p.shape, dtype=np.bool_)
        return z, z
    return p.earn_react, p.earn_known


def information_shock(p: Panel, f: Features, k_sigma: float, rvol_min: float, close_top: float) -> Bool:
    """Price and volume shock of J0 (t14 / t16): abnormal log return ≥ ``k_sigma`` · σ of the 20 daily log
    returns **before** J0, volume ≥ ``rvol_min`` × the median volume of the 50 previous sessions, close in the
    top ``close_top`` of the J0 range. Known at the close of J0."""
    sigma_prev = unpack(shift(pack(f.sigma20, f.pk), 1), f.pk, p.shape[0])
    ar = abnormal_log_return(p, f)
    with np.errstate(invalid="ignore"):
        return _b(
            f.elig
            & (ar >= k_sigma * sigma_prev)
            & (sigma_prev > 0)
            & (p.volume >= rvol_min * f.vol_prev_med50)
            & (f.vol_prev_med50 > 0)
            & (_range_pos(p) >= 1.0 - close_top)
        )


def t14_earnings_reaction(
    p: Panel, f: Features, k_sigma: float = 2.0, rvol_min: float = 2.0, close_top: float = 0.5,
    require_surprise: bool = False,
) -> Bool:  # fmt: skip
    """T14 post-earnings-announcement drift (Brandt et al. 2008; Garfinkel & Sokobin 2006): the shock of
    ``information_shock`` on an announcement's reaction session, inside the known span; with
    ``require_surprise`` the reported EPS also beats the estimate (unknown → no signal)."""
    react, known = _earnings(p)
    m = information_shock(p, f, k_sigma, rvol_min, close_top) & react & known
    if require_surprise:
        sp = p.earn_surprise_pos if p.earn_surprise_pos is not None else np.full(p.shape, np.nan)
        m &= np.nan_to_num(sp, nan=0.0) == 1.0
    return _b(m)


def t15_pre_earnings(p: Panel, f: Features, lead: int = 5) -> Bool:
    """T15 earnings announcement premium (Barber et al. 2013): the own session ``lead`` sessions before a
    reaction session. ASSUMPTION (flagged in the report): the announcement date is known ``lead`` sessions
    ahead (most European issuers publish a financial calendar)."""
    react, known = _earnings(p)
    ahead = shift(pack(react.astype(np.float64), f.pk), -int(lead)) == 1.0
    return _b(f.elig & known & unpack_bool(ahead, f.pk, p.shape[0]))


def _near_reaction(p: Panel, f: Features, react: Bool) -> Bool:
    """A reaction session within ± ``NEWS_WINDOW_SESSIONS`` own sessions of each row (calendar)."""
    r = pack(react.astype(np.float64), f.pk) == 1.0
    near = np.zeros_like(r)
    for k in range(-NEWS_WINDOW_SESSIONS, NEWS_WINDOW_SESSIONS + 1):
        near |= shift(r.astype(np.float64), k) == 1.0
    return unpack_bool(near, f.pk, p.shape[0])


def t16_shock_without_news(
    p: Panel, f: Features, k_sigma: float = 2.0, rvol_min: float = 2.0, close_top: float = 0.5
) -> Bool:  # fmt: skip
    """T16 control of T14 (Chan 2003: shocks without news reverse): the same shock on a session inside the known
    span with no reaction session within ± ``NEWS_WINDOW_SESSIONS`` own sessions (the dates of the next two
    sessions' announcements assumed known, as for T15)."""
    react, known = _earnings(p)
    return _b(information_shock(p, f, k_sigma, rvol_min, close_top) & known & ~_near_reaction(p, f, react))


# --- opening gap down, bought at the next open (registration n° 7, docs/34 §34.14) ---------------------------

GAP_TRIGGERS = frozenset({"t18"})
GAP_EARNINGS = ("on", "off")  # t18 split: gap on an announcement's reaction session / known span, none within ± 2
GAP_CAPS = ("small", "large")  # t18 split ``cap_class``: today's capitalisation (``Panel.small_cap``, flagged)
GAP_REQUIRED = ("gap_min", "gap_atr", "atr_sessions", "dedup_sessions", "dividend_neutral")  # stated by the plan
GAP_OPTIONAL = ("size_from", "size_to", "cap_class", "earnings")


def gap_needs_earnings(params: Mapping[str, Any]) -> bool:
    """A t18 cell split on the announcements (``earnings: on | off``) needs the announcement history."""
    return params.get("earnings") is not None


def cell_needs_earnings(trigger: str, params: Mapping[str, Any]) -> bool:
    """The cell reads the announcement history (t14 / t15 / t16, or a t18 split on the announcements): it is then
    silenced in a venue group whose coverage is under the plan's floor."""
    return trigger in EARNINGS_TRIGGERS or (trigger in GAP_TRIGGERS and gap_needs_earnings(params))


def check_gap_params(params: Mapping[str, Any]) -> None:
    """The t18 parameters of a cell: the definition keys stated (never a code default), the splits in their closed
    lists. Raises ValueError."""
    missing = [k for k in GAP_REQUIRED if k not in params]
    unknown = sorted(set(params) - set(GAP_REQUIRED) - set(GAP_OPTIONAL))
    if missing or unknown:
        raise ValueError(f"t18 : paramètres absents {missing} ou inconnus {unknown}")
    cap, earn = params.get("cap_class"), params.get("earnings")
    if cap not in (None, *GAP_CAPS) or earn not in (None, *GAP_EARNINGS):
        raise ValueError(f"t18 : découpage inconnu (cap_class {cap!r}, earnings {earn!r})")
    lo, hi = float(params.get("size_from", 0.0)), float(params.get("size_to", math.inf))
    if not (0.0 <= lo < hi) or int(params["dedup_sessions"]) < 1 or int(params["atr_sessions"]) < 1:
        raise ValueError("t18 : tranche de taille, délai ou ATR invalides")


def simple_atr(p: Panel, pk: Packing, n: int) -> F64:
    """The ATR of the causes study (``moves.atr_series``, docs/36): the plain mean of the true ranges of the ``n``
    own sessions ending at each row, raw prices, the first own bar's true range being its high − low. It is not the
    bench's Wilder ATR14 (``Features.atr14``, which places the stops). Calendar array, NaN before ``n`` bars."""
    tr = true_range(pack(p.high, pk), pack(p.low, pk), pack(p.close, pk))
    return unpack(rolling_mean(tr, n), pk, p.shape[0])


def opening_gap(p: Panel, f: Features, dividend_neutral: bool) -> F64:
    """Open of each own session ÷ close of the previous own session − 1 (raw prices, ``move_causes.opening_gap``).
    ``dividend_neutral``: times the ratio of the adjustment factors of the two sessions, so that a detachment opens
    no gap (only the ratio is used: the factors' levels embed later dividends, their ratio only this session's)."""
    pk = f.pk
    o, c = pack(p.open, pk), pack(p.close, pk)
    with np.errstate(invalid="ignore", divide="ignore"):
        g = o / shift(c, 1)
        if dividend_neutral:
            fac = pack(f.adj_factor, pk)
            g = g * (fac / shift(fac, 1))
        return unpack(np.where((o > 0) & (shift(c, 1) > 0), g - 1.0, np.nan), pk, p.shape[0])


def first_every(events: Bool, pk: Packing, sessions: int) -> Bool:
    """The first event of each value, then none until ``sessions`` own sessions later (two kept events are at
    least ``sessions`` own sessions apart, as ``move_causes`` / ``_refractory``): outcome-free. Calendar arrays."""
    ev = pack(events.astype(np.float64), pk) == 1.0
    keep = np.zeros_like(ev)
    for c in np.nonzero(ev.any(axis=0))[0].tolist():
        nxt = -1
        for q in np.nonzero(ev[:, c])[0].tolist():
            if q >= nxt:
                keep[q, c] = True
                nxt = q + sessions
    return unpack_bool(keep, pk, events.shape[0])


def gap_down_events(p: Panel, f: Features, gap_min: float, gap_atr: float, atr_sessions: int,
                    dividend_neutral: bool) -> tuple[Bool, F64]:  # fmt: skip
    """Every own session k whose opening gap is a gap **down** of at least max(``gap_min`` ; ``gap_atr`` · ATR ÷
    close), ATR and close of the previous own session k − 1 (``move_causes.gap_threshold``: known before the open of
    k), eligible or not; and the gap itself. Known at the open of k: bought at the open of k + 1 at the earliest."""
    pk = f.pk
    atr_prev = unpack(shift(pack(simple_atr(p, pk, atr_sessions), pk), 1), pk, p.shape[0])
    with np.errstate(invalid="ignore", divide="ignore"):
        thr = np.maximum(gap_min, gap_atr * atr_prev / f.prev_close)
        gap = opening_gap(p, f, dividend_neutral)
        ev = np.isfinite(thr) & np.isfinite(gap) & (f.prev_close > 0) & (gap <= -thr)
    return _b(ev & f.valid), gap


def t18_gap_down(p: Panel, f: Features, params: Mapping[str, Any]) -> Bool:
    """T18 (registration n° 7, lead of the causes study docs/36): an opening gap down of at least max(``gap_min`` ;
    ``gap_atr`` · ATR ÷ previous close) on session k, signalled at k (the order works from the open of k + 1).

    One event per value every ``dedup_sessions`` own sessions — the first one, counted on every gap down of the value
    (eligible or not, before any split: the split cells partition the same events) —, then the cell's splits:
    |gap| in [``size_from`` ; ``size_to``), today's capitalisation class (``cap_class``), and ``earnings``: ``on`` = the
    gap session is an announcement's reaction session inside the known span, ``off`` = known span and no reaction
    session within ± 2 own sessions (the next two sessions' dates assumed known, as t16; unknown is never « no
    news »). True only on eligible bars; corporate actions (and, when the plan declares ``suspect_price_ratio``,
    suspect prices) mask eligibility like every trigger (``pipeline.prepare_group``)."""
    check_gap_params(params)
    ev, gap = gap_down_events(p, f, float(params["gap_min"]), float(params["gap_atr"]), int(params["atr_sessions"]),
                              bool(params["dividend_neutral"]))  # fmt: skip
    m = first_every(ev, f.pk, int(params["dedup_sessions"]))
    lo, hi = float(params.get("size_from", 0.0)), float(params.get("size_to", math.inf))
    with np.errstate(invalid="ignore"):
        m &= (np.abs(gap) >= lo) & (np.abs(gap) < hi)
    cap = params.get("cap_class")
    if cap is not None:
        m &= p.small_cap[None, :] if cap == "small" else ~p.small_cap[None, :]
    earn = params.get("earnings")
    if earn is not None:
        react, known = _earnings(p)
        m &= (react & known) if earn == "on" else (known & ~_near_reaction(p, f, react))
    return _b(m & f.elig)


def t11_low_vol(f: Features) -> Bool:
    return f.low_vol


def t13_regime(f: Features) -> Bool:
    return f.t13


def gate_mask(f: Features, gate: str) -> Bool:
    if gate == "none":
        return f.elig
    if gate == "t13":
        return f.t13
    if gate == "t11":
        return f.low_vol
    if gate == "t13_t11":
        return f.t13 & f.low_vol
    if gate == "t04":
        return f.t04
    raise ValueError(f"unknown gate {gate}")


# --- production detectors, vectorised necessary conditions (D2x, F0 pre-filters) -------------------------


def d2_base(p: Panel, f: Features, d2: Mapping[str, Any]) -> Bool:
    """D2 breakout conditions of ``detectors.D2Breakout`` without its entry cap (rs_rank: research rank)."""
    with np.errstate(invalid="ignore"):
        return _b(
            f.elig
            & (f.sma50 > f.sma200)
            & (p.close > f.sma50)
            & (np.nan_to_num(f.rs_rank, nan=-1.0) >= d2["rs_rank_min"])
            & (f.rvol_mean20 >= d2["rvol_min"])
            & (p.close > f.pivot60)
            & (f.prev_close <= f.pivot60)
            & (p.close <= f.sma20 + d2["max_extension_atr_above_mm20"] * f.atr14)
            & (f.consolidation20 <= d2["consolidation_20_max"])
            & (f.atr14 > 0)
        )


def d2_cap(p: Panel, f: Features, cap_kind: str, cap_value: float) -> F64:
    """Maximum entry price of a D2x variant: pivot · (1 + x), pivot + x · ATR, or close + x · ATR (E_open)."""
    if cap_kind == "pct_pivot":
        return f.pivot60 * (1 + cap_value)
    if cap_kind == "atr_pivot":
        return f.pivot60 + cap_value * f.atr14
    if cap_kind == "e_open":
        return p.close + cap_value * f.atr14
    raise ValueError(f"unknown cap {cap_kind}")


def d3_base(p: Panel, f: Features, d3: Mapping[str, Any]) -> Bool:
    """D3 range-expansion conditions of ``detectors.D3RangeExpansion`` (rs_rank: research rank)."""
    with np.errstate(invalid="ignore", divide="ignore"):
        return _b(
            f.elig
            & (p.close / f.prev_close >= d3["close_ratio_min"])
            & (f.rvol_mean20 >= d3["rvol_min"])
            & (p.volume > f.prev_volume)
            & (np.nan_to_num(_range_pos(p), nan=1.0) >= 2.0 / 3.0)
            & (f.ti65 >= d3["ti65_min"])
            & (np.nan_to_num(f.rs_rank, nan=-1.0) >= d3["rs_rank_min"])
            & (f.atr14 > 0)
        )


def d7_base(p: Panel, f: Features, d7: Mapping[str, Any]) -> Bool:
    """D7 trend-quality conditions of ``detectors.D7TrendQuality`` (rs_rank: research rank)."""
    near = float(d7.get("near_mm50_pct", 0.03))
    with np.errstate(invalid="ignore"):
        return _b(
            f.elig
            & (np.nan_to_num(f.rs_rank, nan=-1.0) >= d7["rs_rank_min"])
            & (p.close > f.sma200)
            & (f.sma200 > f.sma200_lag20)
            & (f.sma50 > f.sma200)
            & (p.low <= f.sma50 * (1 + near))
            & (p.close >= f.sma50 * 0.99)
            & ((p.close > p.open) | (p.close > f.prev_close))
            & (f.atr14 > 0)
        )


def d4_prefilter(p: Panel, f: Features, d4: Mapping[str, Any]) -> Bool:
    """Necessary conditions of ``detectors.D4Pullback`` (the detector itself checks the rest)."""
    with np.errstate(invalid="ignore"):
        touched = ((p.low <= f.sma10 * 1.01) & (p.close >= f.sma10 * 0.99)) | (
            (p.low <= f.sma20 * 1.01) & (p.close >= f.sma20 * 0.99)
        )
        return _b(
            f.elig
            & (np.nan_to_num(f.rs_rank, nan=-1.0) >= d4["rs_rank_min"])
            & (p.close > f.sma50)
            & touched
            & ((p.close > p.open) | (p.close > f.prev_close))
            & (f.rsi14 >= float(d4["rsi_range"][0]) - 1e-9)
            & (f.rsi14 <= float(d4["rsi_range"][1]) + 1e-9)
        )
