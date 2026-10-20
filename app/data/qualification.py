"""Provider qualification analysis (docs/04 §4.3 bis, A0.7). Pure functions over recorded rows.

The script ``scripts/qualify_providers.py`` records observations; this module turns them into
the numbers of the provider sheets: measured delay (``received_at − market_timestamp``), price and
volume discrepancies between providers and versus the Saxo interface reference, gaps and
reconnection behaviour.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Observation:
    provider: str
    symbol: str
    isin: str
    received_at: datetime
    market_timestamp: datetime | None
    last: float | None
    volume_cum: int | None
    data_status: str
    perimeter: str


@dataclass(frozen=True)
class Reference:
    """Value read by the user in the Saxo interface at ``at`` (tests/fixtures/qualification/)."""

    isin: str
    at: datetime
    last: float
    volume_cum: int | None


@dataclass
class DelayStats:
    provider: str
    n: int
    median_s: float | None
    p90_s: float | None
    max_s: float | None
    unavailable: int
    statuses: dict[str, int] = field(default_factory=dict)


def delay_stats(obs: list[Observation]) -> list[DelayStats]:
    by: dict[str, list[Observation]] = {}
    for o in obs:
        by.setdefault(o.provider, []).append(o)
    out = []
    for p, rows in sorted(by.items()):
        delays = [(o.received_at - o.market_timestamp).total_seconds() for o in rows if o.market_timestamp is not None]
        statuses: dict[str, int] = {}
        for o in rows:
            statuses[o.data_status] = statuses.get(o.data_status, 0) + 1
        if delays:
            delays.sort()
            out.append(
                DelayStats(
                    p,
                    len(delays),
                    statistics.median(delays),
                    delays[min(len(delays) - 1, int(0.9 * len(delays)))],
                    delays[-1],
                    len(rows) - len(delays),
                    statuses,
                )
            )
        else:
            out.append(DelayStats(p, 0, None, None, None, len(rows), statuses))
    return out


@dataclass(frozen=True)
class Discrepancy:
    provider: str
    isin: str
    at: datetime
    reference_last: float
    provider_last: float | None
    price_diff_pct: float | None
    reference_volume: int | None
    provider_volume: int | None
    volume_ratio: float | None
    lag_s: float | None


def nearest_observation(
    obs: list[Observation], provider: str, isin: str, at: datetime, window_s: float = 120
) -> Observation | None:
    best: Observation | None = None
    for o in obs:
        if o.provider != provider or o.isin != isin or o.market_timestamp is None:
            continue
        d = abs((o.market_timestamp - at).total_seconds())
        if d <= window_s and (best is None or d < abs((best.market_timestamp - at).total_seconds())):  # type: ignore[operator]
            best = o
    return best


def compare_to_reference(obs: list[Observation], refs: list[Reference], providers: list[str]) -> list[Discrepancy]:
    out = []
    for ref in refs:
        for p in providers:
            o = nearest_observation(obs, p, ref.isin, ref.at)
            if o is None:
                out.append(Discrepancy(p, ref.isin, ref.at, ref.last, None, None, ref.volume_cum, None, None, None))
                continue
            diff = (o.last / ref.last - 1) * 100 if o.last else None
            ratio = (o.volume_cum / ref.volume_cum) if o.volume_cum and ref.volume_cum else None
            lag = (o.market_timestamp - ref.at).total_seconds() if o.market_timestamp else None
            out.append(
                Discrepancy(p, ref.isin, ref.at, ref.last, o.last, diff, ref.volume_cum, o.volume_cum, ratio, lag)
            )
    return out


def volume_perimeter_hint(discrepancies: list[Discrepancy]) -> dict[str, float | None]:
    """Median volume ratio provider/Saxo per provider: ≈1 → primary book, <1 → partial (Cboe)."""
    by: dict[str, list[float]] = {}
    for d in discrepancies:
        if d.volume_ratio is not None:
            by.setdefault(d.provider, []).append(d.volume_ratio)
    return {p: (statistics.median(v) if v else None) for p, v in by.items()}


@dataclass(frozen=True)
class GapSummary:
    provider: str
    symbol: str
    bars: int
    incomplete_bars: int
    missing_minutes: int


def gap_summary(bars: list[tuple[str, str, datetime, bool]]) -> list[GapSummary]:
    """``bars``: (provider, symbol, minute_start, complete). Missing minutes = holes between bars."""
    by: dict[tuple[str, str], list[tuple[datetime, bool]]] = {}
    for p, s, t, c in bars:
        by.setdefault((p, s), []).append((t, c))
    out = []
    for (p, s), rows in sorted(by.items()):
        rows.sort()
        missing = 0
        for (a, _), (b, _) in zip(rows, rows[1:], strict=False):
            missing += max(0, int((b - a).total_seconds() // 60) - 1)
        out.append(GapSummary(p, s, len(rows), sum(1 for _, c in rows if not c), missing))
    return out


def render_report(
    day: str,
    delays: list[DelayStats],
    discrepancies: list[Discrepancy],
    gaps: list[GapSummary],
    events: list[dict[str, object]],
) -> str:
    lines = [
        f"# Qualification des providers — séance du {day}",
        "",
        "## Délai mesuré (received_at − market_timestamp)",
        "",
        "| Provider | n | médiane (s) | p90 (s) | max (s) | indisponibles | statuts |",
        "|---|---|---|---|---|---|---|",
    ]
    for d in delays:
        med = f"{d.median_s:.0f}" if d.median_s is not None else "—"
        p90 = f"{d.p90_s:.0f}" if d.p90_s is not None else "—"
        mx = f"{d.max_s:.0f}" if d.max_s is not None else "—"
        lines.append(f"| {d.provider} | {d.n} | {med} | {p90} | {mx} | {d.unavailable} | {d.statuses} |")
    lines += [
        "",
        "## Écarts vs interface Saxo (relevés utilisateur)",
        "",
        "| Provider | ISIN | heure réf. | cours réf. | cours provider | écart % | vol. réf. | vol. provider | ratio vol. | décalage (s) |",  # noqa: E501
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for x in discrepancies:
        pl = f"{x.provider_last:.3f}" if x.provider_last is not None else "—"
        pd = f"{x.price_diff_pct:+.2f}" if x.price_diff_pct is not None else "—"
        vr = f"{x.volume_ratio:.2f}" if x.volume_ratio is not None else "—"
        lag = f"{x.lag_s:.0f}" if x.lag_s is not None else "—"
        lines.append(
            f"| {x.provider} | {x.isin} | {x.at:%H:%M:%S} | {x.reference_last:.3f} | {pl} | {pd} | {x.reference_volume or '—'} | {x.provider_volume or '—'} | {vr} | {lag} |"  # noqa: E501
        )
    hints = volume_perimeter_hint(discrepancies)
    if hints:
        lines += [
            "",
            "Ratio de volume médian provider / Saxo : "
            + ", ".join(f"{p} = {v:.2f}" if v is not None else f"{p} = —" for p, v in hints.items())
            + " (≈ 1 → carnet primaire ; < 1 → périmètre partiel, ex. Cboe).",
        ]
    lines += [
        "",
        "## Barres 1 min (flux temps réel) : trous et barres incomplètes",
        "",
        "| Provider | Symbole | barres | incomplètes | minutes manquantes |",
        "|---|---|---|---|---|",
    ]
    for g in gaps:
        lines.append(f"| {g.provider} | {g.symbol} | {g.bars} | {g.incomplete_bars} | {g.missing_minutes} |")
    lines += ["", "## Événements de connexion", ""]
    lines += [f"- {e.get('at')} : {e.get('event')} {e.get('error', '')}" for e in events] or ["- aucun"]
    lines += [
        "",
        "> Le rattrapage des ticks manqués par l'endpoint dédié du flux n'est retenu que s'il est prouvé ici (ADR-002).",  # noqa: E501
    ]
    return "\n".join(lines) + "\n"
