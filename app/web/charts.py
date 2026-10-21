"""Server-side SVG charts (docs/17: no JavaScript). Pure string building from stored numbers."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from html import escape

COLORS = {
    "main": "#0369a1",
    "mm50": "#f59e0b",
    "mm200": "#7c3aed",
    "level": "#e11d48",
    "pos": "#10b981",
    "neg": "#f43f5e",
}


MONTHS = ("janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc.")


def _num(v: float, decimals: int) -> str:
    return f"{v:,.{decimals}f}".replace(",", "\u202f")


def _month_label(key: str) -> str:
    """'2026-10' -> 'oct. 26'; any other key is returned unchanged."""
    try:
        y, m = key.split("-")
        return f"{MONTHS[int(m) - 1]} {y[2:]}"
    except (ValueError, IndexError):
        return key


def _scale(v: float, lo: float, hi: float, a: float, b: float) -> float:
    return a if hi == lo else a + (v - lo) * (b - a) / (hi - lo)


def line_chart(
    series: dict[str, Sequence[tuple[date, float | None]]],
    levels: dict[str, float] | None = None,
    width: int = 720,
    height: int = 220,
    unit: str = "€",
) -> str:
    """Multi-series line chart sharing the x axis of the first series. ``None`` values break the line."""
    first = next(iter(series.values()), [])
    if len(first) < 2:
        return '<p class="text-sm text-slate-500">Pas assez de points pour un graphique.</p>'
    days = [d for d, _ in first]
    vals = [v for s in series.values() for _, v in s if v is not None] + list((levels or {}).values())
    if not vals:
        return '<p class="text-sm text-slate-500">Aucune valeur.</p>'
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.06 or abs(hi) * 0.02 or 1.0
    lo, hi = lo - pad, hi + pad
    decimals = 0 if hi - lo >= 50 else 2
    left, right, top, bottom = 64, width - 8, 10, height - 22
    xi = {d: _scale(i, 0, len(days) - 1, left, right) for i, d in enumerate(days)}
    parts = [
        f'<svg viewBox="0 0 {width} {height}" class="w-full h-auto" role="img" aria-label="graphique">',
        f'<rect x="{left}" y="{top}" width="{right - left}" height="{bottom - top}" fill="none" stroke="currentColor" stroke-opacity=".12"/>',  # noqa: E501
    ]
    for frac in (0, 0.5, 1):
        v = lo + (hi - lo) * frac
        y = _scale(v, lo, hi, bottom, top)
        parts.append(
            f'<line x1="{left}" x2="{right}" y1="{y:.1f}" y2="{y:.1f}" stroke="currentColor" stroke-opacity=".08"/>'
        )
        parts.append(
            f'<text x="{left - 6}" y="{y + 4:.1f}" text-anchor="end" font-size="10" fill="currentColor" fill-opacity=".55">{_num(v, decimals)}</text>'  # noqa: E501
        )
    for i, anchor in ((0, "start"), (len(days) // 2, "middle"), (len(days) - 1, "end")):
        parts.append(
            f'<text x="{xi[days[i]]:.1f}" y="{height - 6}" text-anchor="{anchor}" font-size="10" fill="currentColor" fill-opacity=".55">{days[i].strftime("%d/%m/%y")}</text>'  # noqa: E501
        )
    for name, value in (levels or {}).items():
        y = _scale(value, lo, hi, bottom, top)
        parts.append(
            f'<line x1="{left}" x2="{right}" y1="{y:.1f}" y2="{y:.1f}" stroke="{COLORS["level"]}" stroke-dasharray="4 3" stroke-width="1"/>'  # noqa: E501
        )
        parts.append(
            f'<text x="{right - 4}" y="{y - 4:.1f}" text-anchor="end" font-size="10" fill="{COLORS["level"]}">{escape(name)} {value:.2f}</text>'  # noqa: E501
        )
    for idx, (name, s) in enumerate(series.items()):
        color = COLORS.get(name, COLORS["main"] if idx == 0 else "#64748b")
        seg: list[str] = []
        segments: list[list[str]] = []
        for d, v in s:
            if v is None or d not in xi:
                if seg:
                    segments.append(seg)
                seg = []
                continue
            seg.append(f"{xi[d]:.1f},{_scale(v, lo, hi, bottom, top):.1f}")
        if seg:
            segments.append(seg)
        for pts in segments:
            parts.append(
                f'<polyline points="{" ".join(pts)}" fill="none" stroke="{color}" stroke-width="{2 if idx == 0 else 1.3}" stroke-linejoin="round"/>'  # noqa: E501
            )
    parts.append("</svg>")
    return "".join(parts)


def bars(values: dict[str, float], width: int = 720, height: int = 160) -> str:
    """Signed bar chart (monthly P&L); keys 'YYYY-MM' are shown as 'oct. 26'."""
    if not values:
        return '<p class="text-sm text-slate-500">Aucune donnée.</p>'
    m = max(abs(v) for v in values.values()) or 1.0
    n = len(values)
    left, right, top, bottom = 8, width - 8, 18, height - 22
    zero = (top + bottom) / 2
    slot = min((right - left) / n, 72.0)  # a lone month stays a bar, not a block
    start = left + ((right - left) - slot * n) / 2
    bw = slot * 0.7
    parts = [f'<svg viewBox="0 0 {width} {height}" class="w-full h-auto" role="img" aria-label="barres">']
    parts.append(f'<line x1="{left}" x2="{right}" y1="{zero}" y2="{zero}" stroke="currentColor" stroke-opacity=".2"/>')
    for i, (k, v) in enumerate(values.items()):
        h = abs(v) / m * (bottom - top) / 2
        x = start + i * slot + (slot - bw) / 2
        y = zero - h if v >= 0 else zero
        color = COLORS["pos"] if v >= 0 else COLORS["neg"]
        cx = x + bw / 2
        label_y = y - 4 if v >= 0 else zero + h + 11
        parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{max(h, 1):.1f}" rx="2" fill="{color}"><title>{escape(_month_label(k))} : {_num(v, 0)} €</title></rect>'  # noqa: E501
        )
        parts.append(
            f'<text x="{cx:.1f}" y="{label_y:.1f}" text-anchor="middle" font-size="10" fill="{color}">{_num(v, 0)} €</text>'  # noqa: E501
        )
        parts.append(
            f'<text x="{cx:.1f}" y="{height - 6}" text-anchor="middle" font-size="10" fill="currentColor" fill-opacity=".55">{escape(_month_label(k))}</text>'  # noqa: E501
        )
    parts.append("</svg>")
    return "".join(parts)
