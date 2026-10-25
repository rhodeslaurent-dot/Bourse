"""Entry detectors D2, D3, D4 (docs/06 §6.3 and §6.7, docs/22 R1–R6). Pure, end-of-day.

Each detector is a class with ``name``, ``params`` and ``detect(bars, ctx) -> list[Setup]``. A setup
is a *candidate*, not a proposal: the engine then applies admission (R4/R5), full cost, sizing,
caps and the market gate. Levels are structural:
- stop: under the nearest support zone with the wick buffer (R1), else the detector's structure
  (lowest low − 0.25 ATR); rejected (WATCH « stop trop large ») when too far;
- targets: *exploitable* levels only (resistance zones, previous highs, measured move), never an R
  multiple (R4); no exploitable target → the setup is kept as « sans objectif exploitable ».
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.domain.admission import Setup as SetupKind
from app.domain.indicators import Bar, DailyFeatures, compute_daily_features
from app.domain.stops import place_stop
from app.domain.zones import DetectedZone, exploitable_targets, supports_below


@dataclass(frozen=True)
class DetectorContext:
    rs_rank: float | None  # cross-sectional rank 0–100 (features_daily)
    zones: list[DetectedZone]
    wick_buffer_pct: float  # risk.stop.wick_buffer_pct
    stop_buffer_atr_mult: float  # risk.stop.buffer_atr_mult
    extra_targets: list[float] = field(default_factory=list)  # published targets (letters), if any
    buzz: bool = False  # docs/26 S3: the radar saw retail chatter accelerate before any publication


@dataclass
class Setup:
    detector: str
    kind: SetupKind
    entry_level: float  # trigger (or limit) for the next session
    p_max_technical: float  # detector's own cap on the entry price
    stop: float
    stop_basis: str  # "zone" | "structure"
    level_low: float  # R5: support low (pullback) / crossed obstacle low (breakout)
    targets: list[float]  # exploitable, nearest first
    horizon: str  # days | swing_2_6w
    evidence: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def testable(self) -> bool:
        return bool(self.targets) and not self.warnings


def _stop(
    entry: float, structural_low: float, atr: float, ctx: DetectorContext, max_dist: float
) -> tuple[float, str, list[str]]:
    """R1: under the nearest support zone between the structure and the entry, else structural."""
    structural = structural_low - ctx.stop_buffer_atr_mult * atr
    zones = [z for z in supports_below(entry, ctx.zones) if z.high < entry and z.low >= structural]
    if zones:
        stop, basis = place_stop(zones[0].zone, ctx.wick_buffer_pct), "zone"
    else:
        stop, basis = structural, "structure"
    warnings = []
    if entry - stop > max_dist:
        warnings.append(f"stop trop large ({(entry - stop) / entry:.1%} sous l'entrée)")
    return stop, basis, warnings


def _targets(p_max: float, ctx: DetectorContext, extra: list[float]) -> list[float]:
    return [t for t in exploitable_targets(p_max, ctx.zones, extra + ctx.extra_targets) if t > p_max * 1.005]


class D2Breakout:
    """Momentum leader breaking its 60-session pivot out of a consolidation (swing 2–6 weeks)."""

    name = "d2_breakout"

    def __init__(self, params: dict[str, Any]) -> None:
        self.params = params

    def detect(self, bars: list[Bar], ctx: DetectorContext) -> list[Setup]:
        p = self.params
        if len(bars) < 210:
            return []
        f: DailyFeatures = compute_daily_features(bars)
        last = bars[-1]
        if None in (f.mm50, f.mm200, f.mm20, f.atr14, f.adr20, f.pivot_60, f.rvol, f.consolidation_20):
            return []
        if not (f.mm50 and f.mm200 and f.mm20 and f.atr14 and f.adr20 and f.pivot_60 and f.rvol):
            return []  # a zero value (flat prices, no volume) makes the setup meaningless
        if not (f.mm50 > f.mm200 and last.close > f.mm50):
            return []
        if ctx.rs_rank is None or ctx.rs_rank < p["rs_rank_min"] or f.rvol < p["rvol_min"]:
            return []
        if not (last.close > f.pivot_60 and bars[-2].close <= f.pivot_60):
            return []  # breakout today, not an old one
        if last.close > f.mm20 + p["max_extension_atr_above_mm20"] * f.atr14:
            return []
        if f.consolidation_20 is None or f.consolidation_20 > p["consolidation_20_max"]:
            return []
        entry = f.pivot_60 * (1 + p["entry_buffer_pct"])
        p_max = f.pivot_60 * (1 + p["entry_max_pct_above_pivot"])
        lb = int(p["stop_lookback_low"])
        stop, basis, warn = _stop(
            entry, min(b.low for b in bars[-lb:]), f.atr14, ctx, p["stop_max_adr_mult"] * f.adr20 * entry
        )
        base_low = min(b.low for b in bars[-21:-1])
        measured = f.pivot_60 + (f.pivot_60 - base_low)  # height of the base carried over the pivot
        targets = _targets(p_max, ctx, [measured] + ([f.high_52w] if f.high_52w else []))
        return [
            Setup(
                self.name,
                SetupKind.BREAKOUT,
                entry,
                p_max,
                stop,
                basis,
                f.pivot_60,
                targets,
                "swing_2_6w",
                {
                    "pivot_60": f.pivot_60,
                    "rvol": f.rvol,
                    "rs_rank": ctx.rs_rank,
                    "consolidation_20": f.consolidation_20,
                    "mesure_base": measured,
                    "atr14": f.atr14,
                    "adr20": f.adr20,
                },
                warn,
            )
        ]


class D3RangeExpansion:
    """Wide-range up day out of a quiet base (a few days; time stop J+5)."""

    name = "d3_range_expansion"

    def __init__(self, params: dict[str, Any]) -> None:
        self.params = params

    def detect(self, bars: list[Bar], ctx: DetectorContext) -> list[Setup]:
        p = self.params
        if len(bars) < 70:
            return []
        f = compute_daily_features(bars)
        last, prev = bars[-1], bars[-2]
        if None in (f.atr14, f.adr20, f.rvol, f.ti65) or prev.close <= 0:
            return []
        if not (f.atr14 and f.adr20 and f.rvol and f.ti65):
            return []  # a zero value (flat prices, no volume) makes the setup meaningless
        if last.close / prev.close < p["close_ratio_min"] or f.rvol < p["rvol_min"] or last.volume <= prev.volume:
            return []
        rng = last.high - last.low
        if p.get("close_in_top_third", True) and rng > 0 and (last.close - last.low) / rng < 2 / 3:
            return []
        if f.ti65 < p["ti65_min"] or ctx.rs_rank is None or ctx.rs_rank < p["rs_rank_min"]:
            return []
        prior_high = max(b.high for b in bars[-21:-1])
        entry = last.high * 1.001
        p_max = last.high * (1 + p["entry_max_pct_above_high"])
        stop, basis, warn = _stop(entry, last.low, f.atr14, ctx, p["stop_max_adr_mult"] * f.adr20 * entry)
        level = prior_high if last.close > prior_high else last.low
        targets = _targets(p_max, ctx, [f.high_52w] if f.high_52w else [])
        return [
            Setup(
                self.name,
                SetupKind.BREAKOUT,
                entry,
                p_max,
                stop,
                basis,
                level,
                targets,
                "days",
                {"hausse_du_jour": last.close / prev.close - 1, "rvol": f.rvol, "ti65": f.ti65, "rs_rank": ctx.rs_rank},
                warn,
            )
        ]


class D4Pullback:
    """Pullback of a momentum leader to its short moving averages / a support zone that holds."""

    name = "d4_pullback"

    def __init__(self, params: dict[str, Any]) -> None:
        self.params = params

    def detect(self, bars: list[Bar], ctx: DetectorContext) -> list[Setup]:
        p = self.params
        if len(bars) < 60:
            return []
        f = compute_daily_features(bars)
        last, prev = bars[-1], bars[-2]
        if None in (f.mm10, f.mm20, f.mm50, f.atr14, f.adr20, f.rsi14):
            return []
        if not (f.mm10 and f.mm20 and f.mm50 and f.atr14 and f.adr20 and f.rsi14):
            return []  # a zero value (flat prices, no volume) makes the setup meaningless
        if ctx.rs_rank is None or ctx.rs_rank < p["rs_rank_min"] or last.close <= f.mm50:
            return []
        recent = bars[-20:]
        high20 = max(b.high for b in recent)
        pull = 1 - min(b.low for b in recent[[b.high for b in recent].index(high20) :]) / high20
        lo, hi = p["pullback_pct"]
        if not (lo <= pull <= hi):
            return []
        refs = {"mm10": f.mm10, "mm20": f.mm20}
        touched = [k for k in p["ma_refs"] if last.low <= refs[k] * 1.01 and last.close >= refs[k] * 0.99]
        if not touched:
            return []
        vol_before = [b.volume for b in bars[-60:-20]]
        pull_days = recent[[b.high for b in recent].index(high20) + 1 :] or recent[-1:]
        if vol_before and sum(b.volume for b in pull_days) / len(pull_days) > p["volume_dryup_ratio"] * (
            sum(vol_before) / len(vol_before)
        ):
            return []  # a pullback on heavy volume is distribution, not a pause
        rlo, rhi = p["rsi_range"]
        if not (rlo <= f.rsi14 <= rhi):
            return []
        if not (last.close > last.open or last.close > prev.close):
            return []  # « reprise confirmée »: the day closes up
        support_low = min(refs[k] for k in touched)
        pull_low = min(b.low for b in recent[-5:])
        entry = last.high * 1.002
        p_max = last.high * (1 + p["entry_max_pct_above_prev_high"])
        stop, basis, warn = _stop(entry, pull_low, f.atr14, ctx, p["stop_max_adr_mult"] * f.adr20 * entry)
        targets = _targets(p_max, ctx, [high20])
        return [
            Setup(
                self.name,
                SetupKind.PULLBACK,
                entry,
                p_max,
                stop,
                basis,
                min(support_low, pull_low),
                targets,
                "swing_2_6w",
                {"repli_pct": pull, "moyennes_touchees": touched, "rsi14": f.rsi14, "rs_rank": ctx.rs_rank},
                warn,
            )
        ]


class D7TrendQuality:
    """Swing « tendance + qualité » (docs/22 §22.4 B): a leader in a long uptrend (above a rising
    200-day average, 50 > 200) pulling back to its 50-day average and closing up. Quality
    (fundamentals) is scored in block F by the engine; the analyst target, when published, is passed
    as an extra exploitable target with its source."""

    name = "d7_trend_quality"

    def __init__(self, params: dict[str, Any]) -> None:
        self.params = params

    def detect(self, bars: list[Bar], ctx: DetectorContext) -> list[Setup]:
        p = self.params
        slope = int(p.get("mm200_slope_sessions", 20))
        if len(bars) < 200 + slope:
            return []
        f = compute_daily_features(bars)
        past = compute_daily_features(bars[:-slope])
        last, prev = bars[-1], bars[-2]
        if None in (f.mm50, f.mm200, past.mm200, f.atr14, f.adr20):
            return []
        if not (f.mm50 and f.mm200 and past.mm200 and f.atr14 and f.adr20):
            return []  # a zero value (flat prices, no volume) makes the setup meaningless
        if ctx.rs_rank is None or ctx.rs_rank < p["rs_rank_min"]:
            return []
        if not (last.close > f.mm200 and f.mm200 > past.mm200 and f.mm50 > f.mm200):
            return []
        near = float(p.get("near_mm50_pct", 0.03))
        if not (last.low <= f.mm50 * (1 + near) and last.close >= f.mm50 * 0.99):
            return []
        if not (last.close > last.open or last.close > prev.close):
            return []
        entry = last.high * 1.002
        p_max = last.high * (1 + p["entry_max_pct_above_high"])
        lb = int(p.get("stop_lookback_low", 10))
        stop, basis, warn = _stop(
            entry, min(b.low for b in bars[-lb:]), f.atr14, ctx, p["stop_max_adr_mult"] * f.adr20 * entry
        )
        targets = _targets(p_max, ctx, [f.high_52w] if f.high_52w else [])
        return [
            Setup(
                self.name,
                SetupKind.PULLBACK,
                entry,
                p_max,
                stop,
                basis,
                min(f.mm50, min(b.low for b in bars[-lb:])),
                targets,
                "semaines",
                {"mm50": f.mm50, "mm200": f.mm200, "pente_mm200": f.mm200 / past.mm200 - 1, "rs_rank": ctx.rs_rank},
                warn,
            )
        ]


class D8BuzzEarly:
    """Opportunity (docs/26 S3): retail chatter accelerates before any publication and before the price.
    Fires only when the radar flagged the value (``ctx.buzz``); the entry is a limit just above the last
    close, the stop under the recent structure, the target the next exploitable resistance — the usual
    admission (net reward/risk), sizing and caps decide. Short horizon, time stop."""

    name = "d8_buzz_precoce"

    def __init__(self, params: dict[str, Any]) -> None:
        self.params = params

    def detect(self, bars: list[Bar], ctx: DetectorContext) -> list[Setup]:
        p = self.params
        if not ctx.buzz or len(bars) < 30:
            return []
        f = compute_daily_features(bars)
        if not (f.atr14 and f.adr20):
            return []
        last = bars[-1]
        entry = last.close * (1 + float(p.get("entry_buffer_pct", 0.003)))
        p_max = last.close * (1 + float(p.get("entry_max_pct", 0.015)))
        low10 = min(b.low for b in bars[-10:])
        stop, basis, warn = _stop(entry, low10, f.atr14, ctx, float(p.get("stop_max_adr_mult", 2.5)) * f.adr20 * entry)
        high60 = max(b.high for b in bars[-60:])
        targets = _targets(p_max, ctx, [high60] if high60 > p_max else [])
        return [
            Setup(
                self.name,
                SetupKind.BREAKOUT,
                entry,
                p_max,
                stop,
                basis,
                low10,
                targets,
                str(p.get("horizon", "days")),
                {"radar": "buzz précoce", "plus_bas_10_seances": low10},
                warn,
            )
        ]


DETECTORS = {c.name: c for c in (D2Breakout, D3RangeExpansion, D4Pullback, D7TrendQuality, D8BuzzEarly)}


def run_detectors(params: dict[str, Any], bars: list[Bar], ctx: DetectorContext) -> list[Setup]:
    out: list[Setup] = []
    for name, cls in DETECTORS.items():
        p = params.get(name) or {}
        if p.get("enabled", False):
            out.extend(cls(p).detect(bars, ctx))
    return out
