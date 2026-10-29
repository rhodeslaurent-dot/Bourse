"""Vectorised exit engine (docs/31 §2). Pure, numpy only.

One loop over the sessions of the holding window, vectorised over all orders: the same conservative rules as
``app.domain.backtest.simulate_trade`` (fill only when the session trades through the limit or opens below
it; an open at or below the invalidation level cancels the order; stop assumed hit after the fill on the fill
session; gap through the stop → exit at the open, slipped; time exit at the close). With
``ExitSpec(target_on_fill_bar="always", require_bracket=True)``, the ``stop_first`` rule and no dividends, the
entries, exits and MAE are identical to ``simulate_trade`` (unit-tested on random brackets); the MFE differs
on intraday fills only, where the high of the fill session is not counted (it may precede the fill).

Levels are formulas of the executed entry price (``stop = stop_mult · entry − stop_off``, ``target =
tgt_mult · entry + tgt_off``): an absolute level uses a zero multiplier. Path rules when the stop and the
target are both inside one session: ``stop_first`` (primary, conservative), ``ohlc`` (the level closer to the
open first; an open beyond the target exits there) and ``target_first`` (optimism bound); such sessions are
flagged ``ambiguous``. On a fill session filled intraday at the limit (open above it), the order of the
session's prices before the fill is unknown: the stop wins under every rule, the target needs a target-on-fill
spec, and the highest high starts from the entry (every price before the fill was above the limit, so only
the low of that session is known to come after it). Dividends (ex-date after the fill): stop and target lowered
by the amount, cash credited per share.

Buy stop-limit orders (registration n° 4, ``Orders.stop_level`` finite): an open between the trigger level and the
limit (maximum price) fills at the open; an open under the level fills at the level when the session's high reaches
it (the high then comes after the fill: it counts, and a target may be hit on the fill session, the stop first); an
open above the limit cancels the order (triggered out of its range, never chased). ``ExitSpec.bracket_at_fill``:
a fill outside ``stop_exec < entry < target`` is no trade (status ``INVALID``, decided on the fill price only).

A trade whose instrument history ends during the hold (``Bars.ended``: no bar in the last sessions of the
panel — delisting, takeover, suspension) is closed at the last available close with reason ``DELISTED`` and
counted (survivorship: dropping it would keep only the survivors); the pipeline reports a stressed variant
where that exit loses 100 % of the notional. A history that only stops at the end of the data stays
``UNKNOWN`` (never counted).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import numpy.typing as npt

from app.domain.research.panel import F64, I64, Bool, Features, Packing, Panel, pack

I8 = npt.NDArray[np.int8]

UNFILLED, CANCELLED, CLOSED, UNKNOWN, CORPORATE_ACTION, INVALID, DIVIDEND_SUSPECT = 0, 1, 2, 3, 4, 5, 6
STOP, GAP_STOP, TARGET, TIME, TRAIL, MA20, COND_TIME, DELISTED = 0, 1, 2, 3, 4, 5, 6, 7
REASONS = {STOP: "stop", GAP_STOP: "gap_stop", TARGET: "target", TIME: "time", TRAIL: "trail", MA20: "ma20",
           COND_TIME: "cond_time", DELISTED: "delisted"}  # fmt: skip
SLIPPED_EXITS = (STOP, GAP_STOP, TRAIL, MA20)  # exit prices already slipped by the engine (stop / market orders)
ENDED_MARGIN_ROWS = 5  # no bar in the last 5 calendar rows of the panel: the instrument's history has ended
PathRule = Literal["stop_first", "ohlc", "target_first"]


@dataclass(frozen=True)
class Bars:
    """Packed bars of one panel (own sessions, shape ``(L, N)``) and the per-bar events."""

    open: F64
    high: F64
    low: F64
    close: F64
    atr14: F64
    sma20: F64
    div: F64  # dividend amount on its ex-date bar (raw price units), 0 otherwise
    ca: Bool  # corporate action (split…) on that bar
    n_bars: I64  # (N,)
    ended: Bool | None = None  # (N,) history ends inside the panel (delisted…); None: never

    @property
    def width(self) -> int:
        return int(self.open.shape[1])


def history_ended(pk: Packing, t_len: int, margin: int = ENDED_MARGIN_ROWS) -> Bool:
    """Instruments whose last bar is more than ``margin`` calendar rows before the end of the panel."""
    n = pk.n_bars.size
    last = np.full(n, -1, dtype=np.int64)
    has = pk.n_bars > 0
    cols = np.arange(n)[has]
    last[has] = pk.order[pk.n_bars[has] - 1, cols]
    return np.asarray(has & (last < t_len - 1 - margin), dtype=np.bool_)


def bars_from(p: Panel, f: Features, ca: Bool, div: F64) -> Bars:
    pk = f.pk
    return Bars(
        np.ascontiguousarray(pack(p.open, pk)),
        np.ascontiguousarray(pack(p.high, pk)),
        np.ascontiguousarray(pack(p.low, pk)),
        np.ascontiguousarray(pack(p.close, pk)),
        np.ascontiguousarray(pack(f.atr14, pk)),
        np.ascontiguousarray(pack(f.sma20, pk)),
        np.ascontiguousarray(np.nan_to_num(pack(div, pk))),
        np.ascontiguousarray(pack(ca.astype(np.float64), pk) == 1.0),
        pk.n_bars,
        history_ended(pk, p.shape[0]),
    )


@dataclass(frozen=True)
class Orders:
    """Entry orders: ``sig_pos`` is the own-bar position of the signal session (the order works from the
    next one), ``limit`` the maximum entry price, ``cancel_below`` the invalidation level (NaN: none)."""

    inst: I64
    sig_pos: I64
    limit: F64
    cancel_below: F64
    stop_mult: F64
    stop_off: F64
    tgt_mult: F64
    tgt_off: F64
    cond_mfe: F64  # conditional time stop: exit when (highest high / entry − 1) < this at the session (NaN: no)
    validity: int
    stop_level: F64 | None = None  # buy stop-limit trigger level (NaN or None: a plain limit order)

    @property
    def size(self) -> int:
        return int(self.inst.size)

    def take(self, idx: I64) -> Orders:
        return Orders(
            self.inst[idx], self.sig_pos[idx], self.limit[idx], self.cancel_below[idx], self.stop_mult[idx],
            self.stop_off[idx], self.tgt_mult[idx], self.tgt_off[idx], self.cond_mfe[idx], self.validity,
            None if self.stop_level is None else self.stop_level[idx],
        )  # fmt: skip


@dataclass(frozen=True)
class ExitSpec:
    name: str
    max_hold: int  # sessions, the fill session counting as the first
    stop_active: bool = True  # False: the stop only sizes the trade (time exit X1)
    target_r: float = 0.0  # > 0: target = entry + target_r · (entry − stop_exec)
    trail_atr: float = 0.0  # chandelier: stop raised to highest high − k · ATR14 (from the next session)
    ma20_closes: int = 0  # exit at the next open after n consecutive closes under the SMA20
    cond_session: int = 0  # conditional time stop at the close of this session (uses Orders.cond_mfe)
    breakeven_r: float = 0.0  # stop moved to the entry once the high reached entry + k · initial risk
    target_on_fill_bar: Literal["always", "if_open_fill"] = "if_open_fill"
    require_bracket: bool = False  # production: no order unless stop_exec < limit < target
    bracket_at_fill: bool = False  # registration n° 4: a fill outside stop_exec < entry < target is no trade


@dataclass(frozen=True)
class SimResult:
    status: I8
    fill_pos: I64
    exit_pos: I64
    entry: F64
    exit_px: F64
    stop_init: F64
    stop_exec_init: F64
    target_init: F64
    hi: F64
    lo: F64
    div_cash: F64  # dividends received per share (gross)
    reason: I8
    ambiguous: Bool
    at_open: Bool
    rising: Bool | None = None  # filled by a buy stop-limit during the session (its high follows the fill)

    @property
    def closed(self) -> Bool:
        return np.asarray(self.status == CLOSED, dtype=np.bool_)

    @property
    def gross_return(self) -> F64:
        """(exit + dividends) / entry − 1 (dividends excluded when the simulation ignored them)."""
        with np.errstate(invalid="ignore", divide="ignore"):
            return (self.exit_px + self.div_cash) / self.entry - 1

    @property
    def risk_fraction(self) -> F64:
        with np.errstate(invalid="ignore", divide="ignore"):
            return (self.entry - self.stop_exec_init) / self.entry


def simulate(
    bars: Bars,
    od: Orders,
    spec: ExitSpec,
    slippage: float,
    path_rule: PathRule = "stop_first",
    dividends: bool = True,
    chunk: int = 400_000,
) -> SimResult:
    """Simulate every order (chunked for bounded memory)."""
    parts = [
        _simulate(bars, od.take(np.arange(a, min(a + chunk, od.size))), spec, slippage, path_rule, dividends)
        for a in range(0, max(od.size, 1), chunk)
    ]
    if len(parts) == 1:
        return parts[0]
    return SimResult(*(np.concatenate([getattr(r, k) for r in parts]) for k in SimResult.__dataclass_fields__))


def _simulate(bars: Bars, od: Orders, spec: ExitSpec, slip: float, rule: PathRule, dividends: bool) -> SimResult:
    n, w = od.size, bars.width
    o_all, h_all, l_all, c_all = bars.open.ravel(), bars.high.ravel(), bars.low.ravel(), bars.close.ravel()
    atr_all, ma_all, div_all, ca_all = bars.atr14.ravel(), bars.sma20.ravel(), bars.div.ravel(), bars.ca.ravel()
    status = np.zeros(n, dtype=np.int8)
    fill_pos = np.full(n, -1, dtype=np.int64)
    exit_pos = np.full(n, -1, dtype=np.int64)
    entry = np.full(n, np.nan)
    at_open = np.zeros(n, dtype=bool)
    rising = np.zeros(n, dtype=bool)
    ca_hit = np.zeros(n, dtype=bool)
    pending = np.ones(n, dtype=bool)
    if spec.require_bracket:
        s_l = od.stop_mult * od.limit - od.stop_off
        t_l = od.tgt_mult * od.limit + od.tgt_off
        ok = (s_l * (1 - slip) < od.limit) & (od.limit < t_l)
        status[~ok] = INVALID
        pending &= ok
    for k in range(1, od.validity + 1):
        idx = np.nonzero(pending)[0]
        if idx.size == 0:
            break
        p = od.sig_pos[idx] + k
        inst = od.inst[idx]
        avail = p < bars.n_bars[inst]
        pending[idx[~avail]] = False  # no session left: never filled
        idx, p, inst = idx[avail], p[avail], inst[avail]
        g = p * w + inst
        o, lo = o_all[g], l_all[g]
        ca_hit[idx] |= ca_all[g]
        with np.errstate(invalid="ignore"):
            cancel = o <= od.cancel_below[idx]
            lim = od.limit[idx]
            lv = od.stop_level[idx] if od.stop_level is not None else np.full(idx.size, np.nan)
            stp = np.isfinite(lv)
            cancel |= stp & (o > lim)  # stop-limit: an open above its maximum price cancels it
            fo = ~cancel & np.where(stp, o >= lv, o <= lim)
            fl = ~cancel & ~fo & np.where(stp, h_all[g] >= lv, lo < lim)
        status[idx[cancel]] = CANCELLED
        pending[idx[cancel]] = False
        got = fo | fl
        sel = idx[got]
        fill_pos[sel] = p[got]
        entry[sel] = np.where(fo, o, np.where(stp, lv, lim))[got]
        at_open[sel] = fo[got]
        rising[sel] = (fl & stp)[got]
        pending[sel] = False
    if spec.bracket_at_fill:  # a fill outside its stop / target frame is no trade (fill price only)
        fi = np.nonzero(fill_pos >= 0)[0]
        e0 = entry[fi]
        s0 = od.stop_mult[fi] * e0 - od.stop_off[fi]
        t0 = od.tgt_mult[fi] * e0 + od.tgt_off[fi]
        with np.errstate(invalid="ignore"):
            bad = ~(s0 * (1 - slip) < e0) | (np.isfinite(t0) & ~(e0 < t0))
        status[fi[bad]] = INVALID
        fill_pos[fi[bad]] = -1
        entry[fi[bad]] = np.nan

    exit_px = np.full(n, np.nan)
    reason = np.full(n, -1, dtype=np.int8)
    ambiguous = np.zeros(n, dtype=bool)
    hi = np.full(n, np.nan)
    lo_ = np.full(n, np.nan)
    cash = np.zeros(n)
    stop_init = np.full(n, np.nan)
    stop_exec_init = np.full(n, np.nan)
    target_init = np.full(n, np.nan)
    fidx = np.nonzero(fill_pos >= 0)[0]
    e = entry[fidx]
    stop = od.stop_mult[fidx] * e - od.stop_off[fidx]
    stop_exec0 = stop * (1 - slip)
    if spec.target_r > 0:
        tgt = e + spec.target_r * (e - stop_exec0)
    else:
        tgt = od.tgt_mult[fidx] * e + od.tgt_off[fidx]
    stop_init[fidx], stop_exec_init[fidx], target_init[fidx] = stop, stop_exec0, tgt
    has_stop = np.isfinite(stop) & spec.stop_active
    cur_stop = np.where(has_stop, stop, -np.inf)
    cur_tgt = np.where(np.isfinite(tgt), tgt, np.inf)
    f_hi, f_lo, f_cash = e.copy(), e.copy(), np.zeros(fidx.size)
    f_cnt = np.zeros(fidx.size, dtype=np.int64)
    f_pend = np.zeros(fidx.size, dtype=bool)
    f_ca = ca_hit[fidx].copy()
    f_inst, f_fill, f_open, f_rise = od.inst[fidx], fill_pos[fidx], at_open[fidx], rising[fidx]
    f_cond = od.cond_mfe[fidx]
    active = np.ones(fidx.size, dtype=bool)
    out_pos = np.full(fidx.size, -1, dtype=np.int64)
    out_px = np.full(fidx.size, np.nan)
    out_rsn = np.full(fidx.size, -1, dtype=np.int8)
    out_amb = np.zeros(fidx.size, dtype=bool)
    out_status = np.full(fidx.size, UNKNOWN, dtype=np.int8)
    always = spec.target_on_fill_bar == "always"
    ended = np.zeros(bars.width, dtype=bool) if bars.ended is None else bars.ended
    for s in range(spec.max_hold):
        a = np.nonzero(active)[0]
        if a.size == 0:
            break
        p = f_fill[a] + s
        avail = p < bars.n_bars[f_inst[a]]
        gone = a[~avail]
        active[gone] = False
        # history ended inside the panel (delisting…): closed at the last available close, counted and flagged;
        # history stopped by the end of the data: outcome unknown, never counted
        dl = gone[ended[f_inst[gone]]]
        last_pos = bars.n_bars[f_inst[dl]] - 1
        out_pos[dl], out_px[dl], out_rsn[dl] = last_pos, c_all[last_pos * w + f_inst[dl]], DELISTED
        out_status[dl] = CLOSED
        a, p = a[avail], p[avail]
        g = p * w + f_inst[a]
        o, h, lw, c = o_all[g], h_all[g], l_all[g], c_all[g]
        if s > 0:
            f_ca[a] |= ca_all[g]
            if dividends:
                dv = div_all[g]
                cur_stop[a] = cur_stop[a] - dv
                cur_tgt[a] = cur_tgt[a] - dv
                f_cash[a] += dv
        st, tg = cur_stop[a], cur_tgt[a]
        # fill session filled intraday at the limit: the high may precede the fill (MFE, X6, trail, breakeven
        # start from the entry); every price below the limit — the low — follows it
        intraday = (~f_open[a]) if s == 0 else np.zeros(a.size, dtype=bool)
        pre = intraday & ~f_rise[a]  # a stop-limit filled on the way up: its high follows the fill
        hh = np.where(pre, f_hi[a], np.maximum(f_hi[a], h))
        ll = np.minimum(f_lo[a], lw)
        f_hi[a], f_lo[a] = hh, ll
        done = np.zeros(a.size, dtype=bool)
        px = np.full(a.size, np.nan)
        rsn = np.full(a.size, -1, dtype=np.int8)
        if s > 0:
            gap = o <= st
            px[gap], rsn[gap] = o[gap] * (1 - slip), GAP_STOP
            done |= gap
            if spec.ma20_closes > 0:
                m = ~done & f_pend[a]
                px[m], rsn[m] = o[m] * (1 - slip), MA20
                done |= m
        rest = ~done
        sh = rest & (lw <= st)
        to = rest & (s > 0) & (o >= tg)
        fill_ok = np.ones(a.size, dtype=bool) if (s > 0 or always) else (f_open[a] | f_rise[a])
        th = rest & fill_ok & (h >= tg)
        amb = sh & (to | th)
        if rule == "stop_first":
            stop_wins = sh
        elif rule == "target_first":
            stop_wins = sh & ~(to | th)
        else:
            stop_wins = sh & ~to & ~(th & ((o - st) >= (tg - o)))
        stop_wins |= sh & intraday  # intraday fill: the pre-fill open says nothing of the order after the fill
        raised = st > stop_init[fidx[a]]
        px[stop_wins] = st[stop_wins] * (1 - slip)
        rsn[stop_wins] = np.where(raised[stop_wins], TRAIL, STOP)
        tw = rest & ~stop_wins & (to | th)
        px[tw] = np.where(to, o, tg)[tw]
        rsn[tw] = TARGET
        done |= stop_wins | tw
        out_amb[a] |= amb
        if spec.cond_session > 0 and s == spec.cond_session - 1:
            with np.errstate(invalid="ignore"):
                cm = ~done & ((hh / e[a] - 1) < f_cond[a])
            px[cm], rsn[cm] = c[cm], COND_TIME
            done |= cm
        if s == spec.max_hold - 1:
            last = ~done
            px[last], rsn[last] = c[last], TIME
            done |= last
        surv = ~done
        if spec.trail_atr > 0:
            new = hh - spec.trail_atr * atr_all[g]
            upd = surv & np.isfinite(new) & np.isfinite(st)
            cur_stop[a[upd]] = np.maximum(st[upd], new[upd])
        if spec.breakeven_r > 0:
            be = surv & (hh >= e[a] + spec.breakeven_r * (e[a] - stop_exec0[a])) & np.isfinite(st)
            cur_stop[a[be]] = np.maximum(cur_stop[a[be]], e[a][be])
        if spec.ma20_closes > 0:
            with np.errstate(invalid="ignore"):
                below = c < ma_all[g]
            f_cnt[a] = np.where(below, f_cnt[a] + 1, 0)
            f_pend[a] = f_cnt[a] >= spec.ma20_closes
        fin = a[done]
        out_pos[fin], out_px[fin], out_rsn[fin] = p[done], px[done], rsn[done]
        out_status[fin] = CLOSED
        active[fin] = False
    out_status[(out_status == CLOSED) & f_ca] = CORPORATE_ACTION
    status[fidx] = out_status
    exit_pos[fidx], exit_px[fidx], reason[fidx], ambiguous[fidx] = out_pos, out_px, out_rsn, out_amb
    hi[fidx], lo_[fidx], cash[fidx] = f_hi, f_lo, f_cash
    return SimResult(
        status, fill_pos, exit_pos, entry, exit_px, stop_init, stop_exec_init, target_init, hi, lo_, cash, reason,
        ambiguous, at_open, rising,
    )  # fmt: skip
