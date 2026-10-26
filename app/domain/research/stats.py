"""Inference for the trigger research (docs/31 §3.2). Pure, numpy only, fixed seeds.

Weekly series (trades grouped by the week of their signal), Newey–West t, cluster-robust standard errors,
circular block bootstrap, Romano–Wolf step-down (one-sided, studentised by the Newey–West standard error),
Holm, Benjamini–Hochberg, probability of backtest overfitting (CSCV) and the deflated Sharpe ratio.
"""

from __future__ import annotations

import itertools
import math

import numpy as np

from app.domain.research.panel import F64, I64

EULER_GAMMA = 0.5772156649015329


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_ppf(p: float) -> float:
    """Inverse of the standard normal cdf (Acklam's rational approximation + one Newton step)."""
    if not 0.0 < p < 1.0:
        return math.inf if p >= 1.0 else -math.inf
    a = (-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02, 1.383577518672690e02,
         -3.066479806614716e01, 2.506628277459239e00)  # fmt: skip
    b = (-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02, 6.680131188771972e01,
         -1.328068155288572e01)  # fmt: skip
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00, -2.549732539343734e00,
         4.374664141464968e00, 2.938163982698783e00)  # fmt: skip
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00, 3.754408661907416e00)
    lo = 0.02425
    if p < lo:
        q = math.sqrt(-2 * math.log(p))
        x = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )
    elif p <= 1 - lo:
        q = p - 0.5
        r = q * q
        x = (
            (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5])
            * q
            / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
        )
    else:
        q = math.sqrt(-2 * math.log(1 - p))
        x = -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )
    e = norm_cdf(x) - p
    u = e * math.sqrt(2 * math.pi) * math.exp(x * x / 2)
    return x - u / (1 + x * u / 2)


def one_sided_p(t: float | None) -> float:
    """P(Z ≥ t) under the null of no positive effect."""
    if t is None or not math.isfinite(t):
        return 1.0 if t is None or t < 0 else 0.0
    return 1.0 - norm_cdf(t)


# --- series --------------------------------------------------------------------------------------


def weekly_series(week: I64, values: F64) -> tuple[I64, F64]:
    """Equal-weighted mean of the values of each week (weeks with at least one value), weeks ascending."""
    ok = np.isfinite(values)
    w, v = week[ok], values[ok]
    if w.size == 0:
        return np.zeros(0, dtype=np.int64), np.zeros(0)
    uw, inv = np.unique(w, return_inverse=True)
    s = np.bincount(inv, weights=v)
    c = np.bincount(inv)
    return uw.astype(np.int64), s / c


def nw_lag(median_hold_sessions: float) -> int:
    return max(2, math.ceil(median_hold_sessions / 5))


def long_run_variance(x: F64, lag: int) -> float:
    n = x.size
    if n < 2:
        return math.nan
    e = x - x.mean()
    v = float(e @ e) / n
    for k in range(1, min(lag, n - 1) + 1):
        v += 2 * (1 - k / (lag + 1)) * float(e[k:] @ e[:-k]) / n
    return max(v, 0.0)


def newey_west_se(x: F64, lag: int) -> float:
    n = x.size
    lrv = long_run_variance(x, lag)
    return math.sqrt(lrv / n) if n > 1 and lrv > 0 else math.nan


def newey_west_t(x: F64, lag: int) -> float:
    se = newey_west_se(x, lag)
    return float(x.mean()) / se if se and math.isfinite(se) else math.nan


def design_effect(x: F64, lag: int) -> float:
    """Variance of the mean under the Newey–West estimate ÷ the i.i.d. variance."""
    if x.size < 3:
        return math.nan
    iid = float(np.var(x, ddof=0))
    return long_run_variance(x, lag) / iid if iid > 0 else math.nan


def cluster_se(values: F64, groups: I64) -> float:
    """Cluster-robust (CR1) standard error of the mean of ``values`` clustered by ``groups``."""
    ok = np.isfinite(values)
    v, g = values[ok], groups[ok]
    n = v.size
    if n < 2:
        return math.nan
    _, inv = np.unique(g, return_inverse=True)
    n_g = int(inv.max()) + 1
    if n_g < 2:
        return math.nan
    s = np.bincount(inv, weights=v - v.mean())
    var = float(s @ s) / n**2 * n_g / (n_g - 1)
    return math.sqrt(var)


def block_indices(n: int, block: int, rng: np.random.Generator) -> I64:
    k = math.ceil(n / block)
    starts = rng.integers(0, n, size=k)
    return ((starts[:, None] + np.arange(block)[None, :]) % n).ravel()[:n].astype(np.int64)


def block_bootstrap_means(x: F64, block: int, draws: int, seed: int) -> F64:
    """Means of ``draws`` circular block-bootstrap resamples (vectorised by batches)."""
    rng = np.random.default_rng(seed)
    n = x.size
    if n == 0:
        return np.zeros(0)
    k = math.ceil(n / block)
    out = np.empty(draws)
    step = max(1, 2_000_000 // max(n, 1))
    for a in range(0, draws, step):
        b = min(draws, a + step)
        starts = rng.integers(0, n, size=(b - a, k))
        idx = ((starts[:, :, None] + np.arange(block)[None, None, :]) % n).reshape(b - a, -1)[:, :n]
        out[a:b] = x[idx].mean(axis=1)
    return out


def block_bootstrap_ci(x: F64, block: int, draws: int, seed: int, level: float = 0.95) -> tuple[float, float]:
    if x.size < 2:
        return (math.nan, math.nan)
    m = block_bootstrap_means(x, block, draws, seed)
    a = (1 - level) / 2
    return float(np.quantile(m, a)), float(np.quantile(m, 1 - a))


def _nw_columns(x: F64, lag: int) -> tuple[F64, F64]:
    """Column means and Newey–West standard errors of the means of a matrix with gaps (NaN): the gaps
    contribute nothing to the autocovariances (rows = weeks)."""
    ok = np.isfinite(x)
    n = ok.sum(axis=0).astype(np.float64)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = np.where(n > 0, np.where(ok, x, 0.0).sum(axis=0) / np.maximum(n, 1), np.nan)
        e = np.where(ok, x - mean, 0.0)
        v = (e * e).sum(axis=0) / n
        for k in range(1, lag + 1):
            if k < x.shape[0]:
                v = v + 2 * (1 - k / (lag + 1)) * (e[k:] * e[:-k]).sum(axis=0) / n
        se = np.sqrt(np.maximum(v, 0.0) / n)
    return mean, np.where((n >= 3) & (se > 0), se, np.nan)


def romano_wolf(matrix: F64, block: int, draws: int, seed: int, lag: int = 4) -> tuple[F64, F64]:
    """One-sided Romano–Wolf step-down adjusted p-values of the column means of ``matrix`` (rows = weeks,
    NaN = no trade that week): circular block bootstrap of the rows (joint, so the correlation between cells
    is kept), statistics studentised by the Newey–West standard error in the sample and in each resample.
    Returns (t, adjusted p)."""
    w, m = matrix.shape
    p_adj = np.ones(m)
    means, se = _nw_columns(matrix, lag)
    with np.errstate(invalid="ignore", divide="ignore"):
        t = means / se
    cols = np.nonzero(np.isfinite(t))[0]
    if cols.size == 0 or w < 2:
        return t, p_adj
    rng = np.random.default_rng(seed)
    sub = matrix[:, cols]
    stars = np.empty((draws, cols.size))
    with np.errstate(invalid="ignore", divide="ignore"):
        for b in range(draws):
            mb, sb = _nw_columns(sub[block_indices(w, block, rng)], lag)
            stars[b] = (mb - means[cols]) / sb
    order = np.argsort(-t[cols])
    st = np.nan_to_num(stars[:, order], nan=-np.inf)
    suffix_max = np.maximum.accumulate(st[:, ::-1], axis=1)[:, ::-1]
    p = (1 + (suffix_max >= t[cols][order][None, :]).sum(axis=0)) / (1 + draws)
    p = np.maximum.accumulate(p)
    p_adj[cols[order]] = np.minimum(p, 1.0)
    return t, p_adj


def holm(p: F64) -> F64:
    m = p.size
    if m == 0:
        return p.copy()
    order = np.argsort(p)
    adj = np.minimum(1.0, (m - np.arange(m)) * p[order])
    adj = np.maximum.accumulate(adj)
    out = np.empty(m)
    out[order] = adj
    return out


def bh(p: F64) -> F64:
    """Benjamini–Hochberg q-values."""
    m = p.size
    if m == 0:
        return p.copy()
    order = np.argsort(p)
    q = p[order] * m / (np.arange(m) + 1)
    q = np.minimum.accumulate(q[::-1])[::-1]
    out = np.empty(m)
    out[order] = np.minimum(q, 1.0)
    return out


def pbo_cscv(matrix: F64, blocks: int = 16) -> float:
    """Probability of backtest overfitting (Bailey et al., CSCV): rows = weeks (NaN → 0), columns = the
    variants of a family. Share of the splits where the in-sample best is below the out-of-sample median."""
    x = np.nan_to_num(matrix)
    w, m = x.shape
    if m < 2 or w < blocks * 2:
        return math.nan
    size = w // blocks
    x = x[: size * blocks]
    bs = x.reshape(blocks, size, m).sum(axis=1)
    combos = np.array([c for c in itertools.combinations(range(blocks), blocks // 2)])
    sel = np.zeros((combos.shape[0], blocks))
    sel[np.arange(combos.shape[0])[:, None], combos] = 1.0
    is_perf = sel @ bs
    oos_perf = (1 - sel) @ bs
    best = np.argmax(is_perf, axis=1)
    chosen = oos_perf[np.arange(best.size), best]
    rank = (oos_perf < chosen[:, None]).sum(axis=1) + 1
    omega = rank / (m + 1)
    lam = np.log(omega / (1 - omega))
    return float((lam <= 0).mean())


def deflated_sharpe(sr: float, n_trials: int, var_sr: float, n_obs: int, skew: float, kurt: float) -> float:
    """Probability that the true Sharpe ratio exceeds the expected maximum of ``n_trials`` null trials."""
    if not (math.isfinite(sr) and n_obs > 1):
        return math.nan
    sr0 = 0.0
    if n_trials > 1 and var_sr > 0:
        sr0 = math.sqrt(var_sr) * (
            (1 - EULER_GAMMA) * norm_ppf(1 - 1 / n_trials) + EULER_GAMMA * norm_ppf(1 - 1 / (n_trials * math.e))
        )
    den = 1 - skew * sr + (kurt - 1) / 4 * sr * sr
    if den <= 0:
        return math.nan
    return norm_cdf((sr - sr0) * math.sqrt(n_obs - 1) / math.sqrt(den))


def moments(x: F64) -> tuple[float, float, float]:
    """(Sharpe of the series, skewness, kurtosis — not excess)."""
    if x.size < 3:
        return (math.nan, math.nan, math.nan)
    m, s = float(x.mean()), float(x.std(ddof=1))
    if s <= 0:
        return (math.nan, 0.0, 3.0)
    z = (x - m) / s
    return m / s, float((z**3).mean()), float((z**4).mean())


def profit_factor(x: F64) -> float:
    g, lo = float(x[x > 0].sum()), float(-x[x < 0].sum())
    return g / lo if lo > 0 else (math.inf if g > 0 else math.nan)


def gini(x: F64) -> float:
    """Gini coefficient of the absolute contributions (0 = equal, 1 = concentrated)."""
    v = np.sort(np.abs(x[np.isfinite(x)]))
    n = v.size
    if n == 0 or v.sum() == 0:
        return math.nan
    return float((2 * np.arange(1, n + 1) - n - 1) @ v / (n * v.sum()))


def permutation_p(observed: float, null: F64) -> float:
    """One-sided permutation p-value with the +1 correction."""
    null = null[np.isfinite(null)]
    return float((1 + (null >= observed).sum()) / (1 + null.size))
