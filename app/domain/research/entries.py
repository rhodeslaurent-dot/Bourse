"""Signals → orders, lockout, matched controls and forward excess returns (docs/31 §1.1, §3.2). Pure.

Entry E_open(x): a buy limit at ``p_max = close_t + x · ATR14_t`` working from the open of ``t + 1`` for
``validity`` sessions (never at the close of ``t``: unknown before the auction). Exit models X1–X8 place their
stop from the executed price (recalculation at the executed price, CLAUDE.md rule 7); the invalidation level of
the order (an open at or below it cancels the entry) is the same formula applied to ``p_max``.

Matched control: every eligible instrument of the same venue group, day and stratum (liquidity tercile ×
σ20 tercile × T04 state) simulated with the same entry and exit — the mean over the stratum, the instrument
itself excluded. It is the limit of the plan's 20 random draws (no sampling noise) and costs one simulation
of the eligible panel per entry/exit combination.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import numpy as np

from app.domain.research.exits import Bars, ExitSpec, Orders
from app.domain.research.panel import F64, I8, I64, Bool, Features, Packing, Panel, pack, shift, unpack

N_STRATA = 18


@dataclass(frozen=True)
class ExitModel:
    code: str  # X1…X8 (docs/31 §2.1)
    spec: ExitSpec
    stop_kind: Literal["sigma", "atr"]
    stop_k: float
    h_ref: int  # horizon of the σ stops and of the R_ref unit
    cond_r: float = 0.0  # X6: MFE threshold in R_ref units at the conditional session


def exit_model(code: str, h: int, max_hold: int | None = None) -> ExitModel:
    """The exit models of the plan, for the primary horizon ``h``."""
    long = max_hold or 60
    if code == "X1":
        return ExitModel(code, ExitSpec("X1", h, stop_active=False), "sigma", 3.0, h)
    if code == "X2":
        return ExitModel(code, ExitSpec("X2", h), "sigma", 2.0, h)
    if code == "X3":
        return ExitModel(code, ExitSpec("X3", h), "sigma", 3.0, h)
    if code == "X4":
        return ExitModel(code, ExitSpec("X4", long, trail_atr=3.0), "atr", 3.0, h)
    if code == "X5":
        return ExitModel(code, ExitSpec("X5", long, ma20_closes=2), "sigma", 3.0, h)
    if code == "X6":
        return ExitModel(code, ExitSpec("X6", h, cond_session=5), "sigma", 3.0, h, cond_r=0.5)
    if code == "X7":
        return ExitModel(code, ExitSpec("X7", h, target_r=2.0), "atr", 2.5, h)
    if code == "X8":
        return ExitModel(code, ExitSpec("X8", long), "atr", 2.5, h)
    raise ValueError(f"unknown exit model {code}")


def unit_fraction(sigma: F64, h: int) -> F64:
    """The R_ref unit as a fraction of the notional: 2 · σ20 · √h."""
    return 2.0 * sigma * math.sqrt(h)


def lockout(sig: Bool, pk: Packing, t_lo: int, t_hi: int, sessions: int) -> tuple[I64, I64]:
    """Signals (t, i) with ``t_lo ≤ t ≤ t_hi``, at most one per instrument every ``sessions`` own sessions
    (a fixed lockout, independent of the outcome)."""
    m = sig.copy()
    m[: max(t_lo, 0)] = False
    m[t_hi + 1 :] = False
    ts, cols = np.nonzero(m)
    ts, cols = ts.astype(np.int64), cols.astype(np.int64)
    if sessions <= 1 or ts.size == 0:
        return ts, cols
    pos = pk.pos[ts, cols]
    order = np.lexsort((pos, cols))
    keep = np.zeros(ts.size, dtype=bool)
    last_col, nxt = -1, -1
    for k in order.tolist():
        c, ps = int(cols[k]), int(pos[k])
        if c != last_col:
            last_col, nxt = c, -1
        if ps >= nxt:
            keep[k] = True
            nxt = ps + sessions
    return ts[keep], cols[keep]


def make_orders(
    p: Panel,
    f: Features,
    ts: I64,
    cols: I64,
    model: ExitModel,
    validity: int,
    cap_atr: float | None = 1.0,
    p_max: F64 | None = None,
) -> Orders:
    """Orders for signals (t, i): limit = ``p_max`` when given (D2x caps), else close + cap_atr · ATR14."""
    close, atr, sig = p.close[ts, cols], f.atr14[ts, cols], f.sigma20[ts, cols]
    limit = p_max if p_max is not None else close + float(cap_atr or 0.0) * atr
    n = ts.size
    if model.stop_kind == "sigma":
        mult = np.exp(-model.stop_k * sig * math.sqrt(model.h_ref))
        off = np.zeros(n)
        cancel = limit * mult
    else:
        mult = np.ones(n)
        off = model.stop_k * atr
        cancel = limit - off
    cond = model.cond_r * unit_fraction(sig, model.h_ref) if model.cond_r > 0 else np.full(n, np.nan)
    return Orders(
        cols, f.pk.pos[ts, cols], limit.astype(np.float64), cancel, mult, off, np.full(n, np.nan),
        np.full(n, np.nan), cond, validity,
    )  # fmt: skip


def absolute_orders(
    inst: I64, sig_pos: I64, limit: F64, stop: F64, target: F64, validity: int, cancel_below: F64 | None = None
) -> Orders:
    """Production brackets with absolute stop and target (run-2 conventions)."""
    n = inst.size
    zero = np.zeros(n)
    return Orders(
        inst, sig_pos, limit, stop if cancel_below is None else cancel_below, zero, -stop, zero, target,
        np.full(n, np.nan), validity,
    )  # fmt: skip


# --- matched controls ----------------------------------------------------------------------------


def stratum_sums(values: F64, stratum: I8) -> tuple[F64, F64]:
    """Per (day, stratum): sum and count of the finite values."""
    t_len = values.shape[0]
    ok = np.isfinite(values) & (stratum >= 0)
    key = (np.arange(t_len)[:, None] * N_STRATA + stratum.astype(np.int64))[ok]
    s = np.bincount(key, weights=values[ok], minlength=t_len * N_STRATA).reshape(t_len, N_STRATA)
    c = np.bincount(key, minlength=t_len * N_STRATA).astype(np.float64).reshape(t_len, N_STRATA)
    return s, c


def control_mean(values: F64, stratum: I8) -> F64:
    """Mean of the same day and stratum, the instrument itself excluded (NaN when no other member)."""
    s, c = stratum_sums(values, stratum)
    t_len = values.shape[0]
    st = np.where(stratum >= 0, stratum, 0).astype(np.int64)
    rows = np.broadcast_to(np.arange(t_len)[:, None], values.shape)
    ss, cc = s[rows, st], c[rows, st]
    own = np.isfinite(values)
    num = np.where(own, ss - np.nan_to_num(values), ss)
    den = np.where(own, cc - 1, cc)
    with np.errstate(invalid="ignore", divide="ignore"):
        out = np.where((den > 0) & (stratum >= 0), num / den, np.nan)
    return out


def ew_mean(values: F64, mask: Bool) -> F64:
    """Equal-weighted mean of each day over the mask (the eligible benchmark)."""
    ok = mask & np.isfinite(values)
    n = ok.sum(axis=1)
    s = np.where(ok, values, 0.0).sum(axis=1)
    return np.where(n > 0, s / np.maximum(n, 1), np.nan)


# --- forward excess returns (total return, from the open of t+1) ------------------------------------


def forward_return(p: Panel, f: Features, h: int) -> F64:
    """Total return from the adjusted open of ``t + 1`` to the adjusted close of ``t + h`` (own sessions)."""
    pk = f.pk
    t_len = p.shape[0]
    c = pack(p.close, pk)
    fac = pack(f.adj_factor, pk)
    adj_c = c * fac
    adj_o = pack(p.open, pk) * fac
    with np.errstate(invalid="ignore", divide="ignore"):
        return unpack(shift(adj_c, -h) / shift(adj_o, -1) - 1, pk, t_len)


def forward_excess(p: Panel, f: Features, h: int) -> F64:
    """(forward return − equal-weighted eligible mean of the day) / (σ20 · √h)."""
    fr = forward_return(p, f, h)
    bench = ew_mean(fr, f.elig)
    with np.errstate(invalid="ignore", divide="ignore"):
        return (fr - bench[:, None]) / unit_fraction(f.sigma20, h)


def calendar_of(pk: Packing, cols: I64, pos: I64) -> I64:
    """Calendar row of own-bar positions (−1 when out of range)."""
    ok = (pos >= 0) & (pos < pk.order.shape[0])
    out = np.full(pos.shape, -1, dtype=np.int64)
    out[ok] = pk.order[pos[ok], cols[ok]]
    return out


def gather_bars(b: Bars, cols: I64, pos: I64) -> tuple[F64, F64, F64, F64]:
    g = pos * b.width + cols
    return b.open.ravel()[g], b.high.ravel()[g], b.low.ravel()[g], b.close.ravel()[g]
