"""Outcome of one proposal on official daily bars after its creation (docs/18 §18.4, docs/12 §12.2).

Pure and deterministic. Conservative by construction:
- no look-ahead: the creation session is used only for a proposal issued before the open;
- limit entry at the maximal entry price, filled at min(open, limit) when the low reaches it;
- slipped stop ``stop × (1 − slippage)``; a bar touching stop and target counts as stopped
  (``review.same_bar_rule: stop_first``), including the fill bar;
- a missing session on the path makes the outcome ``unavailable`` (never estimated).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

FINAL_STATUSES = frozenset({"not_filled", "stopped", "target", "horizon"})


@dataclass(frozen=True)
class DayBar:
    day: date
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class OutcomeInput:
    created_day: date  # session date (Paris) of creation
    created_before_open: bool
    entry_price: float | None  # maximal entry price (limit); None → reference = first open, no trade
    stop: float | None
    target: float | None
    slippage_pct: float
    validity_sessions: int
    horizon_sessions: int
    expires_day: date | None = None
    cost_per_share: float | None = None  # round trip, in the instrument currency


@dataclass
class Outcome:
    status: str  # pending | running | not_filled | stopped | target | horizon | unavailable
    final: bool = False
    ref_price: float | None = None
    entry_day: date | None = None
    entry_price: float | None = None
    exit_day: date | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    r_gross: float | None = None
    r_net: float | None = None
    costs_counted: bool = False
    ret_pct: float | None = None
    mfe_pct: float | None = None
    mae_pct: float | None = None
    mfe_r: float | None = None
    mae_r: float | None = None
    ret_1d: float | None = None
    ret_5d: float | None = None
    ret_20d: float | None = None
    last_bar_day: date | None = None
    note: str | None = None


def _forward(ref: float, closes: list[float], k: int) -> float | None:
    return closes[k - 1] / ref - 1 if len(closes) >= k and ref else None


def evaluate(inp: OutcomeInput, sessions: list[date], bars: dict[date, DayBar]) -> Outcome:
    """``sessions``: the instrument venue's sessions from the creation day up to the last session
    whose official close is expected (yesterday or today after the close); ``bars`` by day."""
    path = [d for d in sessions if d > inp.created_day or (inp.created_before_open and d == inp.created_day)]
    if not path:
        return Outcome("pending")
    # Stop at the first missing session: everything after it cannot be evaluated.
    avail: list[DayBar] = []
    missing: date | None = None
    for d in path:
        b = bars.get(d)
        if b is None:
            missing = d
            break
        avail.append(b)

    out = Outcome("running")
    if not avail:
        return Outcome("unavailable", note=f"séance manquante {missing:%d/%m/%Y}" if missing else None)
    out.last_bar_day = avail[-1].day
    ref = inp.entry_price if inp.entry_price else avail[0].open
    out.ref_price = ref
    closes = [b.close for b in avail]
    out.ret_1d, out.ret_5d, out.ret_20d = (_forward(ref, closes, k) for k in (1, 5, 20))

    def incomplete(reason_day: date | None) -> Outcome:
        if reason_day is not None:
            out.status, out.note = "unavailable", f"séance manquante {reason_day:%d/%m/%Y}"
        return out

    if not inp.entry_price:
        # No entry level: forward returns only, final once the horizon is observed.
        window = avail[: inp.horizon_sessions]
        out.mfe_pct = max(b.high for b in window) / ref - 1
        out.mae_pct = min(b.low for b in window) / ref - 1
        if len(avail) >= inp.horizon_sessions:
            last = avail[inp.horizon_sessions - 1]
            out.status, out.final, out.exit_reason = "horizon", True, "horizon"
            out.exit_day, out.exit_price, out.ret_pct = last.day, last.close, last.close / ref - 1
            return out
        return incomplete(missing)

    # 1) simulated limit entry
    fill_idx: int | None = None
    validity = [
        i
        for i, d in enumerate(path[: inp.validity_sessions])
        if inp.expires_day is None or d <= inp.expires_day
    ]
    for i in validity:
        if i >= len(avail):
            return incomplete(missing)
        if avail[i].low <= inp.entry_price:
            fill_idx = i
            break
    if fill_idx is None:
        window_over = len(path) >= inp.validity_sessions or (
            inp.expires_day is not None and path[-1] >= inp.expires_day
        )
        if not window_over:
            return out  # still within the validity window
        out.status, out.final, out.note = "not_filled", True, "prix d'entrée jamais atteint"
        return out
    fill_bar = avail[fill_idx]
    fill = min(fill_bar.open, inp.entry_price)
    out.entry_day, out.entry_price = fill_bar.day, fill

    stop_exec = inp.stop * (1 - inp.slippage_pct) if inp.stop else None
    risk = fill - stop_exec if stop_exec is not None and fill > stop_exec else None
    hi, lo = max(fill_bar.close, fill), fill_bar.low  # the fill bar's high may predate the fill

    # 2) exits, stop first; the horizon bar is the ``horizon_sessions``-th session after the fill
    last_idx = fill_idx + inp.horizon_sessions
    exit_px: float | None = None
    stop, target = inp.stop, inp.target
    j = fill_idx
    while j <= last_idx:
        if j >= len(avail):
            break
        b = avail[j]
        if j > fill_idx:
            hi, lo = max(hi, b.high), min(lo, b.low)
        if stop is not None and b.low <= stop:
            px = min(b.open, stop) if j > fill_idx else min(fill, stop)
            exit_px, out.status, out.exit_reason = px * (1 - inp.slippage_pct), "stopped", "stop"
        elif target is not None and (b.high >= target if j > fill_idx else b.close >= target):
            exit_px = max(b.open, target) if j > fill_idx else target
            out.status, out.exit_reason = "target", "objectif"
        elif j == last_idx:
            exit_px, out.status, out.exit_reason = b.close, "horizon", "horizon"
        if exit_px is not None:
            out.exit_day, out.exit_price, out.final = b.day, exit_px, True
            break
        j += 1

    out.mfe_pct, out.mae_pct = hi / fill - 1, lo / fill - 1
    if risk:
        out.mfe_r, out.mae_r = (hi - fill) / risk, (lo - fill) / risk
    if exit_px is None:
        out.status = "running"
        return incomplete(missing)
    out.ret_pct = exit_px / fill - 1
    if risk:
        out.r_gross = (exit_px - fill) / risk
        if inp.cost_per_share is not None:
            out.r_net, out.costs_counted = (exit_px - fill - inp.cost_per_share) / risk, True
        else:
            out.r_net = out.r_gross
    return out
