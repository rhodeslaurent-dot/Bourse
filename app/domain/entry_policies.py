"""Entry policies replayed on the detectors' setups (docs/34 §34.10, exploratory, 29/10/2026). Pure: no I/O, no LLM.

On 29/10/2026 every opportunity's limit (maximum entry price, R/R ≥ ``admission.min_reward_risk`` net to the FIRST
target) was below the price: nothing is ever bought. This module turns one setup (seen at the close of its day)
into the order of each policy, fills it on the following sessions only, and exits it with one shared logic:

- P0 current: limit at the maximum entry price on target 1, valid ``entry.expiry_sessions``;
- P1 next open: buy at the next open if it is at most close + ``next_open_gap_atr`` ATR;
- P2 confirmation buy-stop: max(trigger, the day's high) + 1 tick, valid ``confirm_sessions``, maximum price
  level × (1 + ``confirm_max_atr`` · ATR / close);
- P2' the J+1 stop-limit of docs/06 §6.3: threshold = pivot (D2) or trigger × (1 + ``stop_limit_buffer``), limit =
  the maximum entry price, valid one session;
- P3 horizon target: as P0 but the ratio is measured on the farthest exploitable target (last target or 52-week
  high), ``partial_share`` sold at target 1;
- P4 shallow pullback: limit at close − ``pullback_atr`` ATR (the MM20 when it lies between that and the close),
  valid ``pullback_sessions``;
- P5 ATR frame: next open (same cap as P1), stop entry − ``frame_stop_atr`` ATR, target entry + ``frame_target_atr``
  ATR (target 1 when higher);
- P5' next open (same cap), stop entry − ``frame_stop_atr`` ATR, no target, time exit after ``time_only_hold``.

Conventions (daily bars: the order of the high and the low inside a session is unknown — never in favour of the
result):
- a limit fills when a later session's low reaches it, at min(open, limit); a session opening at or below the
  stop before any fill cancels the order (setup invalidated); a session without volume fills nothing;
- a buy-stop fills at max(open, level) when that price is ≤ its maximum price; a gap above the maximum price
  cancels the order (the stop-limit is triggered out of its limit and not chased);
- an order at the next open fills at the opening auction price when it is ≤ its cap (an order bounded by a price:
  no entry slippage, ``risk.slippage_pct``), else it is not filled;
- on the fill session the stop is assumed hit **after** the fill; when the stop and a target are both inside one
  session, the **stop** comes first; a limit filled during the session never claims that session's high (the high
  may have come before the fill) — a fill at the open or by a buy-stop does (the high then comes after the fill);
- stop at the setup stop, executed slipped (stop × (1 − ``risk.slippage_pct``)); a gap through the stop exits at the
  open, slipped; a gap above the target exits at the open;
- still open after ``hold`` sessions (fill session included) → exit at the close; not enough history → outcome
  unknown (never counted as a win or a loss).

Statistics (aggregates only — no price leaves the computation): per trade, per signal (an order that did not fill
counts 0 R), and the paired difference with P0 on the same signal; time series by week for a Newey-West t, a
moving-block bootstrap interval and the sign over chronological folds; cells under ``min_n`` observations masked.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

import numpy as np
import numpy.typing as npt

from app.domain.admission import p_max_rr
from app.domain.indicators import Bar

F64 = npt.NDArray[np.float64]

POLICIES = ("P0", "P1", "P2", "P2'", "P3", "P4", "P5", "P5'")
POLICY_FR = {
    "P0": "actuelle : limite au prix maximal (ratio ≥ k sur l'objectif 1)",
    "P1": "ouverture suivante (plafond clôture + 1 ATR)",
    "P2": "achat sur franchissement confirmé (stop d'achat, 3 séances, plafond + 0,5 ATR)",
    "P2'": "ordre à plage de déclenchement J+1 de docs/06 (seuil pivot ou déclencheur × 1,003, limite au prix maximal)",
    "P3": "objectif d'horizon (ratio sur l'objectif le plus lointain, moitié vendue à l'objectif 1)",
    "P4": "repli léger (limite clôture − 0,5 ATR, ou MM20)",
    "P5": "cadre ATR (ouverture suivante, stop −2 ATR, objectif +3 ATR)",
    "P5'": "ouverture suivante, stop −2 ATR, sans objectif, sortie au temps après 10 séances",
}
NO_ORDER_FR = {
    "sans_objectif": "pas d'objectif exploitable",
    "limite_sous_stop": "limite au niveau du stop ou dessous",
    "limite_sous_seuil": "prix maximal au niveau du seuil de déclenchement ou dessous",
    "niveau_au_dessus_objectif": "niveau d'entrée au-dessus de l'objectif",
    "atr_inconnu": "ATR inconnue",
}
NO_FILL_FR = {
    "jamais_atteint": "prix jamais atteint pendant la validité",
    "invalide": "ouverture au stop ou dessous avant exécution (configuration invalidée)",
    "ecart_au_dessus": "ouverture au-dessus du prix maximal (non poursuivi)",
    "sans_volume": "séance sans volume",
    "hors_cadre": "exécution hors du cadre stop / objectif",
    "fin_des_donnees": "fin de l'historique avant la fin de validité",
}


class OrderKind(StrEnum):
    LIMIT = "limite"
    NEXT_OPEN = "ouverture"
    BUY_STOP = "stop_achat"


@dataclass(frozen=True)
class PolicyParams:
    """The study's hypotheses (request of 29/10/2026, printed in the report) and the production values it reads
    from ``config/params.yaml`` (slippage, admission ratio, order validity)."""

    slippage_pct: float  # risk.slippage_pct
    min_reward_risk: float  # admission.min_reward_risk
    expiry_sessions: int  # entry.expiry_sessions
    next_open_gap_atr: float = 1.0
    confirm_sessions: int = 3
    confirm_max_atr: float = 0.5
    stop_limit_buffer: float = 0.003
    pullback_atr: float = 0.5
    pullback_sessions: int = 3
    frame_stop_atr: float = 2.0
    frame_target_atr: float = 3.0
    time_only_hold: int = 10
    partial_share: float = 0.5

    @property
    def max_valid(self) -> int:
        return max(self.expiry_sessions, self.confirm_sessions, self.pullback_sessions, 1)


@dataclass(frozen=True)
class SetupView:
    """What the policies may read of a setup: values known at the close of the setup day only."""

    close: float
    high: float  # the setup day's high
    atr: float | None  # ATR14 on the window ending at the setup day
    mm20: float | None
    high_52w: float | None
    trigger: float  # the detector's entry level
    p_max_technical: float  # the detector's own cap
    stop: float  # the setup's structural stop (before slippage)
    targets: tuple[float, ...]  # exploitable targets, nearest first
    fee_fraction: float  # round-trip fees of the admission (fraction of the notional), as the backtest
    p_max_stored: float | None = None  # a stored proposal: the limit the tool actually published (P0, P2')
    pivot: float | None = None  # D2: the 60-session pivot (P2' threshold)

    def scaled(self, k: float) -> SetupView:
        """The same geometry around a close × ``k`` (placebo: every level keeps its distance to the close in %)."""

        def opt(x: float | None) -> float | None:
            return None if x is None else x * k

        return SetupView(
            self.close * k, self.high * k, opt(self.atr), opt(self.mm20), opt(self.high_52w), self.trigger * k,
            self.p_max_technical * k, self.stop * k, tuple(t * k for t in self.targets), self.fee_fraction,
            opt(self.p_max_stored), opt(self.pivot),
        )  # fmt: skip


@dataclass(frozen=True)
class Order:
    policy: str
    kind: OrderKind
    level: float  # limit price, buy-stop threshold, or the setup close (next open)
    max_price: float  # highest acceptable fill price (sizing price, CLAUDE.md rule 7)
    valid: int  # sessions
    stop: float | None  # None: relative to the fill (ATR frame)
    target: float  # math.inf: no target
    partial_target: float | None = None
    frame: tuple[float, float] | None = None  # (stop distance, target distance) from the fill
    hold: int | None = None  # the policy's own time exit (else the setup's horizon)

    def planned_stop(self) -> float:
        """The stop the order is sized on: the setup stop, or the frame's stop under the maximum price."""
        if self.stop is not None:
            return self.stop
        assert self.frame is not None
        return self.max_price - self.frame[0]


@dataclass(frozen=True)
class Fill:
    index: int  # in the bars after the setup day (0 = next session)
    price: float
    at_open: bool
    rising: bool  # filled by a buy-stop during the session: the session's high comes after the fill


@dataclass(frozen=True)
class Exit:
    index: int  # exit session (same indexing as the bars passed)
    reason: str  # stop | gap_stop | target | time | en_cours (data end, ``open_ok`` only)
    gross_return: float  # weighted over the legs: Σ share × (price / entry − 1)
    risk_fraction: float  # (entry − slipped stop) / entry
    partial: bool  # a first part was sold at the partial target
    sessions: int  # held, fill session included

    @property
    def gross_r(self) -> float:
        return self.gross_return / self.risk_fraction

    @property
    def closed(self) -> bool:
        return self.reason != "en_cours"


@dataclass(frozen=True)
class Outcome:
    order: Order
    fill: Fill | None
    pending_end: int  # last session the order was pending (−1: none)
    exit: Exit | None  # None: not filled, or the data end before the outcome is known
    stop: float | None = None  # effective stop and target (after the fill for a frame)
    target: float | None = None
    no_fill: str | None = None  # why the order did not fill (NO_FILL_FR)

    @property
    def filled(self) -> bool:
        return self.fill is not None


def net_r(gross_return: float, risk_fraction: float, cost_fraction: float) -> float:
    """R after the round-trip costs, as a fraction of the notional (as ``TradeResult.net_r``)."""
    return (gross_return - cost_fraction) / risk_fraction


def tick_size(price: float) -> float:
    """Approximate quotation step: a power of ten near 5 bp of the price (0,01 € from 20 to 199 €, 0,001 € from 2
    to 19 €). The real step depends on the liquidity band of the value (MiFID II table), unknown in the history."""
    if price <= 0:
        return 0.0
    return float(10 ** math.floor(math.log10(price * 0.0005)))


def far_target(sv: SetupView) -> float | None:
    """Farthest exploitable target: the last target of the setup, or the 52-week high when it lies above it."""
    if not sv.targets:
        return None
    far = sv.targets[-1]
    if sv.high_52w is not None and sv.high_52w > far:
        far = sv.high_52w
    return far


def p_max_t1(sv: SetupView, min_reward_risk: float) -> float:
    """The production maximum entry price (R4): the stored one, else min(ratio price on target 1, detector cap)."""
    if sv.p_max_stored is not None:
        return sv.p_max_stored
    return min(p_max_rr(sv.targets[0], sv.stop, sv.fee_fraction, min_reward_risk), sv.p_max_technical)


def plan(policy: str, sv: SetupView, pp: PolicyParams) -> Order | str:
    """The order of ``policy`` for this setup, or the reason there is none (NO_ORDER_FR)."""
    if not sv.targets:
        return "sans_objectif"
    t1 = sv.targets[0]
    k = pp.min_reward_risk
    if policy == "P0":
        pm = p_max_t1(sv, k)
        if pm <= sv.stop:
            return "limite_sous_stop"
        if pm >= t1:
            return "niveau_au_dessus_objectif"
        return Order(policy, OrderKind.LIMIT, pm, pm, pp.expiry_sessions, sv.stop, t1)
    if policy == "P2'":
        pm = p_max_t1(sv, k)
        level = (sv.pivot if sv.pivot is not None else sv.trigger) * (1 + pp.stop_limit_buffer)
        if level <= sv.stop:
            return "limite_sous_stop"
        if pm <= level:
            return "limite_sous_seuil"
        if pm >= t1:
            return "niveau_au_dessus_objectif"
        return Order(policy, OrderKind.BUY_STOP, level, pm, 1, sv.stop, t1)
    if policy == "P3":
        far = far_target(sv) or t1
        pm = min(p_max_rr(far, sv.stop, sv.fee_fraction, k), sv.p_max_technical)
        if pm <= sv.stop:
            return "limite_sous_stop"
        if pm >= far:
            return "niveau_au_dessus_objectif"
        partial = t1 if pm < t1 < far else None
        return Order(policy, OrderKind.LIMIT, pm, pm, pp.expiry_sessions, sv.stop, far, partial)
    atr = sv.atr if sv.atr is not None and sv.atr > 0 else None
    if atr is None or sv.close <= 0:
        return "atr_inconnu"
    cap = sv.close + pp.next_open_gap_atr * atr
    if policy == "P1":
        return Order(policy, OrderKind.NEXT_OPEN, sv.close, cap, 1, sv.stop, t1)
    if policy == "P2":
        base = max(sv.trigger, sv.high)
        level = base + tick_size(base)
        if level >= t1:
            return "niveau_au_dessus_objectif"
        if level <= sv.stop:
            return "limite_sous_stop"
        max_price = level * (1 + pp.confirm_max_atr * atr / sv.close)
        return Order(policy, OrderKind.BUY_STOP, level, max_price, pp.confirm_sessions, sv.stop, t1)
    if policy == "P4":
        limit = sv.close - pp.pullback_atr * atr
        if sv.mm20 is not None and limit < sv.mm20 < sv.close:
            limit = sv.mm20
        if limit <= sv.stop:
            return "limite_sous_stop"
        if limit >= t1:
            return "niveau_au_dessus_objectif"
        return Order(policy, OrderKind.LIMIT, limit, limit, pp.pullback_sessions, sv.stop, t1)
    if policy == "P5":
        frame = (pp.frame_stop_atr * atr, pp.frame_target_atr * atr)
        return Order(policy, OrderKind.NEXT_OPEN, sv.close, cap, 1, None, t1, frame=frame)
    if policy == "P5'":
        frame = (pp.frame_stop_atr * atr, math.inf)
        return Order(policy, OrderKind.NEXT_OPEN, sv.close, cap, 1, None, math.inf, frame=frame, hold=pp.time_only_hold)
    raise ValueError(f"politique inconnue : {policy}")


def fill_order(order: Order, bars: Sequence[Bar]) -> tuple[Fill | None, int, str | None]:
    """First fill of ``order`` on ``bars`` (the sessions **after** the setup day). Returns (fill, last pending
    index, reason of no fill)."""
    last = -1
    for i, b in enumerate(bars[: order.valid]):
        last = i
        if order.stop is not None and b.open <= order.stop:
            return None, i, "invalide"  # the setup is invalidated before the order could fill
        if b.volume <= 0:
            if order.kind == OrderKind.NEXT_OPEN:
                return None, i, "sans_volume"
            continue
        if order.kind == OrderKind.LIMIT:
            if b.low <= order.level:
                return Fill(i, min(b.open, order.level), b.open <= order.level, False), i, None
        elif order.kind == OrderKind.NEXT_OPEN:
            if b.open <= order.max_price:
                return Fill(i, b.open, True, False), i, None
            return None, i, "ecart_au_dessus"
        else:  # buy-stop (stop-limit)
            if b.open > order.max_price:
                return None, i, "ecart_au_dessus"  # triggered out of its limit: cancelled, never chased
            if b.open >= order.level:
                return Fill(i, b.open, True, False), i, None
            if b.high >= order.level:
                return Fill(i, order.level, False, True), i, None
    if len(bars) < order.valid:
        return None, last, "fin_des_donnees"
    return None, last, "jamais_atteint"


def run_exit(
    bars: Sequence[Bar],
    start: int,
    entry: float,
    stop: float,
    target: float,
    slippage_pct: float,
    hold: int,
    partial_target: float | None = None,
    partial_share: float = 0.5,
    pre_open: bool = False,
    target_first_session: bool = True,
    open_ok: bool = False,
) -> Exit | None:
    """Exit of a position entered at ``entry`` on session ``start``. ``pre_open``: held before that session opens
    (its gaps count); ``target_first_session``: the first session's high may reach a target (fill at the open or by
    a buy-stop). None when the slipped stop is not under the entry, or when the data end before the outcome is
    known — unless ``open_ok`` (a stored proposal still running: marked to the last close, reason « en_cours »)."""
    stop_exec = stop * (1 - slippage_pct)
    if entry <= 0 or not (stop_exec < entry < target):
        return None
    risk = (entry - stop_exec) / entry
    left = 1.0
    done = 0.0  # Σ share × return of the legs already sold
    pending = partial_target if partial_target is not None and entry < partial_target < target else None
    end = min(len(bars), start + hold)
    for j in range(start, end):
        b = bars[j]
        gaps = j > start or pre_open
        sessions = j - start + 1
        if gaps and b.open <= stop:
            px = b.open * (1 - slippage_pct)
            return Exit(j, "gap_stop", done + left * (px / entry - 1), risk, left < 1, sessions)
        if b.low <= stop:
            return Exit(j, "stop", done + left * (stop_exec / entry - 1), risk, left < 1, sessions)
        if j == start and not target_first_session:
            continue
        if pending is not None:
            hit = b.open if gaps and b.open >= pending else pending if b.high >= pending else None
            if hit is not None:
                done += partial_share * (hit / entry - 1)
                left -= partial_share
                pending = None
        if gaps and b.open >= target:
            return Exit(j, "target", done + left * (b.open / entry - 1), risk, left < 1, sessions)
        if b.high >= target:
            return Exit(j, "target", done + left * (target / entry - 1), risk, left < 1, sessions)
    if start + hold > len(bars):
        if open_ok and end > start:
            j = end - 1
            return Exit(j, "en_cours", done + left * (bars[j].close / entry - 1), risk, left < 1, j - start + 1)
        return None  # not enough history: never counted as a win or a loss
    j = start + hold - 1
    return Exit(j, "time", done + left * (bars[j].close / entry - 1), risk, left < 1, hold)


def effective_frame(order: Order, entry: float) -> tuple[float, float]:
    """(stop, target) of the position: the order's own, or the ATR frame placed from the executed price."""
    if order.frame is not None:
        return entry - order.frame[0], max(order.target, entry + order.frame[1])
    assert order.stop is not None
    return order.stop, order.target


def simulate(order: Order, bars: Sequence[Bar], pp: PolicyParams, hold: int, open_ok: bool = False) -> Outcome:
    """Fill then exit one order; a fill whose stop / target frame is inconsistent (e.g. an open at or above the
    target) is no trade (« hors_cadre »)."""
    f, pending_end, why = fill_order(order, bars)
    if f is None:
        return Outcome(order, None, pending_end, None, no_fill=why)
    stop, target = effective_frame(order, f.price)
    if not (stop * (1 - pp.slippage_pct) < f.price < target):
        return Outcome(order, None, pending_end, None, no_fill="hors_cadre")
    ex = run_exit(
        bars, f.index, f.price, stop, target, pp.slippage_pct, order.hold or hold, order.partial_target,
        pp.partial_share, pre_open=False, target_first_session=f.at_open or f.rising, open_ok=open_ok,
    )  # fmt: skip
    return Outcome(order, f, pending_end, ex, stop, target)


def counterfactual(order: Order, close: float, bars: Sequence[Bar], pp: PolicyParams, hold: int) -> Exit | None:
    """The same stop and targets as if bought at the setup close (adverse selection: what the orders that did not
    fill would have given)."""
    stop, target = effective_frame(order, close)
    return run_exit(
        bars, 0, close, stop, target, pp.slippage_pct, order.hold or hold, order.partial_target, pp.partial_share,
        pre_open=True, target_first_session=True,
    )  # fmt: skip


def excursions(bars: Sequence[Bar], ref: float, n: int) -> tuple[float, float] | None:
    """Best and worst excursion (fractions of ``ref``) over the first ``n`` sessions; None when fewer are known."""
    if ref <= 0 or n <= 0 or len(bars) < n:
        return None
    window = bars[:n]
    return max(b.high for b in window) / ref - 1, min(b.low for b in window) / ref - 1


# --- statistics on weekly series ---------------------------------------------------------------------------


def week_index(ordinal: int) -> int:
    """Week number of a date ordinal (weeks start on Monday: ordinal 1 is Monday 0001-01-01)."""
    return (ordinal - 1) // 7


def _series(weeks: dict[int, list[float]]) -> tuple[F64, F64]:
    """(Σx, n) per week, every week from the first to the last of the cell (empty weeks included)."""
    lo, hi = min(weeks), max(weeks)
    s = np.zeros(hi - lo + 1)
    n = np.zeros(hi - lo + 1)
    for k, (a, b) in weeks.items():
        s[k - lo] = a
        n[k - lo] = b
    return s, n


def newey_west_t(s: F64, n: F64, lag: int) -> float | None:
    """t of the mean Σs / Σn with a Newey-West (Bartlett) variance of the weekly contributions s_w − n_w·mean:
    signals of neighbouring weeks share the same price path while their trades overlap (``lag`` weeks)."""
    tot = float(n.sum())
    if tot < 2 or s.size < 2:
        return None
    m = float(s.sum()) / tot
    u = s - n * m
    v = float(u @ u)
    for lag_k in range(1, min(lag, u.size - 1) + 1):
        v += 2 * (1 - lag_k / (lag + 1)) * float(u[lag_k:] @ u[:-lag_k])
    var = v / (tot * tot)
    return m / math.sqrt(var) if var > 1e-18 else None


def block_bootstrap_ci(s: F64, n: F64, block: int, draws: int, seed: int) -> tuple[float, float] | None:
    """95 % interval of Σs / Σn by a moving-block bootstrap of the weekly series (blocks of ``block`` weeks keep
    the dependence of overlapping trades); fixed seed, reproducible."""
    g = s.size
    if g < 2 or float(n.sum()) < 2:
        return None
    b = min(max(1, block), g)
    k = -(-g // b)
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, g - b + 1, size=(draws, k))
    idx = (starts[:, :, None] + np.arange(b)).reshape(draws, k * b)[:, :g]
    ss, nn = s[idx].sum(axis=1), n[idx].sum(axis=1)
    ok = nn > 0
    if int(ok.sum()) < draws // 2:
        return None
    stat = ss[ok] / nn[ok]
    return float(np.quantile(stat, 0.025)), float(np.quantile(stat, 0.975))


def fold_means(s: F64, n: F64, folds: int = 5) -> list[float | None]:
    """Mean of each of ``folds`` consecutive spans of weeks (chronological stability of the sign)."""
    out: list[float | None] = []
    for part in np.array_split(np.arange(s.size), folds):
        tot = float(n[part].sum()) if part.size else 0.0
        out.append(float(s[part].sum()) / tot if tot else None)
    return out


def weekly_stats(
    weeks: dict[int, list[float]], lag: int, seed: int, min_n: int, draws: int = 1000, bootstrap: bool = True
) -> dict[str, object]:
    """Mean, Newey-West t, block-bootstrap interval and fold signs of a statistic accumulated by week."""
    tot = int(sum(v[1] for v in weeks.values()))
    if tot < min_n or not weeks:
        return {"n": tot, "masque": tot > 0}
    s, n = _series(weeks)
    m = float(s.sum()) / tot
    out: dict[str, object] = {"n": tot, "moyenne": round(m, 4), "t_nw": _r(newey_west_t(s, n, lag), 2)}
    if bootstrap:
        ci = block_bootstrap_ci(s, n, lag + 1, draws, seed)
        out["ic95"] = [round(ci[0], 4), round(ci[1], 4)] if ci else None
        folds = fold_means(s, n)
        out["plis"] = ["?" if x is None else "+" if x > 0 else "−" for x in folds]
        out["plis_positifs"] = sum(1 for x in folds if x is not None and x > 0)
    return out


# --- aggregation (counts and moments only: no price leaves the computation) ------------------------------


@dataclass
class Moments:
    n: int = 0
    total: float = 0.0
    squares: float = 0.0

    def add(self, x: float) -> None:
        self.n += 1
        self.total += x
        self.squares += x * x

    def merge(self, o: Moments) -> None:
        self.n += o.n
        self.total += o.total
        self.squares += o.squares

    @property
    def mean(self) -> float | None:
        return self.total / self.n if self.n else None

    @property
    def t_stat(self) -> float | None:
        if self.n < 2:
            return None
        m = self.total / self.n
        var = (self.squares - self.n * m * m) / (self.n - 1)
        return m / math.sqrt(var / self.n) if var > 1e-18 else None


def cluster_t(weeks: dict[int, list[float]]) -> float | None:
    """t of the mean with a standard error clustered by week ({week: [Σx, n]})."""
    g = len(weeks)
    n = sum(v[1] for v in weeks.values())
    if g < 2 or n < 2:
        return None
    m = sum(v[0] for v in weeks.values()) / n
    var = g / (g - 1) * sum((v[0] - v[1] * m) ** 2 for v in weeks.values()) / (n * n)
    return m / math.sqrt(var) if var > 1e-18 else None


def _r(x: float | None, nd: int = 3) -> float | None:
    return None if x is None else round(x, nd)


def _add_week(weeks: dict[int, list[float]], week: int, x: float) -> None:
    w = weeks.setdefault(week, [0.0, 0.0])
    w[0] += x
    w[1] += 1


def _merge_weeks(a: dict[int, list[float]], b: dict[int, list[float]]) -> None:
    for k, v in b.items():
        w = a.setdefault(k, [0.0, 0.0])
        w[0] += v[0]
        w[1] += v[1]


@dataclass
class CellStats:
    """Aggregates of one cell (arm × policy × detector × venue group × year × status at the setup close)."""

    setups: int = 0
    orders: int = 0
    fills: int = 0
    fills_open: int = 0
    to_fill: Counter[int] = field(default_factory=Counter)  # sessions from the setup to the fill (1 = next)
    trades: int = 0
    wins: int = 0
    gross: Moments = field(default_factory=Moments)
    net_cto: Moments = field(default_factory=Moments)
    net_pea: Moments = field(default_factory=Moments)
    cost_r: Moments = field(default_factory=Moments)
    net_stress: Moments = field(default_factory=Moments)  # costs × 1.5
    gains: float = 0.0
    losses: float = 0.0
    targets: int = 0
    stops: int = 0
    times: int = 0
    partials: int = 0
    holds: Counter[int] = field(default_factory=Counter)
    cf_filled: Moments = field(default_factory=Moments)
    cf_unfilled: Moments = field(default_factory=Moments)
    no_order: Counter[str] = field(default_factory=Counter)
    no_fill: Counter[str] = field(default_factory=Counter)
    weeks: dict[int, list[float]] = field(default_factory=dict)  # net R CTO per trade, by week of the setup
    signal_weeks: dict[int, list[float]] = field(default_factory=dict)  # R per signal (not filled: 0)
    delta_weeks: dict[int, list[float]] = field(default_factory=dict)  # R per signal − P0's, same signal

    def add_setup(self, outcome: Outcome | None, no_order: str | None = None) -> None:
        self.setups += 1
        if outcome is None:
            self.no_order[no_order or "?"] += 1
            return
        self.orders += 1
        if outcome.fill is not None:
            self.fills += 1
            self.fills_open += outcome.fill.at_open
            self.to_fill[outcome.fill.index + 1] += 1
        elif outcome.no_fill:
            self.no_fill[outcome.no_fill] += 1

    def add_trade(self, ex: Exit, net_cto: float, net_pea: float | None, fee_cto: float, week: int) -> None:
        self.trades += 1
        self.wins += net_cto > 0
        self.gross.add(ex.gross_r)
        self.net_cto.add(net_cto)
        self.cost_r.add(fee_cto / ex.risk_fraction)
        self.net_stress.add(net_r(ex.gross_return, ex.risk_fraction, 1.5 * fee_cto))
        if net_pea is not None:
            self.net_pea.add(net_pea)
        if net_cto > 0:
            self.gains += net_cto
        else:
            self.losses -= net_cto
        self.targets += ex.reason == "target"
        self.stops += ex.reason in ("stop", "gap_stop")
        self.times += ex.reason == "time"
        self.partials += ex.partial
        self.holds[ex.sessions] += 1
        _add_week(self.weeks, week, net_cto)

    def add_signal(self, r: float, week: int) -> None:
        _add_week(self.signal_weeks, week, r)

    def add_delta(self, d: float, week: int) -> None:
        _add_week(self.delta_weeks, week, d)

    def add_counterfactual(self, filled: bool, net: float) -> None:
        (self.cf_filled if filled else self.cf_unfilled).add(net)

    def merge(self, o: CellStats) -> None:
        for name in ("setups", "orders", "fills", "fills_open", "trades", "wins", "targets", "stops", "times",
                     "partials"):  # fmt: skip
            setattr(self, name, getattr(self, name) + getattr(o, name))
        self.gains += o.gains
        self.losses += o.losses
        for a, b in (
            (self.gross, o.gross),
            (self.net_cto, o.net_cto),
            (self.net_pea, o.net_pea),
            (self.cost_r, o.cost_r),
            (self.net_stress, o.net_stress),
            (self.cf_filled, o.cf_filled),
            (self.cf_unfilled, o.cf_unfilled),
        ):
            a.merge(b)
        for ca, cb in ((self.holds, o.holds), (self.to_fill, o.to_fill)):
            ca.update(cb)
        self.no_order.update(o.no_order)
        self.no_fill.update(o.no_fill)
        _merge_weeks(self.weeks, o.weeks)
        _merge_weeks(self.signal_weeks, o.signal_weeks)
        _merge_weeks(self.delta_weeks, o.delta_weeks)

    def as_dict(
        self, months: float | None = None, lag: int = 4, seed: int = 7, min_n: int = 20, delta: bool = True,
        bootstrap: bool = True,
    ) -> dict[str, object]:  # fmt: skip
        """Statistics of the cell; every mean, rate or t over fewer than ``min_n`` observations is masked."""
        n = self.trades
        ok_t = n >= min_n
        ok_f = self.fills >= min_n

        def tr(x: object) -> object:
            return x if ok_t else None

        cf_f = self.cf_filled.mean if self.cf_filled.n >= min_n else None
        cf_u = self.cf_unfilled.mean if self.cf_unfilled.n >= min_n else None
        cf_all = Moments()
        cf_all.merge(self.cf_filled)
        cf_all.merge(self.cf_unfilled)
        sig = weekly_stats(self.signal_weeks, lag, seed, min_n, bootstrap=False)
        out: dict[str, object] = {
            "setups": self.setups,
            "ordres": self.orders,
            "executes": self.fills,
            "taux_execution": _r(self.fills / self.orders, 4) if self.orders >= min_n else None,
            "part_execution_ouverture": _r(self.fills_open / self.fills, 4) if ok_f else None,
            "delai_execution_moyen": _r(sum(k * v for k, v in self.to_fill.items()) / self.fills, 2) if ok_f else None,
            "delai_execution_median": statistics.median(self.to_fill.elements()) if ok_f else None,
            "trades": n,
            "gagnants": tr(_r(self.wins / n, 4) if n else None),
            "r_brut_moyen": tr(_r(self.gross.mean)),
            "r_net_cto": tr(_r(self.net_cto.mean)),
            "r_net_pea": _r(self.net_pea.mean) if self.net_pea.n >= min_n else None,
            "trades_pea": self.net_pea.n,
            "cout_r_moyen": tr(_r(self.cost_r.mean)),
            "r_net_cto_couts_x1_5": tr(_r(self.net_stress.mean)),
            "duree_mediane_seances": tr(statistics.median(self.holds.elements()) if n else None),
            "part_objectif": tr(_r(self.targets / n, 4) if n else None),
            "part_stop": tr(_r(self.stops / n, 4) if n else None),
            "part_duree": tr(_r(self.times / n, 4) if n else None),
            "part_sortie_partielle": tr(_r(self.partials / n, 4) if n else None),
            "profit_factor": tr(_r(self.gains / self.losses, 3) if self.losses > 0 else None),
            "t_net_cto": tr(_r(self.net_cto.t_stat, 2)),
            "t_net_cto_semaine": tr(_r(cluster_t(self.weeks), 2)),
            "trades_par_mois": _r(n / months, 2) if months else None,
            "r_par_signal": sig,
            "selection_adverse": {
                "cf_executes": _r(cf_f),
                "n_executes": self.cf_filled.n,
                "cf_non_executes": _r(cf_u),
                "n_non_executes": self.cf_unfilled.n,
                "cf_tous": _r(cf_all.mean) if cf_all.n >= min_n else None,
                "n_tous": cf_all.n,
                "ecart": _r(cf_u - cf_f) if cf_f is not None and cf_u is not None else None,
            },
            "sans_ordre": dict(self.no_order),
            "non_executes": dict(self.no_fill),
            "masque": not ok_t and n > 0,
        }
        if delta:
            out["delta_vs_p0"] = weekly_stats(self.delta_weeks, lag, seed, min_n, bootstrap=bootstrap)
        return out


def merged(cells: Iterable[CellStats]) -> CellStats:
    out = CellStats()
    for c in cells:
        out.merge(c)
    return out


# --- standing orders: what the user would actually get each month --------------------------------------


NEVER = 10**9  # a position whose outcome is unknown at the end of the data stays open


@dataclass(frozen=True, slots=True)
class OrderSpan:
    value: int  # id of the value (one order or position per value at a time)
    first_day: int  # ordinal of the first session the order is pending
    end_day: int  # ordinal of the last session it is pending (the fill session when filled)
    fill_day: int | None  # ordinal of the fill session
    exit_day: int | None  # ordinal of the exit session (NEVER: filled, outcome unknown)
    net_r: float | None  # net R (CTO) of the trade, when the outcome is known
    tiebreak: float  # same-day orders: a seeded random order, never the order of the values or of the outcome


def standing_orders_replay(orders: Sequence[OrderSpan], max_standing: int, max_lines: int) -> list[bool]:
    """Chronological replay, first come first served: an order is placed on its first pending session only when
    fewer than ``max_standing`` orders are pending (``entry.standing_orders_max``), fewer than ``max_lines`` orders
    and positions hold risk (open-risk cap ÷ risk per trade: CLAUDE.md rule 7, reserved risk included) and its value
    has neither a pending order nor a position. Only what is known on that session decides. Returns the accepted
    flag of each order (input order)."""
    idx = sorted(range(len(orders)), key=lambda k: (orders[k].first_day, orders[k].tiebreak))
    accepted = [False] * len(orders)
    pending: list[int] = []  # end days of the placed orders still pending
    risk: list[int] = []  # last day each placed order or position holds risk
    busy: dict[int, int] = {}  # value → last day it holds an order or a position
    for k in idx:
        o = orders[k]
        d = o.first_day
        pending = [e for e in pending if e >= d]
        risk = [e for e in risk if e >= d]
        if len(pending) >= max_standing or len(risk) >= max_lines or busy.get(o.value, -1) >= d:
            continue
        last = o.end_day if o.fill_day is None else max(o.end_day, o.exit_day if o.exit_day is not None else NEVER)
        pending.append(o.end_day)
        risk.append(last)
        busy[o.value] = last
        accepted[k] = True
    return accepted


def pace(
    orders: Sequence[OrderSpan], max_standing: int, max_lines: int, months: float, min_n: int = 20
) -> dict[str, object]:
    """Book-level pace: orders placed, fills and trades per month with the standing-order and open-risk caps."""
    acc = standing_orders_replay(orders, max_standing, max_lines)
    taken = [o for o, a in zip(orders, acc, strict=True) if a]
    fills = [o for o in taken if o.fill_day is not None]
    nets = [o.net_r for o in fills if o.net_r is not None]
    m = months if months > 0 else None
    ok = len(nets) >= min_n
    return {
        "emplacements": max_standing,
        "lignes": max_lines,
        "ordres_possibles": len(orders),
        "ordres_places": len(taken),
        "ordres_par_mois": _r(len(taken) / m, 2) if m else None,
        "executes_par_mois": _r(len(fills) / m, 2) if m else None,
        "trades": len(nets),
        "trades_par_mois": _r(len(nets) / m, 2) if m else None,
        "r_net_moyen": _r(statistics.fmean(nets)) if ok else None,
        "r_net_par_mois": _r(sum(nets) / m, 3) if m and ok else None,
    }
