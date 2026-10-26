"""Costs per account applied to simulated trades (docs/31 §2.3). Pure, vectorised.

Legs as ``app.domain.costs``: buy = commission + notional · (TTF + foreign tax + change fee); sell =
commission + notional · change fee; SRD carry = daily rate · buy notional · calendar days. On top, the
half-spread (Corwin–Schultz, per instrument-month, floored at ``risk.slippage_pct``) on both legs. Sizing on
the slipped stop and the risk budget (CLAUDE.md rule 7): ``shares = floor((budget − fees) / R_per_share_eur)``,
R per share widened by the change haircut outside the euro, notional capped by ``risk.max_position_pct_of_capital``.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from app.domain.research.panel import F64, Bool, Days
from app.domain.risk import FeeSchedule


def commission(fs: FeeSchedule, notional: F64) -> F64:
    """Vectorised ``FeeSchedule.commission`` (same bands, floor and cap)."""
    x = np.asarray(notional, dtype=np.float64)
    if fs.bands:
        fee = np.zeros_like(x)
        done = np.zeros(x.shape, dtype=bool)
        for upper, fixed, pct, minimum in fs.bands:
            m = ~done & (np.ones(x.shape, dtype=bool) if upper is None else (x <= upper))
            fee[m] = np.maximum(fixed + pct * x[m], minimum)
            done |= m
    elif fs.commission_pct is None:
        return np.zeros_like(x)
    else:
        fee = np.maximum(x * fs.commission_pct, fs.commission_min_eur)
    if fs.commission_cap_pct is not None:
        fee = np.minimum(fee, x * fs.commission_cap_pct)
    return fee


@dataclass(frozen=True)
class Account:
    name: str  # cto | pea | srd
    fees: FeeSchedule
    srd_daily_rate: float = 0.0  # srd.crd_daily_rate on the SRD scenario
    dividend_tax: float = 0.0  # tax on dividends in the « net » variant (PFU on the CTO, 0 in the PEA)


@dataclass(frozen=True)
class LegInputs:
    """Per-trade inputs of the cost model (arrays of the same length)."""

    entry: F64
    exit_px: F64
    stop_exec: F64
    div_cash: F64  # dividends per share (gross)
    fx_in: F64  # EUR per unit of quote currency at entry / exit
    fx_out: F64
    ttf: F64
    ftt: F64
    fx_fee: F64
    haircut: F64  # risk.fx_haircut outside the euro, else 0
    hs_in: F64  # half-spread at entry / exit
    hs_out: F64
    days: F64  # calendar days held (SRD carry)
    unit_frac: F64  # risk unit of the R metric as a fraction of the notional (2 σ √h)
    stop_exit: Bool  # exit by a stop order (stress: extra slippage)


@dataclass(frozen=True)
class Economics:
    shares: F64  # floored (0: not affordable with the risk budget)
    notional: F64  # EUR at the floored size
    net_eur: F64  # floored size; NaN when no share is affordable
    net_ret: F64  # net return on the notional (fractional size)
    gross_ret: F64
    r_unit: F64  # net return / unit fraction (R_ref)
    r_unit_gross: F64
    r_stop: F64  # net result / (shares × (entry − slipped stop)) — R on the stop distance
    r_stop_gross: F64
    notional_frac: F64  # EUR at the fractional size (capacity check)


def economics(
    x: LegInputs,
    acct: Account,
    risk_budget_eur: float,
    notional_cap_eur: float,
    fee_mult: float = 1.0,
    spread_mult: float = 1.0,
    extra_exit_slip: float = 0.0,
    div_rate: float = 1.0,
) -> Economics:
    rps_raw = (x.entry - x.stop_exec) * x.fx_in
    rps = rps_raw * (1 + x.haircut)
    unit_px = x.entry * x.fx_in
    with np.errstate(invalid="ignore", divide="ignore"):
        n0 = np.minimum(risk_budget_eur / rps * unit_px, notional_cap_eur)
        est = 2 * commission(acct.fees, n0) + n0 * (x.ttf + x.ftt + 2 * x.fx_fee)
        frac = np.minimum(np.maximum(risk_budget_eur - est, 0.0) / rps, notional_cap_eur / unit_px)
        frac = np.where(np.isfinite(frac) & (frac > 0), frac, 0.0)
    whole = np.floor(frac)

    def result(s: F64) -> tuple[F64, F64, F64]:
        nb = s * unit_px
        ns = s * x.exit_px * x.fx_out
        div = s * x.div_cash * div_rate * x.fx_out
        fees = commission(acct.fees, nb) + nb * (x.ttf + x.ftt + x.fx_fee) + commission(acct.fees, ns) + ns * x.fx_fee
        cost = fee_mult * fees + spread_mult * (x.hs_in * nb + x.hs_out * ns) + extra_exit_slip * ns * x.stop_exit
        cost = cost + acct.srd_daily_rate * nb * x.days
        gross = ns - nb + div
        return gross - cost, gross, nb

    net, gross, nb = result(frac)
    net_w, _, nb_w = result(whole)
    with np.errstate(invalid="ignore", divide="ignore"):
        net_ret = np.where(nb > 0, net / nb, np.nan)
        gross_ret = np.where(nb > 0, gross / nb, np.nan)
        r_stop = np.where(frac > 0, net / (frac * rps_raw), np.nan)
        r_stop_gross = np.where(frac > 0, gross / (frac * rps_raw), np.nan)
        return Economics(
            whole,
            nb_w,
            np.where(whole > 0, net_w, np.nan),
            net_ret,
            gross_ret,
            net_ret / x.unit_frac,
            gross_ret / x.unit_frac,
            r_stop,
            r_stop_gross,
            nb,
        )


# --- spreads ------------------------------------------------------------------------------------


def corwin_schultz(high: F64, low: F64) -> F64:
    """Two-day Corwin–Schultz spread estimate, placed on the second day (packed own sessions), ≥ 0."""
    with np.errstate(invalid="ignore", divide="ignore"):
        lhl = np.log(high / low) ** 2
        beta = lhl.copy()
        beta[1:] = lhl[1:] + lhl[:-1]
        beta[0] = np.nan
        hmax = high.copy()
        lmin = low.copy()
        hmax[1:] = np.maximum(high[1:], high[:-1])
        lmin[1:] = np.minimum(low[1:], low[:-1])
        gamma = np.log(hmax / lmin) ** 2
        k = 3 - 2 * math.sqrt(2)
        alpha = (np.sqrt(2 * beta) - np.sqrt(beta)) / k - np.sqrt(gamma / k)
        s = 2 * (np.exp(alpha) - 1) / (1 + np.exp(alpha))
    return np.where(np.isfinite(s), np.maximum(s, 0.0), np.nan)


def monthly_half_spread(spread: F64, days: Days, floor: float) -> F64:
    """Half of the mean spread estimate of the **previous** month of each instrument (no look-ahead),
    floored; the floor when unknown. Calendar arrays ``(T, N)``."""
    month = days.astype("datetime64[M]").astype(np.int64)
    m0 = int(month.min()) if month.size else 0
    mi = month - m0
    n_m = int(mi.max()) + 1 if mi.size else 1
    ok = np.isfinite(spread)
    s = np.zeros((n_m, spread.shape[1]))
    c = np.zeros((n_m, spread.shape[1]))
    np.add.at(s, mi, np.where(ok, spread, 0.0))
    np.add.at(c, mi, ok.astype(np.float64))
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = np.where(c > 0, s / c, np.nan)
    prev = np.full_like(mean, np.nan)
    prev[1:] = mean[:-1]
    hs = prev[mi] / 2
    return np.where(np.isfinite(hs), np.maximum(hs, floor), floor)


# --- survivorship stress (docs/31 §0) -------------------------------------------------------------


def delisting_adjustment(
    gross_ret: F64, hold_years: F64, unit_frac: F64, small: Bool, hazard_small: float, hazard_large: float,
    takeover_jump: float,
) -> F64:  # fmt: skip
    """Expected change of each trade's R under the assumed yearly hazards (same rate for bankruptcy and
    takeover): bankruptcy → −100 % of the notional instead of the realised return; takeover → +jump."""
    h = np.where(small, hazard_small, hazard_large)
    with np.errstate(invalid="ignore", divide="ignore"):
        return hold_years * h * ((-1.0 - gross_ret) + takeover_jump) / unit_frac


def breakeven_hazard_multiple(r: F64, adj: F64) -> float:
    """Multiple λ of the assumed hazards that brings the mean R to zero (∞ when the stress helps)."""
    m_r, m_a = float(np.nanmean(r)), float(np.nanmean(adj))
    if m_r <= 0:
        return 0.0
    if m_a >= 0:
        return math.inf
    return -m_r / m_a


def ttf_rates(isins: Sequence[str], market_caps_eur: Sequence[float | None], rate: float, threshold_eur: float) -> F64:
    """French TTF on purchases: French issuer (ISIN prefix FR) with a capitalisation at or above the threshold
    (today's capitalisation — flagged in the report), else 0."""
    return np.array(
        [
            rate if x.startswith("FR") and (c or 0.0) >= threshold_eur else 0.0
            for x, c in zip(isins, market_caps_eur, strict=True)
        ]
    )
