"""Panels of daily bars and vectorised features (docs/31 §1.1). Pure, numpy only.

A ``Panel`` holds the raw bars of one venue group on the union of its sessions (shape ``(T, N)``, NaN where
an instrument has no bar). Every rolling feature is computed on the instrument's **own** sessions (the
``Packing`` stacks each column's bars at the top, as the scalar indicators see them) and then put back on the
calendar. Cross-sectional features (ranks, breadth, terciles, regime) use the calendar and the eligible
population of the day only. Nothing at row ``t`` depends on a bar after ``t``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

import numpy as np
import numpy.typing as npt

F64 = npt.NDArray[np.float64]
F32 = npt.NDArray[np.float32]
I64 = npt.NDArray[np.int64]
I8 = npt.NDArray[np.int8]
Bool = npt.NDArray[np.bool_]
Days = npt.NDArray[np.datetime64]


@dataclass(frozen=True)
class IndexSeries:
    """A market index on its own sessions (CAC 40, STOXX 600): days ascending, closes."""

    days: Days
    close: F64


@dataclass(frozen=True)
class Panel:
    group: str  # venue group: P0 | P1 | P2
    days: Days  # (T,) datetime64[D], union of the sessions of the group's venues, ascending
    isins: tuple[str, ...]
    open: F64  # (T, N) raw prices, quote currency, NaN where no bar
    high: F64
    low: F64
    close: F64
    adj_close: F64  # total-return adjusted close (dividends and splits), NaN where no bar
    volume: F64
    fx_to_eur: F64  # (T, N) EUR per unit of quote currency
    is_xpar: Bool  # (N,) regime index: CAC 40 for Paris, STOXX 600 otherwise
    small_cap: Bool  # today's capitalisation (flagged in the report)
    pea_eligible: Bool
    ttf_rate: F64  # (N,) French TTF on purchases (FR issuer, today's cap ≥ threshold), else 0
    ftt_rate: F64  # (N,) foreign transaction tax on purchases (tax.ftt_by_country)
    fx_fee_pct: F64  # (N,) change fee per leg for non-euro quotes, else 0
    in_universe: Bool  # today's universe membership (survivorship split)
    index_cac: IndexSeries | None = None
    index_stoxx: IndexSeries | None = None
    vintage: I64 | None = None  # (T, N) download vintage of each bar (source × reception day), None: one vintage

    @property
    def shape(self) -> tuple[int, int]:
        return (int(self.close.shape[0]), int(self.close.shape[1]))

    def select(self, cols: I64) -> Panel:
        """The same panel restricted to some instruments (columns)."""
        c = np.asarray(cols, dtype=np.int64)
        return Panel(
            self.group, self.days, tuple(self.isins[int(k)] for k in c),
            self.open[:, c], self.high[:, c], self.low[:, c], self.close[:, c], self.adj_close[:, c],
            self.volume[:, c], self.fx_to_eur[:, c], self.is_xpar[c], self.small_cap[c], self.pea_eligible[c],
            self.ttf_rate[c], self.ftt_rate[c], self.fx_fee_pct[c], self.in_universe[c], self.index_cac,
            self.index_stoxx, None if self.vintage is None else self.vintage[:, c],
        )  # fmt: skip


# --- own-session packing ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Packing:
    order: I64  # (L, N) calendar row of the k-th own bar of each column, −1 = padding
    pos: I64  # (T, N) own-bar ordinal of the bar on day t, −1 when no bar
    n_bars: I64  # (N,)


def valid_bars(p: Panel) -> Bool:
    ok = np.isfinite(p.open) & np.isfinite(p.high) & np.isfinite(p.low) & np.isfinite(p.close)
    return ok & (p.close > 0) & (p.low > 0) & np.isfinite(p.volume)


def make_packing(valid: Bool) -> Packing:
    t_len, n = valid.shape
    counts = valid.sum(axis=0).astype(np.int64)
    length = int(counts.max()) if n else 0
    order = np.argsort(~valid, axis=0, kind="stable")[:length].astype(np.int64)
    k = np.arange(length, dtype=np.int64)[:, None]
    order = np.where(k < counts[None, :], order, -1)
    pos = np.full((t_len, n), -1, dtype=np.int64)
    m = order >= 0
    cols = np.broadcast_to(np.arange(n, dtype=np.int64), (length, n))
    pos[order[m], cols[m]] = np.broadcast_to(k, (length, n))[m]
    return Packing(order, pos, counts)


def pack(x: F64, pk: Packing) -> F64:
    safe = np.where(pk.order >= 0, pk.order, 0)
    out = np.take_along_axis(x, safe, axis=0).astype(np.float64)
    return np.where(pk.order >= 0, out, np.nan)


def unpack(y: F64, pk: Packing, t_len: int) -> F64:
    n = y.shape[1]
    out = np.full((t_len, n), np.nan)
    m = pk.order >= 0
    cols = np.broadcast_to(np.arange(n, dtype=np.int64), pk.order.shape)
    out[pk.order[m], cols[m]] = y[m]
    return out


def unpack_bool(y: Bool, pk: Packing, t_len: int) -> Bool:
    return np.asarray(unpack(y.astype(np.float64), pk, t_len) == 1.0, dtype=np.bool_)


# --- rolling helpers on packed arrays (oldest first, NaN padding at the tail) ----------------------------


def shift(x: F64, k: int) -> F64:
    """x[i − k] at row i (k > 0: past values), NaN where undefined."""
    out = np.full_like(x, np.nan, dtype=np.float64)
    if k == 0:
        return x.astype(np.float64)
    if k > 0:
        if k < x.shape[0]:
            out[k:] = x[:-k]
    elif -k < x.shape[0]:
        out[:k] = x[-k:]
    return out


def rolling_sum(x: F64, n: int) -> F64:
    """Sum over the last ``n`` rows; NaN when the window holds a NaN or is incomplete."""
    out = np.full_like(x, np.nan, dtype=np.float64)
    if n <= 0 or x.shape[0] < n:
        return out
    nan = np.isnan(x)
    zero = np.zeros((1,) + x.shape[1:])
    c = np.concatenate([zero, np.cumsum(np.where(nan, 0.0, x), axis=0)])
    k = np.concatenate([zero, np.cumsum(nan, axis=0, dtype=np.float64)])
    s = c[n:] - c[:-n]
    out[n - 1 :] = np.where(k[n:] - k[:-n] > 0, np.nan, s)
    return out


def rolling_mean(x: F64, n: int) -> F64:
    return rolling_sum(x, n) / n


def sma(x: F64, n: int) -> F64:
    """Simple moving average over the last ``n`` own sessions (``indicators.sma``)."""
    return rolling_mean(x, n)


def rolling_std(x: F64, n: int) -> F64:
    """Sample standard deviation (ddof 1) over ``n`` rows, from running sums. Meant for small, centred
    values (daily log returns), where the sums keep a relative precision far below 1e-10."""
    s1 = rolling_sum(x, n)
    s2 = rolling_sum(x * x, n)
    var = (s2 - s1 * s1 / n) / (n - 1)
    return np.sqrt(np.maximum(var, 0.0))


def rolling_max(x: F64, n: int) -> F64:
    out = np.full_like(x, np.nan, dtype=np.float64)
    if x.shape[0] < n:
        return out
    out[n - 1 :] = np.lib.stride_tricks.sliding_window_view(x, n, axis=0).max(axis=-1)
    return out


def rolling_min(x: F64, n: int) -> F64:
    out = np.full_like(x, np.nan, dtype=np.float64)
    if x.shape[0] < n:
        return out
    out[n - 1 :] = np.lib.stride_tricks.sliding_window_view(x, n, axis=0).min(axis=-1)
    return out


def rolling_median(x: F64, n: int, chunk: int = 64) -> F64:
    """Median over ``n`` rows, by blocks of columns (bounded memory). Windows reaching the NaN padding only
    produce values on padding rows, which ``unpack`` drops."""
    out = np.full_like(x, np.nan, dtype=np.float64)
    if x.shape[0] < n:
        return out
    k = n // 2
    for c0 in range(0, x.shape[1], chunk):
        w = np.lib.stride_tricks.sliding_window_view(x[:, c0 : c0 + chunk].astype(np.float32), n, axis=0)
        part = np.partition(w, (k - 1, k) if n % 2 == 0 and k > 0 else (k,), axis=-1)
        med = 0.5 * (part[..., k - 1] + part[..., k]) if n % 2 == 0 and k > 0 else part[..., k]
        out[n - 1 :, c0 : c0 + chunk] = med
    return out


def true_range(high: F64, low: F64, close: F64) -> F64:
    pc = shift(close, 1)
    tr = np.fmax(high - low, np.fmax(np.abs(high - pc), np.abs(low - pc)))
    tr[0] = high[0] - low[0]
    return tr


def atr_wilder(high: F64, low: F64, close: F64, n: int = 14) -> F64:
    """Wilder's ATR from the first own bar (``indicators.atr``): seed = mean of TR[1..n], then smoothed."""
    tr = true_range(high, low, close)
    out = np.full_like(tr, np.nan)
    if tr.shape[0] <= n:
        return out
    a = tr[1 : n + 1].sum(axis=0) / n
    out[n] = a
    for k in range(n + 1, tr.shape[0]):
        a = (a * (n - 1) + tr[k]) / n
        out[k] = a
    return out


def returns_over(x: F64, n: int) -> F64:
    return x / shift(x, n) - 1


def rs_score_packed(close: F64) -> F64:
    """0.4·r250 + 0.2·r125 + 0.2·r63 + 0.2·r21 on own sessions (``indicators.rs_score``)."""
    return (
        0.4 * returns_over(close, 250)
        + 0.2 * returns_over(close, 125)
        + 0.2 * returns_over(close, 63)
        + 0.2 * returns_over(close, 21)
    )


def recent_any(cond: Bool, n: int) -> Bool:
    """True when ``cond`` held on the row itself or on one of the ``n − 1`` previous rows (packed)."""
    cs = np.cumsum(cond.astype(np.int64), axis=0)
    prev = np.zeros_like(cs)
    if n < cs.shape[0]:
        prev[n:] = cs[:-n]
    return np.asarray(cs - prev > 0, dtype=np.bool_)


def prior_any(cond: Bool, n: int) -> Bool:
    """True when ``cond`` held on at least one of the ``n`` previous rows (packed, own sessions)."""
    x = cond.astype(np.float64)
    s = shift(rolling_sum(x, n), 1)
    early = np.zeros_like(x)
    early[1:] = np.cumsum(x, axis=0)[:-1]  # fewer than n previous rows: count what exists
    s = np.where(np.isnan(s), early, s)
    return s > 0


# --- cross-sectional helpers (calendar) ------------------------------------------------------------


def pct_rank(x: F64, mask: Bool, extra: Bool | None = None) -> F64:
    """Percentile 0–100 within ``mask`` of each day, as ``indicators.percentile_rank`` (strictly below +
    half of the other ties, over n − 1). NaN outside the mask, except on ``extra`` rows: ranked against the
    day's population as if each one joined it alone (counting of masked signals; never a population member)."""
    out = np.full(x.shape, np.nan)
    for t in range(x.shape[0]):
        m = mask[t] & np.isfinite(x[t])
        v = x[t, m]
        n = v.size
        if n == 0:
            continue
        if extra is not None:
            e = extra[t] & ~mask[t] & np.isfinite(x[t])
            if e.any():
                s_ = np.sort(v)
                ve = x[t, e]
                lo_ = np.searchsorted(s_, ve, side="left")
                out[t, e] = 100.0 * (lo_ + 0.5 * (np.searchsorted(s_, ve, side="right") - lo_)) / n
        if n == 1:
            out[t, m] = 100.0
            continue
        s = np.sort(v)
        below = np.searchsorted(s, v, side="left")
        ties = np.searchsorted(s, v, side="right") - below
        out[t, m] = 100.0 * (below + 0.5 * np.maximum(ties - 1, 0)) / (n - 1)
    return out


def terciles(x: F64, mask: Bool) -> I8:
    """0 / 1 / 2 by daily tercile within the mask, −1 outside."""
    r = pct_rank(x, mask)
    out = np.full(x.shape, -1, dtype=np.int8)
    ok = np.isfinite(r)
    out[ok] = np.minimum((r[ok] / 100.0 * 3).astype(np.int8), 2)
    return out


def daily_median(x: F64, mask: Bool) -> F64:
    y = np.where(mask & np.isfinite(x), x, np.nan)
    out = np.full(x.shape[0], np.nan)
    has = np.isfinite(y).any(axis=1)
    out[has] = np.nanmedian(y[has], axis=1)
    return out


def month_end(days: Days) -> Bool:
    """Last session of each calendar month in the panel (the very last row is not known to be one)."""
    m = days.astype("datetime64[M]")
    out = np.zeros(days.shape[0], dtype=bool)
    if days.shape[0] > 1:
        out[:-1] = m[1:] != m[:-1]
    return out


def index_regime(idx: IndexSeries | None, days: Days, n: int = 200) -> F64:
    """1.0 when the index's last close on or before each day is above its own ``n``-session average,
    0.0 when below, NaN when unknown."""
    out = np.full(days.shape[0], np.nan)
    if idx is None or idx.close.size == 0:
        return out
    c = idx.close.astype(np.float64)
    avg = np.full_like(c, np.nan)
    if c.size >= n:
        cs = np.concatenate([[0.0], np.cumsum(c)])
        avg[n - 1 :] = (cs[n:] - cs[:-n]) / n
    up = np.where(np.isfinite(avg), (c > avg).astype(np.float64), np.nan)
    k = np.searchsorted(idx.days, days, side="right") - 1
    ok = k >= 0
    out[ok] = up[k[ok]]
    return out


# --- features --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class FeatureParams:
    min_price: float  # universe.min_price
    min_turnover_eur: float  # universe.min_adv_eur_pea (60-session median turnover)
    breadth_green: float  # regime.breadth_green
    turnover_sessions: int = 60


@dataclass(frozen=True)
class Features:
    pk: Packing
    valid: Bool
    elig: Bool
    prev_close: F64
    prev_volume: F64
    sma10: F64
    sma20: F64
    sma50: F64
    sma200: F64
    sma200_lag20: F64
    atr14: F64
    atr14_prev: F64
    sigma20: F64  # std of 20 daily log returns of adj_close
    rvol_med20: F64  # volume / median volume of the previous 20 sessions
    rvol_mean20: F64  # volume / mean volume of the previous 20 sessions (production ``rvol_day``)
    vol_prev_max49: F64
    vol_prev_med50: F64
    high_252: F64
    high_250: F64  # production ``high_52w``
    pivot60: F64  # highest high of the 60 previous sessions
    consolidation20: F64
    ti65: F64
    rsi14: F64
    mom_12_1: F64  # adj_close[t−21] / adj_close[t−252] − 1
    mom_rank: F64
    rs_score: F64
    rs_rank: F64  # within the eligible instruments of the venue group, 0–100
    ret5: F64  # log return of adj_close over 5 sessions
    turnover_med60: F64  # EUR
    turnover_med20: F64
    t04: Bool  # close > SMA200 and SMA50 > SMA200
    breadth: F64  # (T,) share of eligible instruments above their SMA50
    t13: Bool  # venue index above its SMA200 and breadth ≥ regime.breadth_green
    t13_known: Bool
    low_vol: Bool  # σ20 ≤ daily median σ20 of the eligible instruments (T11)
    liq_terc: I8
    sig_terc: I8
    stratum: I8  # liq · 6 + sig · 2 + T04 ∈ 0..17, −1 when not eligible
    month_end: Bool  # (T,)
    adj_factor: F64  # adj_close / close
    ca_masked: Bool  # eligible but for a recent corporate action (``exclude``): never a signal, counted


def packed_adjusted(p: Panel, pk: Packing) -> F64:
    """Adjusted closes on own sessions, the raw close where the adjusted one is missing."""
    c, adj = pack(p.close, pk), pack(p.adj_close, pk)
    return np.where(np.isfinite(adj) & (adj > 0), adj, c)


def adjustment_factor(p: Panel, pk: Packing) -> F64:
    """``adj_close / close`` on the calendar (NaN where no valid bar)."""
    with np.errstate(invalid="ignore", divide="ignore"):
        return unpack(packed_adjusted(p, pk) / pack(p.close, pk), pk, p.shape[0])


def build_features(p: Panel, fp: FeatureParams, exclude: Bool | None = None) -> Features:
    """Features of a panel; ``exclude`` (T, N) removes rows from the eligible population before every
    cross-sectional feature (recent corporate actions: raw-price features unreliable)."""
    t_len, _ = p.shape
    valid = valid_bars(p)
    pk = make_packing(valid)
    h, lo, c = pack(p.high, pk), pack(p.low, pk), pack(p.close, pk)
    v, fx = pack(p.volume, pk), pack(p.fx_to_eur, pk)
    adj = packed_adjusted(p, pk)

    def u(x: F64) -> F64:
        return unpack(x, pk, t_len)

    prev_c = shift(c, 1)
    lr = np.log(adj / shift(adj, 1))
    atr = atr_wilder(h, lo, c, 14)
    turn = c * v * fx
    rng = rolling_max(h, 20)
    rng_lo = rolling_min(lo, 20)
    cons = (shift(rng, 1) - shift(rng_lo, 1)) / shift(rng_lo, 1)
    d = c - prev_c
    gains = rolling_mean(np.maximum(d, 0.0), 14)
    losses = rolling_mean(np.maximum(-d, 0.0), 14)
    with np.errstate(divide="ignore", invalid="ignore"):
        rsi = np.where(losses == 0, 100.0, 100.0 - 100.0 / (1 + gains / losses))
        rsi = np.where(np.isfinite(gains) & np.isfinite(losses), rsi, np.nan)
        vprev = shift(v, 1)
        sma50_p = sma(c, 50)
        sma200_p = sma(c, 200)
        med20 = shift(rolling_median(v, 20), 1)
        mean20 = shift(rolling_mean(v, 20), 1)
        f = dict(
            prev_close=u(prev_c),
            prev_volume=u(vprev),
            sma10=u(sma(c, 10)),
            sma20=u(sma(c, 20)),
            sma50=u(sma50_p),
            sma200=u(sma200_p),
            sma200_lag20=u(shift(sma200_p, 20)),
            atr14=u(atr),
            atr14_prev=u(shift(atr, 1)),
            sigma20=u(rolling_std(lr, 20)),
            rvol_med20=u(np.where(med20 > 0, v / med20, np.nan)),
            rvol_mean20=u(np.where(mean20 > 0, v / mean20, np.nan)),
            vol_prev_max49=u(shift(rolling_max(v, 49), 1)),
            vol_prev_med50=u(shift(rolling_median(v, 50), 1)),
            high_252=u(rolling_max(h, 252)),
            high_250=u(rolling_max(h, 250)),
            pivot60=u(shift(rolling_max(h, 60), 1)),
            consolidation20=u(cons),
            ti65=u(sma(c, 7) / sma(c, 65)),
            rsi14=u(rsi),
            mom_12_1=u(shift(adj, 21) / shift(adj, 252) - 1),
            rs_score=u(rs_score_packed(c)),
            ret5=u(np.log(adj / shift(adj, 5))),
            turnover_med60=u(rolling_median(turn, fp.turnover_sessions)),
            turnover_med20=u(rolling_median(turn, 20)),
            adj_factor=u(adj / c),
        )
    close = p.close
    flat = (close == f["prev_close"]) & (p.high == p.low)
    with np.errstate(invalid="ignore"):
        elig = (
            valid
            & (p.volume > 0)
            & ~flat
            & (close >= fp.min_price)
            & (np.nan_to_num(f["turnover_med60"], nan=-1.0) >= fp.min_turnover_eur)
        )
    ca_masked = np.zeros_like(elig) if exclude is None else elig & exclude
    elig = elig & ~ca_masked
    with np.errstate(invalid="ignore"):
        t04 = (close > f["sma200"]) & (f["sma50"] > f["sma200"])
        above50 = elig & np.isfinite(f["sma50"]) & (close > f["sma50"])
    known50 = elig & np.isfinite(f["sma50"])
    n50 = known50.sum(axis=1)
    breadth = np.where(n50 > 0, above50.sum(axis=1) / np.maximum(n50, 1), np.nan)
    cac = index_regime(p.index_cac, p.days)
    stoxx = index_regime(p.index_stoxx, p.days)
    idx_up = np.where(p.is_xpar[None, :], cac[:, None], stoxx[:, None])
    t13_known = np.isfinite(idx_up) & np.isfinite(breadth)[:, None]
    t13 = t13_known & (np.nan_to_num(idx_up) == 1.0) & (np.nan_to_num(breadth) >= fp.breadth_green)[:, None]
    sig = f["sigma20"]
    med_sig = daily_median(sig, elig)
    with np.errstate(invalid="ignore"):
        low_vol = elig & np.isfinite(sig) & (sig <= med_sig[:, None])
    liq = terciles(f["turnover_med60"], elig)
    sgt = terciles(sig, elig & np.isfinite(sig))
    stratum = np.where((liq >= 0) & (sgt >= 0), liq * 6 + sgt * 2 + t04.astype(np.int8), -1).astype(np.int8)
    return Features(
        pk=pk,
        valid=valid,
        elig=elig,
        t04=t04,
        breadth=breadth,
        t13=t13,
        t13_known=t13_known,
        low_vol=low_vol,
        liq_terc=liq,
        sig_terc=sgt,
        stratum=stratum,
        month_end=month_end(p.days),
        mom_rank=pct_rank(f["mom_12_1"], elig, ca_masked),
        rs_rank=pct_rank(f["rs_score"], elig, ca_masked),
        ca_masked=ca_masked,
        **f,
    )


# --- corporate actions and dividends (docs/31 §2.4) --------------------------------------------------


def rescale_vintage_boundaries(p: Panel, min_jump: float, ca_jump: float) -> tuple[Panel, int]:
    """Adjusted closes downloaded at different times carry different adjustment vintages: the factor
    ``adj_close / close`` jumps where two vintages meet although nothing happened to the share. At every
    change of ``p.vintage`` between two own sessions, the later rows' factor is rescaled so that it is
    continuous (anchored on the oldest rows: a row depends on its past only) — no dividend nor corporate
    action is inferred there — unless the raw close itself moves by ``ca_jump`` with the factor (a split on
    the boundary day stays a corporate action). Returns the panel and the number of boundaries whose factor
    moved by at least ``min_jump`` (the events avoided)."""
    if p.vintage is None:
        return p, 0
    pk = make_packing(valid_bars(p))
    c = pack(p.close, pk)
    with np.errstate(invalid="ignore", divide="ignore"):
        fac = packed_adjusted(p, pk) / c
        ratio = fac / shift(fac, 1)
        split = (np.abs(c / shift(c, 1) - 1) >= ca_jump) & (np.abs(ratio - 1) >= ca_jump)
    vin = pack(p.vintage.astype(np.float64), pk)
    prev = shift(vin, 1)
    boundary = np.isfinite(prev) & np.isfinite(vin) & (vin != prev) & np.isfinite(ratio) & (ratio > 0) & ~split
    corr = np.where(boundary, 1.0 / np.where(boundary, ratio, 1.0), 1.0)
    scale = unpack(np.cumprod(corr, axis=0), pk, p.shape[0])
    adj = np.where(np.isfinite(scale), p.adj_close * scale, p.adj_close)
    n = int((boundary & (np.abs(ratio - 1) >= min_jump)).sum())
    return replace(p, adj_close=adj), n


def mass_jump_days(adj_factor: F64, pk: Packing, min_jump: float, share: float, min_count: int = 10) -> Bool:
    """Safety net: days (T,) where more than ``share`` of the instruments with a known factor change show a
    jump ≥ ``min_jump`` (a vintage boundary the data did not reveal, not a dividend season). Flagged and
    counted in the report."""
    f = pack(adj_factor, pk)
    with np.errstate(invalid="ignore", divide="ignore"):
        ratio = shift(f, 1) / f
    known = unpack_bool(np.isfinite(ratio), pk, adj_factor.shape[0])
    jump = unpack_bool(np.isfinite(ratio) & (np.abs(ratio - 1) >= min_jump), pk, adj_factor.shape[0])
    n_known = known.sum(axis=1)
    n_jump = jump.sum(axis=1)
    return np.asarray((n_known >= min_count) & (n_jump > share * np.maximum(n_known, 1)), dtype=np.bool_)


def adj_factor_events(adj_factor: F64, close: F64, pk: Packing, ca_jump: float, div_min: float) -> tuple[Bool, F64]:
    """From the adjustment factor ``f = adj_close / close`` on own sessions: a change ≥ ``ca_jump`` between
    two sessions is a corporate action (split…); a drop of the factor of at least ``div_min`` is a dividend
    ``D_t = close_{t−1} · (1 − f_{t−1} / f_t)`` paid to holders of the previous session. Calendar arrays."""
    t_len = close.shape[0]
    f = pack(adj_factor, pk)
    c = pack(close, pk)
    with np.errstate(invalid="ignore", divide="ignore"):
        ratio = shift(f, 1) / f
        ca = np.isfinite(ratio) & (np.abs(ratio - 1) >= ca_jump)
        div = np.where(~ca & np.isfinite(ratio) & (ratio < 1 - div_min), shift(c, 1) * (1 - ratio), 0.0)
    return unpack_bool(ca, pk, t_len), np.nan_to_num(unpack(div, pk, t_len))


# --- exchange rates (docs/31 §1.1) --------------------------------------------------------------------------


def fx_path(days: Days, rate_days: Days, eur_per_unit: F64, static: float | None) -> tuple[F64, Bool]:
    """EUR per unit of a currency on each day: the last ECB rate published on or before the day — never a
    later one (no back-fill from the future). Before the first rate (or without any) the plan's static table
    ``static`` is used and those days are returned flagged; without a static rate they stay NaN (unknown)."""
    out = np.full(days.shape[0], np.nan)
    if rate_days.size:
        k = np.searchsorted(rate_days, days, side="right") - 1
        known = k >= 0
        out[known] = eur_per_unit[k[known]]
    else:
        known = np.zeros(days.shape[0], dtype=bool)
    flagged = ~known
    if static is not None and math.isfinite(static) and static > 0:
        out[flagged] = float(static)
    else:
        flagged = np.zeros(days.shape[0], dtype=bool)
    return out, np.asarray(flagged, dtype=np.bool_)
