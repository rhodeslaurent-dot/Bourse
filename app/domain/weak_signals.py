"""docs/26 — public weak signals: robust anomaly scores on attention (Wikipedia page views), volume and press
coverage, and the radar labels. Pure functions — no I/O, no LLM. A weak signal is never a trigger: it only
yields a caution, a corroboration or an idea for the normal engine."""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

PUMP = "emballement"
ATTENTION_NO_NEWS = "attention_sans_nouvelle"
VOLUME_QUIET = "volume_sans_mouvement"
ATTENTION_NEWS = "attention_corroboree"
BUZZ_EARLY = "buzz_precoce"

LABELS = {
    PUMP: "emballement non corroboré",
    ATTENTION_NO_NEWS: "attention anormale sans nouvelle publiée",
    VOLUME_QUIET: "volume anormal sans mouvement de prix ni nouvelle",
    ATTENTION_NEWS: "attention anormale avec nouvelle publiée",
    BUZZ_EARLY: "les échanges des particuliers s'emballent avant toute publication et avant le prix",
}


@dataclass(frozen=True)
class RadarParams:
    baseline_days: int = 60
    recent_days: int = 3
    min_history_days: int = 30
    min_mad: float = 0.15  # floor on the MAD of log(1 + views): a page read 3 times a day is not « 10 σ » at 9
    attention_z: float = 2.0
    pump_z: float = 3.0
    pump_rvol: float = 3.0
    volume_rvol_min: float = 2.5
    quiet_move_atr: float = 0.5
    idea_min_rs: float = 70.0
    social_z: float = 2.5  # forums and social networks (docs/26 S3)
    velocity_ratio: float = 3.0  # messages of the last hours ÷ the usual rate (no long history needed)
    velocity_min_messages: int = 5
    early_move_atr: float = 1.5  # « the price has not moved yet »: |5-session change| < 1.5 ATR


def robust_z(values: list[float], p: RadarParams, log: bool = True) -> float | None:
    """z of the mean of the last ``recent_days`` values against the median / MAD of the ``baseline_days``
    before them (the recent window is excluded from the baseline). None when the history is too short."""
    if len(values) < p.min_history_days + p.recent_days:
        return None
    xs = [math.log1p(max(0.0, v)) for v in values] if log else list(values)
    recent = xs[-p.recent_days :]
    base = xs[-(p.recent_days + p.baseline_days) : -p.recent_days]
    if len(base) < p.min_history_days:
        return None
    med = statistics.median(base)
    mad = statistics.median(abs(x - med) for x in base) * 1.4826
    return (statistics.fmean(recent) - med) / max(mad, p.min_mad if log else 1e-12)


def relative_volume(volumes: list[float | None], window: int = 60) -> float | None:
    """Last session's volume ÷ median of the ``window`` sessions before it; None without enough history or
    without the last volume (never a zero by default)."""
    if len(volumes) < window // 2 + 1 or not volumes[-1]:
        return None
    base = [v for v in volumes[-(window + 1) : -1] if v is not None and v > 0]
    if len(base) < window // 2:
        return None
    med = statistics.median(base)
    last = volumes[-1]
    return last / med if med > 0 and last is not None else None


@dataclass(frozen=True)
class RadarInput:
    attention_z: float | None
    rvol: float | None
    move_atr: float | None  # |close − previous close| ÷ ATR14
    news_recent: int  # attached news items within ``news_hours``
    small_cap: bool
    rs_rank: float | None
    social_z: float | None = None  # highest z of the forum / social counts
    move_5d_atr: float | None = None  # |close − close 5 sessions ago| ÷ ATR14
    velocity: float | None = None  # recent rate ÷ usual rate (velocity())
    recent_messages: int = 0


@dataclass(frozen=True)
class RadarLabel:
    label: str
    idea: bool  # worth a look by the normal engine (never a proposal by itself)
    text: str


def velocity(
    times: list[float],
    now: float,
    window_h: float = 2.0,
    base_h: float = 24.0,
    usual_per_hour: float | None = None,
) -> tuple[int, float | None]:
    """(messages of the last ``window_h`` hours, their rate ÷ the usual rate) from POSIX timestamps.

    The usual rate is the one of the ``base_h`` hours before when the timestamps reach back that far
    (one page read is enough); otherwise ``usual_per_hour`` (from the stored daily counts) is used; without
    either, the ratio is unknown (None) — a busy forum whose page only covers the last hour is never read as
    an acceleration."""
    if not times:
        return 0, None
    w0, b0 = now - window_h * 3600, now - (window_h + base_h) * 3600
    recent = sum(1 for x in times if w0 <= x <= now)
    if min(times) <= b0:
        base = sum(1 for x in times if b0 <= x < w0)
        if base == 0:
            return recent, (float("inf") if recent else None)
        return recent, (recent / window_h) / (base / base_h)
    if usual_per_hour is not None and usual_per_hour > 0:
        return recent, (recent / window_h) / usual_per_hour
    return recent, None


def label(inp: RadarInput, p: RadarParams) -> RadarLabel | None:
    """The strongest label of the day, or None. Order: pump guard, quiet volume, attention without news,
    corroborated attention."""
    z, rv, sz = inp.attention_z, inp.rvol, inp.social_z
    no_news = inp.news_recent == 0
    top = max((x for x in (z, sz) if x is not None), default=None)
    if top is not None and top >= p.pump_z and inp.small_cap and no_news and rv is not None and rv >= p.pump_rvol:
        return RadarLabel(
            PUMP, False, f"{LABELS[PUMP]} : attention z {top:.1f}, volume × {rv:.1f}, petite capitalisation"
        )
    fast = (
        inp.velocity is not None and inp.velocity >= p.velocity_ratio and inp.recent_messages >= p.velocity_min_messages
    )
    if (
        ((sz is not None and sz >= p.social_z) or fast)
        and no_news
        and (inp.move_5d_atr is None or inp.move_5d_atr < p.early_move_atr)
    ):
        vol = f", volume × {rv:.1f}" if rv is not None and rv >= 1.5 else ""
        if fast and inp.velocity is not None:
            pace = (
                "aucun message les 24 h précédentes" if inp.velocity == float("inf") else f"rythme × {inp.velocity:.1f}"
            )
            how = f"{inp.recent_messages} messages récents, {pace}"
        else:
            how = f"z {sz:.1f}" if sz is not None else "échanges anormaux"
        return RadarLabel(BUZZ_EARLY, True, f"{LABELS[BUZZ_EARLY]} ({how}{vol}) : mouvement possible, à examiner")
    if no_news and rv is not None and rv >= p.volume_rvol_min and inp.move_atr is not None:
        if inp.move_atr < p.quiet_move_atr:
            return RadarLabel(VOLUME_QUIET, False, f"{LABELS[VOLUME_QUIET]} : volume × {rv:.1f}")
    if z is not None and z >= p.attention_z:
        if no_news:
            return RadarLabel(ATTENTION_NO_NEWS, False, f"{LABELS[ATTENTION_NO_NEWS]} : z {z:.1f}")
        idea = inp.rs_rank is not None and inp.rs_rank >= p.idea_min_rs
        return RadarLabel(ATTENTION_NEWS, idea, f"{LABELS[ATTENTION_NEWS]} : z {z:.1f}")
    return None
