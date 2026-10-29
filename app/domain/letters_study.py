"""docs/20 §20.11 — historical study of the letters' recommendations (user request of 29/10/2026). Pure.

Exploratory question: do the values recommended by the paid letters rise after the reception, after full costs,
and which entry / stop rule captures it? Every measure is taken **without look-ahead**:

- a letter is placed on its **reaction session** (the session itself when the letter arrives before the venue's
  open, else the next session of the venue); the « close » base is the first close after the reception;
- levels, volatility (ATR) and the tool's structure stop come from the last close before the reaction open;
- a session where the order of the prices is unknown is read the prudent way: stop and target in one session →
  stop first; on a session filled during the session, only the stop can be reached and its high is not counted
  in the favourable excursion (docs/31 §31.3).

Controls: values drawn at random (seeded) on the same day and venue with a neighbouring volatility, with the same
entries and exits, the letter's levels transposed in ATR from the reference close. Nothing here changes a rule:
adoption only through a pre-registered plan (docs/31). No price, name or ISIN leaves this module's aggregates.
"""

from __future__ import annotations

import bisect
import math
import random
import statistics
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any

from app.domain.detectors import DetectorContext, _stop
from app.domain.indicators import Bar, atr
from app.domain.letter_levels import canonical_levels, recalculable
from app.domain.moves import wilson
from app.domain.zones import DetectedZone


@dataclass(frozen=True)
class StudyParams:
    """Conventions of the study (exploratory: written in each report, not in params.yaml — as docs/34's study)."""

    horizons: tuple[int, ...] = (1, 3, 5, 10, 20)
    controls: int = 20  # control values per recommendation
    entry_valid_sessions: int = 3  # E2 / E3 orders
    buy_stop_cap_atr: float = 0.5  # E2: maximum price = trigger + 0.5 ATR
    atr_stop: float = 2.0  # X3
    atr_target: float = 3.0  # X3
    max_hold: int = 20  # time exit (sessions, the fill session included)
    excursion_sessions: int = 20  # MFE / MAE window from the reaction open
    pre_move_up: float = 0.03  # « already up more than 3 % before the reception »
    pre_move_sessions: int = 5
    novelty_sessions: int = 20  # same source, same value within 20 sessions → « répétée »
    tool_window: int = 3  # « letters vs tool »: a detector setup within ±3 sessions
    structure_lookback: int = 10  # X2: lowest low of the last 10 sessions (as d7 / d8)
    level_band: tuple[float, float] = (0.5, 2.0)  # a level outside [0.5 ; 2] × reference close is a misreading
    vol_band: float = 1.5  # controls: ATR % within [1/1.5 ; 1.5] × the recommended value's
    liquidity_share: float = 0.25  # controls: traded value ≥ min(25 % of the value's, the venue's median)
    min_control_trades: int = 3  # paired excess: at least 3 control trades for a recommendation
    min_recommendations: int = 50  # thresholds to report (not to decide)
    min_trades: int = 20
    mask_below: int = 5  # a statistic on fewer observations is masked (one value's path would show)
    seed: int = 20261029


BASES = ("ouverture", "cloture")
ENTRIES = ("E1", "E2", "E3")
EXITS = ("X1", "X2", "X3")
ARMS = tuple(f"{e}_{x}" for e in ENTRIES for x in EXITS)
ENTRY_FR = {
    "E1": "ouverture de la séance de réaction",
    "E2": "achat sur franchissement du déclencheur (ou de la résistance), prix maximal +0,5 ATR, 3 séances",
    "E3": "limite au niveau d'achat (ou au support) de la lettre, 3 séances",
}
EXIT_FR = {
    "X1": "stop et premier objectif de la lettre",
    "X2": "stop de structure de l'outil (R1) et premier objectif de la lettre",
    "X3": "stop à 2 ATR, objectif à 3 ATR",
}
EXECUTED = "execute"
NOT_APPLICABLE = "non_applicable"
NOT_FILLED = "non_execute"
INVALIDATED = "invalide"
INCOHERENT = "niveaux_incoherents"
UNKNOWN = "issue_inconnue"
CORPORATE_ACTION = "operation_sur_titres"
STATUS_FR = {
    EXECUTED: "exécuté",
    NOT_APPLICABLE: "niveau absent de la lettre",
    NOT_FILLED: "ordre jamais exécuté",
    INVALIDATED: "ouverture sous le stop avant l'exécution",
    INCOHERENT: "niveaux incohérents au prix d'entrée (stop au-dessus, objectif déjà dépassé)",
    UNKNOWN: "historique terminé avant l'issue",
    CORPORATE_ACTION: "opération sur titres pendant la position",
}


# --- placement ----------------------------------------------------------------------------------------------


def reaction_sessions(
    received_local: datetime, open_time: time, close_time: time, sessions: Sequence[date]
) -> tuple[int, int] | None:
    """Indices in ``sessions`` (the venue's sessions, sorted) of (reaction session, first close after the
    reception). ``received_local`` is the reception time in the venue's time zone; ``close_time`` the close of
    that day (half day included). Before the open of a session: that session for both. During the session: the
    next session's open, and that day's close. After the close, or on a day without session: the next session for
    both. None when the history ends first."""
    d, t = received_local.date(), received_local.time()
    i = bisect.bisect_left(sessions, d)
    n = len(sessions)
    if i < n and sessions[i] == d:
        if t < open_time:
            r, c = i, i
        elif t < close_time:
            r, c = i + 1, i
        else:
            r, c = i + 1, i + 1
    else:
        r, c = i, i
    if r >= n:
        return None
    return r, c


# --- forward measures (indices into one value's own sessions) ------------------------------------------------


def forward_from_open(bars: Sequence[Bar], r: int, h: int) -> float | None:
    """Return held ``h`` sessions from the open of session ``r``: close of session r + h − 1 ÷ open of r − 1."""
    j = r + h - 1
    if r < 0 or h < 1 or j >= len(bars) or bars[r].open <= 0:
        return None
    return bars[j].close / bars[r].open - 1


def forward_from_close(bars: Sequence[Bar], c: int, h: int) -> float | None:
    """Return over ``h`` sessions from the close of session ``c``."""
    j = c + h
    if c < 0 or h < 1 or j >= len(bars) or bars[c].close <= 0:
        return None
    return bars[j].close / bars[c].close - 1


def excursions_atr(bars: Sequence[Bar], r: int, sessions: int, atr_value: float | None) -> tuple[float, float] | None:
    """(MFE, MAE) in ATR over ``sessions`` sessions from the open of ``r`` (MAE ≤ 0)."""
    if atr_value is None or atr_value <= 0 or r < 0 or r + sessions > len(bars):
        return None
    seg = bars[r : r + sessions]
    o = bars[r].open
    return (max(b.high for b in seg) - o) / atr_value, (min(b.low for b in seg) - o) / atr_value


def pre_move(bars: Sequence[Bar], c: int, intraday: bool, sessions: int) -> float | None:
    """Change before the reception: last price known at the reception (the previous close, or the open of the
    session in progress when the letter arrives during it) ÷ the close ``sessions`` sessions earlier − 1."""
    b = c - 1
    if b - sessions < 0 or c >= len(bars):
        return None
    px = bars[c].open if intraday else bars[b].close
    base = bars[b - sessions].close
    return px / base - 1 if base > 0 else None


def atr_at(bars: Sequence[Bar], k: int, n: int = 14, window: int = 280) -> float | None:
    """Wilder ATR at the close of session ``k`` on the window the detectors see (280 sessions)."""
    if k < 0 or k >= len(bars):
        return None
    return atr(list(bars[max(0, k - window + 1) : k + 1]), n)


# --- letter levels ------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class LetterLevels:
    trigger: float | None = None  # declencheur, else resistance (E2)
    buy: float | None = None  # achat, else support (E3)
    stop: float | None = None  # X1
    target: float | None = None  # first target (X1, X2)
    kind: str = "aucun"  # recalculables | partiels | aucun


def _num(v: object) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int | float):
        x = float(v)
    elif isinstance(v, str):
        try:
            x = float(v.replace(" ", "").replace(" ", "").replace(",", "."))
        except ValueError:
            return None
    else:
        return None
    return x if math.isfinite(x) and x > 0 else None


def letter_levels(
    raw: Mapping[str, object] | None, ref: float, band: tuple[float, float] = (0.5, 2.0)
) -> tuple[LetterLevels, list[str]]:
    """The stored levels under the closed list of names (``canonical_levels``), without the ones that cannot be a
    price of this value (outside ``band`` × the reference close: another value's level, an index, a typo). Returns
    (levels, names dropped)."""
    nums: dict[str, float] = {}
    dropped: list[str] = []
    for k, v in (raw or {}).items():
        x = _num(v)
        if x is None:
            dropped.append(str(k))
        else:
            nums[str(k)] = x
    lv, unknown = canonical_levels(nums)
    dropped += unknown
    kept = {k: x for k, x in lv.items() if ref > 0 and band[0] * ref <= x <= band[1] * ref}
    dropped += [k for k in lv if k not in kept]
    targets = [kept[k] for k in ("objectif", "objectif_2", "objectif_3") if k in kept]
    target = kept["objectif"] if "objectif" in kept else (min(targets) if targets else None)
    kind = "recalculables" if recalculable(kept) else "partiels" if kept else "aucun"
    trigger = kept["declencheur"] if "declencheur" in kept else kept.get("resistance")
    buy = kept["achat"] if "achat" in kept else kept.get("support")
    return LetterLevels(trigger, buy, kept.get("stop"), target, kind), dropped


def transpose(level: float | None, ref: float, atr_value: float, ref_c: float, atr_c: float) -> float | None:
    """A level of the recommended value carried to a control: same distance from the reference close in ATR."""
    if level is None or atr_value <= 0 or atr_c <= 0 or ref <= 0 or ref_c <= 0:
        return None
    out = ref_c + (level - ref) / atr_value * atr_c
    return out if out > 0 else None


def structure_stop(
    window: Sequence[Bar],
    entry: float,
    atr_value: float | None,
    zones: list[DetectedZone],
    wick_buffer_pct: float,
    buffer_atr_mult: float,
    lookback: int = 10,
) -> float | None:
    """The tool's stop R1 (``detectors._stop``): under the nearest support zone between the structure and the
    planned entry, else the structure (lowest low of ``lookback`` sessions, or the entry if lower) less the ATR
    buffer. ``window`` ends at the reference close (known before the order)."""
    if not window or atr_value is None or atr_value <= 0 or entry <= 0:
        return None
    low = min(min(b.low for b in window[-lookback:]), entry)
    stop, _, _ = _stop(entry, low, atr_value, DetectorContext(None, zones, wick_buffer_pct, buffer_atr_mult), math.inf)
    return stop if 0 < stop < entry else None


# --- orders and exits on daily bars ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EntryRule:
    kind: str  # open | buy_stop | limit
    level: float | None = None  # trigger (buy_stop) or limit price
    cap: float | None = None  # maximum price of a buy stop (a stop-limit: never above it)
    valid: int = 1  # sessions, the reaction session included


@dataclass(frozen=True)
class ExitRule:
    stop: float | None = None  # absolute level (it also cancels an order when a session opens at or below it)
    target: float | None = None
    stop_atr: float | None = None  # or: fill − k · ATR
    target_atr: float | None = None
    atr: float | None = None
    max_hold: int = 20


@dataclass(frozen=True)
class Fill:
    index: int
    price: float
    at_open: bool  # False: filled during the session (the order of that session's prices is unknown)


@dataclass(frozen=True)
class Trade:
    entry_index: int
    exit_index: int
    entry: float
    exit: float
    reason: str  # stop | gap_stop | target | time
    risk_fraction: float  # (entry − slipped stop) ÷ entry
    mfe_r: float
    mae_r: float

    @property
    def gross_return(self) -> float:
        return self.exit / self.entry - 1

    @property
    def gross_r(self) -> float:
        return self.gross_return / self.risk_fraction

    def net_r(self, cost_fraction: float) -> float:
        """R after the round-trip costs (fraction of the notional), as ``backtest.TradeResult.net_r``."""
        return (self.gross_return - cost_fraction) / self.risk_fraction


def fill(bars: Sequence[Bar], rule: EntryRule, invalid_at: float | None, slippage: float) -> Fill | str:
    """Execution of the entry order on ``bars`` (bars[0] = the reaction session). A limit fills at the open when
    the open is at or under it, else at the limit when the session trades through it (low strictly below). A buy
    stop triggers at the open (open ≥ trigger: filled at the open up to its cap, else a limit at the cap) or during
    the session (high ≥ trigger: filled at trigger × (1 + slippage), never above the cap nor the high). A session
    opening at or below ``invalid_at`` before any fill cancels the order."""
    if rule.kind == "open":
        if not bars:
            return UNKNOWN
        b0 = bars[0]
        if invalid_at is not None and b0.open <= invalid_at:
            return INVALIDATED
        return Fill(0, b0.open, True)
    if rule.level is None or rule.level <= 0:
        return INCOHERENT
    triggered = rule.kind == "limit"
    limit = rule.level if rule.kind == "limit" else (rule.cap if rule.cap is not None else math.inf)
    for i, b in enumerate(bars[: rule.valid]):
        if invalid_at is not None and b.open <= invalid_at:
            return INVALIDATED
        if not triggered:
            if b.open >= rule.level:
                triggered = True
            elif b.high >= rule.level:
                return Fill(i, min(limit, rule.level * (1 + slippage), b.high), False)
            else:
                continue
        if b.open <= limit:
            return Fill(i, b.open, True)
        if b.low < limit:
            return Fill(i, limit, False)
    return UNKNOWN if len(bars) < rule.valid else NOT_FILLED


def simulate(bars: Sequence[Bar], entry: EntryRule, exit_rule: ExitRule, slippage: float) -> Trade | str:
    """One trade (bars[0] = the reaction session): the entry order, then a slipped stop, the target and the time
    exit. Stop and target in one session → stop first; a gap through the stop exits at the open, slipped; a session
    opening above the target exits at the open; on a fill during the session only the stop can be reached and that
    session's high is not counted. Returns the trade, or why there is none (a ``STATUS_FR`` key)."""
    f = fill(bars, entry, exit_rule.stop, slippage)
    if isinstance(f, str):
        return f
    e = f.price
    stop = exit_rule.stop
    if stop is None and exit_rule.stop_atr is not None and exit_rule.atr:
        stop = e - exit_rule.stop_atr * exit_rule.atr
    target = exit_rule.target
    if target is None and exit_rule.target_atr is not None and exit_rule.atr:
        target = e + exit_rule.target_atr * exit_rule.atr
    if stop is None or target is None or not (0 < stop < e < target):
        return INCOHERENT
    stop_exec = stop * (1 - slippage)
    risk = (e - stop_exec) / e
    hi = lo = e
    n = len(bars)
    for j in range(f.index, min(n, f.index + exit_rule.max_hold)):
        b = bars[j]
        if j > f.index and b.open <= stop:
            px = b.open * (1 - slippage)
            return _trade(f.index, j, e, px, "gap_stop", risk, hi, min(lo, b.open))
        if j == f.index and not f.at_open:
            lo = min(lo, b.low)
            if b.low <= stop:
                return _trade(f.index, j, e, stop_exec, "stop", risk, hi, lo)
            continue
        hi, lo = max(hi, b.high), min(lo, b.low)
        if b.low <= stop:
            return _trade(f.index, j, e, stop_exec, "stop", risk, hi, lo)
        if j > f.index and b.open >= target:
            return _trade(f.index, j, e, b.open, "target", risk, hi, lo)
        if b.high >= target:
            return _trade(f.index, j, e, target, "target", risk, hi, lo)
    if f.index + exit_rule.max_hold > n:
        return UNKNOWN
    last = f.index + exit_rule.max_hold - 1
    return _trade(f.index, last, e, bars[last].close, "time", risk, hi, lo)


def _trade(i: int, j: int, entry: float, px: float, reason: str, risk: float, hi: float, lo: float) -> Trade:
    return Trade(i, j, entry, px, reason, risk, (hi / entry - 1) / risk, (lo / entry - 1) / risk)


# --- controls ----------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Candidate:
    key: str
    vol: float  # ATR % at the reference session
    liquidity: float  # mean traded value of the previous 20 sessions (quote currency)


def pick_controls(
    cands: Sequence[Candidate],
    vol: float,
    liquidity: float | None,
    n: int,
    rng: random.Random,
    vol_band: float = 1.5,
    liquidity_share: float = 0.25,
) -> tuple[list[str], bool]:
    """``n`` controls among the same-day, same-venue candidates: liquid enough (at least ``liquidity_share`` of the
    recommended value's traded value, or the venue's median when lower), then drawn at random among those whose
    ATR % is within [1/``vol_band`` ; ``vol_band``] × the value's; when fewer, the nearest in volatility (flag
    ``widened``). Deterministic for a given ``rng`` state (candidates sorted by key before the draw)."""
    pool = sorted((c for c in cands if c.vol > 0 and c.liquidity > 0), key=lambda c: c.key)
    if not pool or vol <= 0 or n <= 0:
        return [], False
    if liquidity is not None and liquidity > 0:
        floor = min(liquidity_share * liquidity, statistics.median(c.liquidity for c in pool))
        pool = [c for c in pool if c.liquidity >= floor]
    near = [c for c in pool if 1 / vol_band <= c.vol / vol <= vol_band]
    if len(near) >= n:
        return [c.key for c in rng.sample(near, n)], False
    ranked = sorted(pool, key=lambda c: (abs(math.log(c.vol / vol)), c.key))
    return [c.key for c in ranked[:n]], True


# --- one recommendation, measured -------------------------------------------------------------------------------


@dataclass
class ArmOutcome:
    status: str  # EXECUTED or why not (STATUS_FR)
    net_cto: float | None = None
    net_pea: float | None = None
    controls_cto: list[float] = field(default_factory=list)  # net R (CTO) of the controls' executed trades
    controls_tried: int = 0


@dataclass
class RecRow:
    isin: str  # kept for tracing, never reported
    day: date  # reaction session, never reported
    source: str
    direction: str  # buy | watch
    origin: str  # extraction | revue
    levels: str  # recalculables | partiels | aucun
    group: str  # P0 | P1 | P2
    year: int
    pre_up: bool | None
    novelty: str  # nouvelle | repetee
    fwd: dict[str, dict[int, float | None]]  # base → horizon → gross return
    bench: dict[str, dict[int, float | None]]  # base → horizon → benchmark return (venue index or equal-weighted)
    ctrl_fwd: dict[str, dict[int, list[float]]]  # base → horizon → controls' gross returns
    cost: float | None  # round-trip cost fraction (CTO) of a forward position
    mfe: float | None = None
    mae: float | None = None
    ctrl_mfe: list[float] = field(default_factory=list)
    ctrl_mae: list[float] = field(default_factory=list)
    arms: dict[str, ArmOutcome] = field(default_factory=dict)
    tool: list[str] = field(default_factory=list)  # detectors with a setup within ±tool_window sessions
    tool_net: float | None = None  # net R (CTO) of the tool's own first trade


# --- statistics -----------------------------------------------------------------------------------------------------


def mean_stats(xs: Sequence[float], mask_below: int = 5) -> dict[str, Any]:
    """n, mean, median, normal 95 % interval and t of the mean; masked below ``mask_below`` observations."""
    n = len(xs)
    if n == 0:
        return {"n": 0}
    if n < mask_below:
        return {"n": n, "masque": f"moins de {mask_below} observations"}
    m = statistics.fmean(xs)
    out: dict[str, Any] = {"n": n, "moyenne": round(m, 4), "mediane": round(statistics.median(xs), 4)}
    if n > 1:
        se = statistics.stdev(xs) / math.sqrt(n)
        out["ic95"] = [round(m - 1.96 * se, 4), round(m + 1.96 * se, 4)]
        out["t"] = round(m / se, 2) if se > 0 else None
    return out


def share_stats(k: int, n: int, mask_below: int = 5) -> dict[str, Any]:
    """Proportion with its Wilson 95 % interval; masked below ``mask_below`` observations."""
    if n == 0:
        return {"n": 0}
    if n < mask_below:
        return {"n": n, "masque": f"moins de {mask_below} observations"}
    lo, hi = wilson(k, n)
    return {"n": n, "k": k, "part": round(k / n, 4), "ic95": [round(lo, 4), round(hi, 4)]}


def _fwd_stats(rows: Sequence[RecRow], base: str, h: int, p: StudyParams) -> dict[str, Any]:
    gross: list[float] = []
    net: list[float] = []
    abn: list[float] = []
    abn_net: list[float] = []
    exc: list[float] = []
    ctrl: list[float] = []
    for r in rows:
        cs = r.ctrl_fwd.get(base, {}).get(h, [])
        ctrl += cs
        g = r.fwd.get(base, {}).get(h)
        if g is None:
            continue
        gross.append(g)
        if r.cost is not None:
            net.append(g - r.cost)
        b = r.bench.get(base, {}).get(h)
        if b is not None:
            abn.append(g - b)
            if r.cost is not None:
                abn_net.append(g - b - r.cost)
        if cs:
            exc.append(g - statistics.fmean(cs))
    mb = p.mask_below
    return {
        "brut": mean_stats(gross, mb),
        "net_cto": mean_stats(net, mb),
        "part_positive_nette": share_stats(sum(1 for x in net if x > 0), len(net), mb),
        "anormal": mean_stats(abn, mb),
        "anormal_net": mean_stats(abn_net, mb),
        "exces_controles": mean_stats(exc, mb),
        "controles_brut": mean_stats(ctrl, mb),
    }


def _arm_stats(rows: Sequence[RecRow], arm: str, p: StudyParams) -> dict[str, Any]:
    outs = [r.arms[arm] for r in rows if arm in r.arms]
    applicable = [o for o in outs if o.status != NOT_APPLICABLE]
    done = [o for o in applicable if o.status == EXECUTED and o.net_cto is not None]
    nets = [o.net_cto for o in done if o.net_cto is not None]
    pea = [o.net_pea for o in done if o.net_pea is not None]
    ctrl = [x for o in applicable for x in o.controls_cto]
    paired = [
        o.net_cto - statistics.fmean(o.controls_cto)
        for o in done
        if o.net_cto is not None and len(o.controls_cto) >= p.min_control_trades
    ]
    mb = p.mask_below
    return {
        "applicables": len(applicable),
        "executes": len(done),
        "taux_execution": share_stats(len(done), len(applicable), mb),
        "r_net_cto": mean_stats(nets, mb),
        "gagnants": share_stats(sum(1 for x in nets if x > 0), len(nets), mb),
        "r_net_pea": mean_stats(pea, mb),
        "issues": dict(sorted(Counter(o.status for o in applicable).items())),
        "controles": {
            "essais": sum(o.controls_tried for o in applicable),
            "executes": len(ctrl),
            "r_net_cto": mean_stats(ctrl, mb),
        },
        "exces_apparie": mean_stats(paired, mb),
        "seuils_atteints": len(done) >= p.min_trades and len(rows) >= p.min_recommendations,
    }


def summarize(rows: Sequence[RecRow], p: StudyParams) -> dict[str, Any]:
    """Aggregates of a set of recommendations: forward returns (gross, net, abnormal, excess over the controls),
    excursions in ATR, and each entry × exit arm (net R CTO and PEA, controls, paired excess)."""
    mb = p.mask_below
    return {
        "n": len(rows),
        "seuil_recommandations_atteint": len(rows) >= p.min_recommendations,
        "rendements": {base: {str(h): _fwd_stats(rows, base, h, p) for h in p.horizons} for base in BASES},
        "excursions_atr": {
            "mfe": mean_stats([r.mfe for r in rows if r.mfe is not None], mb),
            "mae": mean_stats([r.mae for r in rows if r.mae is not None], mb),
            "mfe_controles": mean_stats([x for r in rows for x in r.ctrl_mfe], mb),
            "mae_controles": mean_stats([x for r in rows for x in r.ctrl_mae], mb),
        },
        "bras": {arm: _arm_stats(rows, arm, p) for arm in ARMS},
    }


DIRECTION_FR = {"buy": "achat", "watch": "à surveiller"}


def _pre_label(r: RecRow) -> str:
    return "inconnu" if r.pre_up is None else "oui" if r.pre_up else "non"


BREAKDOWNS: dict[str, Callable[[RecRow], str]] = {
    "source": lambda r: r.source,
    "sens": lambda r: DIRECTION_FR.get(r.direction, r.direction),
    "niveaux": lambda r: r.levels,
    "groupe_de_places": lambda r: r.group,
    "annee": lambda r: str(r.year),
    "deja_en_hausse_avant_reception": _pre_label,
    "nouveaute": lambda r: r.novelty,
    "origine": lambda r: r.origin,
}


def breakdowns(rows: Sequence[RecRow], p: StudyParams) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for name, key in BREAKDOWNS.items():
        groups: dict[str, list[RecRow]] = {}
        for r in rows:
            groups.setdefault(key(r), []).append(r)
        out[name] = {k: summarize(v, p) for k, v in sorted(groups.items())}
    return out


def letters_vs_tool(rows: Sequence[RecRow], p: StudyParams) -> dict[str, Any]:
    """Recommendations where the tool also had a detector setup within ±``tool_window`` sessions, against the
    others; the tool's own trade on the same values and the paired gap letter − tool."""
    with_ = [r for r in rows if r.tool]
    without = [r for r in rows if not r.tool]
    tool_nets = [r.tool_net for r in with_ if r.tool_net is not None]
    gaps: dict[str, list[float]] = {}
    for arm in ("E1_X1", "E1_X2", "E1_X3"):
        gaps[arm] = []
        for r in with_:
            o = r.arms.get(arm)
            if r.tool_net is not None and o is not None and o.status == EXECUTED and o.net_cto is not None:
                gaps[arm].append(o.net_cto - r.tool_net)
    mb = p.mask_below
    return {
        "fenetre_seances": p.tool_window,
        "avec_configuration": summarize(with_, p),
        "sans_configuration": summarize(without, p),
        "par_detecteur": dict(sorted(Counter(d for r in with_ for d in r.tool).items())),
        "trade_de_l_outil": {
            "r_net_cto": mean_stats(tool_nets, mb),
            "gagnants": share_stats(sum(1 for x in tool_nets if x > 0), len(tool_nets), mb),
        },
        "ecart_lettre_moins_outil": {arm: mean_stats(v, mb) for arm, v in gaps.items()},
    }


def novelty(previous: Sequence[int], current: int, sessions: int) -> str:
    """« repetee » when the same source recommended the same value within ``sessions`` venue sessions before."""
    return "repetee" if any(0 < current - k <= sessions for k in previous) else "nouvelle"
