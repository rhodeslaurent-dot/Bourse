"""Triggers of the pre-registered plan (docs/31 §1.2–1.3). Each one is a boolean panel ``(T, N)`` computed
from data known at the close of ``t`` only, and true only on eligible bars. Pure functions."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import numpy as np

from app.domain.research.panel import F64, Bool, Features, Panel, pack, prior_any, unpack_bool


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
