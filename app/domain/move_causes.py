"""docs/36 — causes of the rises, step 1 (user request of 30/10/2026). Pure, exploratory.

Question: for a sample of rises of +5 % and more (the docs/34 episodes), which **computable** cause preceded or
accompanied each one — and is that cause worth anything? Two safeguards, never one without the other:

- **three groups under the same rules**: the rises, the symmetric falls of the same zigzag, and random sessions
  (controls) stratified like the rises. A cause that precedes rises as often as falls, or as often as an ordinary
  session, explains nothing;
- **timing**: each cause is dated relative to t0, the first session of the move (the session after the low or the
  high; for a control, the drawn session): « connue avant » (known at or before the close of t0 − 1), « avant
  l'ouverture de t0 » (known in the night before t0), « pendant » (from the open of t0 to the close of t0 + 1). Only
  what was known can be bought, and only from the **next open after it was known** (an opening gap is not buyable:
  the event study measures from the open after).

Sessions are the value's own stored sessions (as docs/34); a time is coded on a half-session scale: ``night(k)``
(between the close of k − 1 and the open of k) < ``during(k)`` (session k, its close included). Nothing here reads a
database or the network; nothing here changes a rule (docs/31: adoption only through a pre-registered plan).
"""

from __future__ import annotations

import bisect
import math
import statistics
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any

import numpy as np
import numpy.typing as npt

from app.domain.indicators import Bar
from app.domain.letters_study import forward_from_open, mean_stats, reaction_sessions, share_stats
from app.domain.moves import (
    MoveParams,
    atr_series,
    barrier_label,
    daily_sigma,
    find_episodes,
    size_bucket,
    zigzag,
)

F64 = npt.NDArray[np.float64]
Mask = npt.NDArray[np.bool_]

# --- groups, causes, timings (closed lists) -------------------------------------------------------------------------

RISES, FALLS, CONTROLS = "hausses", "baisses", "controles"
GROUPS = (RISES, FALLS, CONTROLS)
GROUP_FR = {RISES: "hausses", FALLS: "baisses", CONTROLS: "contrôles"}

CORPORATE = "operation_sur_titres"
EARNINGS = "publication_resultats"
INSIDER = "achat_dirigeant"
LETTER = "recommandation_lettre"
NEWS = "nouvelle_stockee"
MARKET = "mouvement_marche"
SECTOR = "mouvement_secteur"
GAP = "saut_ouverture"
RVOL = "volume_anormal_avant"
SETUP = "configuration_technique"
NO_CAUSE = "aucune_cause"
# the fixed priority of the primary cause: information first (what happened to the company), then the systematic
# moves (market, then sector: a sector move that the market explains is the market's), then the price and volume
# observables, which describe a move more than they cause it
CAUSES = (CORPORATE, EARNINGS, INSIDER, LETTER, NEWS, MARKET, SECTOR, GAP, RVOL, SETUP)
PRIMARY = (*CAUSES, NO_CAUSE)
INFORMATION = (CORPORATE, EARNINGS, INSIDER, LETTER, NEWS)
NO_CONTROL = frozenset({MARKET, SECTOR})  # explanations of a move: a control has no move to explain → sans objet
CAUSE_FR = {
    CORPORATE: "opération sur titres ou détachement de dividende",
    EARNINGS: "publication de résultats",
    INSIDER: "achat de dirigeant (AMF)",
    LETTER: "recommandation d'une lettre",
    NEWS: "nouvelle stockée",
    MARKET: "mouvement de marché",
    SECTOR: "mouvement de secteur",
    GAP: "saut d'ouverture (non capturable à l'ouverture)",
    RVOL: "volume anormal avant le mouvement",
    SETUP: "configuration technique déjà présente",
    NO_CAUSE: "aucune cause trouvée",
}

KNOWN_BEFORE, BEFORE_OPEN, DURING = "connue_avant", "avant_ouverture", "pendant"
TIMINGS = (KNOWN_BEFORE, BEFORE_OPEN, DURING)
TIMING_FR = {
    KNOWN_BEFORE: "connue avant (au plus tard à la clôture de t0 − 1)",
    BEFORE_OPEN: "avant l'ouverture de t0 (nuit précédente)",
    DURING: "pendant (de l'ouverture de t0 à la clôture de t0 + 1)",
}

# event study (part B): causes taken as events, every occurrence in the perimeter
EV_EARNINGS = EARNINGS
EV_INSIDER = INSIDER
EV_LETTER = LETTER
EV_GAP_UP = "saut_ouverture_haussier"
EV_GAP_DOWN = "saut_ouverture_baissier"
EV_RVOL = "volume_anormal"
SETUP_PREFIX = "configuration_"
EVENT_FR = {
    EV_EARNINGS: "publication de résultats (toutes)",
    EV_INSIDER: "achat de dirigeant (AMF, toute fonction, ≥ 10 000 €)",
    EV_LETTER: "recommandation d'une lettre (achat ou à surveiller)",
    EV_GAP_UP: "saut d'ouverture haussier",
    EV_GAP_DOWN: "saut d'ouverture baissier",
    EV_RVOL: "volume anormal (RVOL ≥ seuil) à la clôture",
}


@dataclass(frozen=True)
class CauseParams:
    """Conventions of the study (exploratory: written in each report, not in params.yaml — as docs/34's study)."""

    window_before: int = 5  # window of the causes: [t0 − 5, t0 + 1]
    window_after: int = 1
    first_sessions: int = 3  # « mouvement de marché / secteur »: the move's first sessions
    explain_share: float = 0.5  # … whose equal-weighted return explains ≥ 50 % of the value's
    gap_min: float = 0.03  # opening gap ≥ max(3 % ; 1.5 ATR14 / close of t0 − 1)
    gap_atr: float = 1.5
    rvol_min: float = 2.0  # RVOL (production definition: volume ÷ mean of the 20 previous) ≥ 2 …
    rvol_sessions: int = 3  # … on one of the sessions t0 − 3 … t0 − 1
    setup_sessions: int = 3  # a production detector setup at one of the closes t0 − 3 … t0 − 1
    min_peers: int = 5  # an equal-weighted benchmark needs at least 5 values
    dividend_min: float = 0.002  # change of the adjustment factor (adj_close ÷ close) read as a dividend detachment
    ca_jump: float = 0.2  # … and above 20 %: a corporate action, prices not comparable (skipped, as docs/34)
    comparability_before: int = 22  # no corporate action from t0 − 22 (ATR, σ, RVOL inputs) to the end of the move
    suspect_ratio: float = 4.0  # a session at ≥ 4 × or ≤ ¼ of the previous close: suspect prices, skipped like a CA
    max_gap_days: int = 7  # an event placed more than 7 calendar days before its session is not placed
    earnings_margin_days: int = 100  # earnings history « known » within 100 days of a loaded one (docs/34)
    refractory: int = 10  # event study: one occurrence per value and cause every 10 sessions (the first one)
    horizons: tuple[int, ...] = (5, 10, 20)
    mask_below: int = 20  # any statistic on fewer observations is masked (docs/34 §34.10)
    events_max: int = 20_000  # occurrences measured per cause (seeded sample beyond)
    t_discovery: float = 3.0  # Harvey, Liu & Zhu (2016)
    cap_small_eur: float = 1e9
    cap_large_eur: float = 1e10


# --- moves ----------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Move:
    """A rise (low → high of the zigzag) or a fall (high → low). ``start`` is the pivot close, ``end`` the opposite
    pivot; ``magnitude`` > 0 (a fall of 6 % is 0.06)."""

    direction: str  # RISES | FALLS
    start: int
    end: int
    magnitude: float
    sigma: float
    max_session_share: float
    confirmed: bool
    jump_share: float = 0.5

    @property
    def t0(self) -> int:
        return self.start + 1

    @property
    def sessions(self) -> int:
        return self.end - self.start

    @property
    def shape(self) -> str:
        return "saut" if self.max_session_share >= self.jump_share else "progressive"

    @property
    def sign(self) -> int:
        return 1 if self.direction == RISES else -1


def reversal_thresholds(bars: Sequence[Bar], p: MoveParams) -> list[float]:
    """The zigzag thresholds of ``find_episodes`` (max(reversal_min ; reversal_atr · ATR14 / close))."""
    closes = [b.close for b in bars]
    return [
        max(p.reversal_min, p.reversal_atr * (a / c)) if a is not None and c > 0 else p.reversal_min
        for a, c in zip(atr_series(bars), closes, strict=True)
    ]


def find_rises(bars: Sequence[Bar], p: MoveParams) -> list[Move]:
    """The docs/34 episodes (``moves.find_episodes``) as moves."""
    return [
        Move(RISES, e.start, e.peak, e.rise, e.sigma, e.max_session_share, e.confirmed, e.jump_share)
        for e in find_episodes(bars, p)
    ]


def find_falls(bars: Sequence[Bar], p: MoveParams) -> list[Move]:
    """The symmetric down episodes of the same zigzag: H → L swings whose decline 1 − close[L] / close[H] is
    ≥ ``min_rise`` and ≥ ``sigma_k`` · σ · √sessions (σ of the 20 sessions before the high)."""
    closes = [b.close for b in bars]
    piv = zigzag(closes, reversal_thresholds(bars, p))
    out: list[Move] = []
    for (i0, k0), (i1, k1) in zip(piv, piv[1:], strict=False):
        if k0 != "H" or not k1.startswith("L") or i1 <= i0 or closes[i0] <= 0 or closes[i1] <= 0:
            continue
        sigma = daily_sigma(closes, i0 + 1, p.sigma_sessions)
        if sigma is None:
            continue
        fall = 1 - closes[i1] / closes[i0]
        if fall < p.min_rise or fall < p.sigma_k * sigma * math.sqrt(i1 - i0):
            continue
        total = math.log(closes[i0] / closes[i1])
        worst = max(math.log(closes[j - 1] / closes[j]) for j in range(i0 + 1, i1 + 1))
        out.append(Move(FALLS, i0, i1, fall, sigma, worst / total if total > 0 else 1.0, not k1.endswith("?"),
                        p.jump_share))  # fmt: skip
    return out


# --- time codes -----------------------------------------------------------------------------------------------------


def night(k: int) -> int:
    """Between the close of session k − 1 and the open of session k (after the close, before the open, a day off)."""
    return 2 * k


def during(k: int) -> int:
    """During session k (from its open to its close)."""
    return 2 * k + 1


def entry_session(code: int) -> int:
    """The first open after a time: the open of k for ``night(k)``, of k + 1 for ``during(k)``."""
    return code // 2 if code % 2 == 0 else code // 2 + 1


def code_session(code: int) -> int:
    """The session a time belongs to or precedes (k for both ``night(k)`` and ``during(k)``)."""
    return code // 2


def place_time(
    local: datetime, open_time: time, close_time: time, sessions: Sequence[date], max_gap_days: int = 7
) -> int | None:
    """Code of a publication time (``local``: wall time of the value's venue) on the value's sessions: before the
    open → ``night(k)``; during the session → ``during(k)``; after the close or on a day without session →
    ``night`` of the next session. The rule of the letters (``letters_study.reaction_sessions``, and
    ``letter_known_day``: after the close → known the next day). None when the history ends first or the session
    found is more than ``max_gap_days`` calendar days later (not listed yet, suspended)."""
    rc = reaction_sessions(local, open_time, close_time, sessions)
    if rc is None:
        return None
    r, c = rc
    code = during(c) if r == c + 1 else night(r)
    if (sessions[code_session(code)] - local.date()).days > max_gap_days:
        return None
    return code


def earnings_code(day: date, when: str, sessions: Sequence[date], max_gap_days: int = 7) -> int | None:
    """Code of an earnings announcement: ``bmo`` → the night before the session of its day; ``intraday`` → during
    that session; ``amc`` **and unknown** → the night after it (the prudent reading: an announcement of unknown time
    is never assumed known before the close of its day, nor bought at that day's open)."""
    if when == "bmo":
        i = bisect.bisect_left(sessions, day)
        code = night(i)
    elif when == "intraday":
        i = bisect.bisect_left(sessions, day)
        code = during(i) if i < len(sessions) and sessions[i] == day else night(i)
    else:
        i = bisect.bisect_right(sessions, day)
        code = night(i)
    if i >= len(sessions) or (sessions[i] - day).days > max_gap_days:
        return None
    return code


def timing(code: int, t0: int, p: CauseParams) -> str | None:
    """Timing class of a time relative to t0, None outside the window [night(t0 − 5), close of t0 + 1]."""
    if code < night(t0 - p.window_before) or code > during(t0 + p.window_after):
        return None
    if code <= during(t0 - 1):
        return KNOWN_BEFORE
    if code == night(t0):
        return BEFORE_OPEN
    return DURING


# --- flags ----------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Flag:
    """A cause on one unit. ``present`` None: the source does not cover the unit (unknown — never « absent »).
    ``timing`` and ``entry`` (first open after it was known) describe its earliest occurrence in the window."""

    present: bool | None
    timing: str | None = None
    entry: int | None = None


UNKNOWN = Flag(None)
ABSENT = Flag(False)


def flag_from_codes(codes: Sequence[int], t0: int, p: CauseParams, covered: bool = True) -> Flag:
    """The earliest of the sorted time ``codes`` inside the window of t0."""
    if not covered:
        return UNKNOWN
    i = bisect.bisect_left(codes, night(t0 - p.window_before))
    if i < len(codes) and codes[i] <= during(t0 + p.window_after):
        c = codes[i]
        return Flag(True, timing(c, t0, p), entry_session(c))
    return ABSENT


def flag_from_sessions(sessions: Sequence[int], t0: int, p: CauseParams, known: bool = True) -> Flag:
    """A cause dated by the session where it takes effect at the open (an ex-date): before t0 → known before, at t0
    → before the open of t0 (announced beforehand), t0 + 1 → during. ``sessions`` sorted."""
    if not known:
        return UNKNOWN
    i = bisect.bisect_left(sessions, t0 - p.window_before)
    if i < len(sessions) and sessions[i] <= t0 + p.window_after:
        k = sessions[i]
        return Flag(True, KNOWN_BEFORE if k < t0 else BEFORE_OPEN if k == t0 else DURING, k)
    return ABSENT


def suspect_sessions(opens: Sequence[float], closes: Sequence[float], ratio: float) -> list[int]:
    """Own sessions whose open or close is ≥ ``ratio`` × or ≤ 1/``ratio`` of the previous close (or a price ≤ 0): a
    unit change or a bad tick the adjustment factor does not show. Skipped like a corporate action (first run of
    30/10/2026: one such series averaged d7's measured returns at +794 %)."""
    out: list[int] = []
    for k in range(1, len(closes)):
        prev = closes[k - 1]
        if prev <= 0 or closes[k] <= 0 or opens[k] <= 0:
            out.append(k)
            continue
        if any(x / prev >= ratio or x / prev <= 1 / ratio for x in (opens[k], closes[k])):
            out.append(k)
    return out


def opening_gap(open_t0: float, close_prev: float) -> float | None:
    return open_t0 / close_prev - 1 if close_prev > 0 and open_t0 > 0 else None


def gap_threshold(close_prev: float, atr_prev: float | None, p: CauseParams) -> float | None:
    """max(gap_min ; gap_atr · ATR14 / close), ATR at the close before the gap (known before the open)."""
    if atr_prev is None or close_prev <= 0:
        return None
    return max(p.gap_min, p.gap_atr * atr_prev / close_prev)


def gap_flag(gap: float | None, threshold: float | None, t0: int) -> tuple[Flag, int]:
    """« saut d'ouverture » at t0, in either direction (the same predicate for the three groups); the direction is
    returned apart. Known at the open of t0 (during t0): bought, at best, at the open of t0 + 1."""
    if gap is None or threshold is None:
        return UNKNOWN, 0
    if abs(gap) >= threshold:
        return Flag(True, DURING, t0 + 1), (1 if gap > 0 else -1)
    return ABSENT, 0


def rvol_series(volumes: Sequence[float], n: int = 20) -> list[float | None]:
    """RVOL of each session as production computes it (``indicators.rvol_day``): volume ÷ mean of the ``n``
    previous volumes."""
    out: list[float | None] = []
    acc = 0.0
    for k, v in enumerate(volumes):
        if k >= n:
            out.append(v / (acc / n) if acc > 0 else None)
            acc -= volumes[k - n]
        else:
            out.append(None)
        acc += v
    return out


def rvol_flag(rvols: Sequence[float | None], t0: int, p: CauseParams) -> Flag:
    """RVOL ≥ ``rvol_min`` at one of the closes t0 − ``rvol_sessions`` … t0 − 1: an observable before the move."""
    vals = [rvols[k] for k in range(max(0, t0 - p.rvol_sessions), t0) if k < len(rvols)]
    known = [v for v in vals if v is not None]
    if not known:
        return UNKNOWN
    return Flag(True, KNOWN_BEFORE, t0) if any(v >= p.rvol_min for v in known) else ABSENT


def setup_flag(detectors: Sequence[str], t0: int) -> Flag:
    return Flag(True, KNOWN_BEFORE, t0) if detectors else ABSENT


def explained_flag(move_ret: float | None, bench_ret: float | None, sign: int, end_session: int,
                   p: CauseParams) -> Flag:  # fmt: skip
    """« mouvement de marché / secteur »: the equal-weighted benchmark over the move's first sessions explains at
    least ``explain_share`` of the value's own return (same direction as the move). Known only at the close of
    those sessions (``end_session``): « pendant »."""
    if move_ret is None or bench_ret is None:
        return UNKNOWN
    if move_ret * sign <= 0:
        return ABSENT
    return Flag(True, DURING, end_session + 1) if bench_ret / move_ret >= p.explain_share else ABSENT


def ew_return(p0: F64, p1: F64, ok: Mask, liquidity: F64 | None = None, min_values: int = 5) -> float | None:
    """Equal-weighted return p1 / p0 − 1 over the columns ``ok`` with valid prices; with ``liquidity``, over the
    liquid half (traded value ≥ its median), as the venue benchmark of the letters study (``_bench``). None below
    ``min_values`` values."""
    sel = ok & np.isfinite(p0) & np.isfinite(p1) & (p0 > 0)
    if liquidity is not None:
        sel &= np.isfinite(liquidity)
    if int(sel.sum()) < min_values:
        return None
    if liquidity is not None:
        sel &= liquidity >= np.median(liquidity[sel])
    return float(np.mean(p1[sel] / p0[sel] - 1))


def primary_cause(flags: Mapping[str, Flag]) -> str:
    """The first present cause in the fixed priority (``CAUSES``), else « aucune cause trouvée »."""
    for c in CAUSES:
        f = flags.get(c)
        if f is not None and f.present:
            return c
    return NO_CAUSE


def without_information(flags: Mapping[str, Flag]) -> bool:
    """No information cause and no market / sector move present: the target of step 2 (news history)."""
    return not any((f := flags.get(c)) is not None and f.present for c in (*INFORMATION, MARKET, SECTOR))


def capturable_share(closes: Sequence[float], opens: Sequence[float], move: Move, entry: int | None) -> float | None:
    """Share of the move still ahead at the first open after the cause was known: (close[end] − open[entry]) ÷
    (close[end] − close[start]); 0 when it was known only after the end of the move."""
    if entry is None:
        return None
    total = closes[move.end] - closes[move.start]
    if total == 0 or entry >= len(opens):
        return None
    if entry > move.end:
        return 0.0
    return (closes[move.end] - opens[entry]) / total


def cap_class(market_cap_eur: float | None, p: CauseParams) -> str:
    if market_cap_eur is None or market_cap_eur <= 0:
        return "inconnue"
    if market_cap_eur < p.cap_small_eur:
        return "petite (< 1 Md€)"
    return "moyenne (1–10 Md€)" if market_cap_eur < p.cap_large_eur else "grande (≥ 10 Md€)"


def allocate[K](counts: Mapping[K, int], total: int) -> dict[K, int]:
    """``total`` draws split across strata in proportion to ``counts`` (largest remainder, ties by key order)."""
    n = sum(counts.values())
    if n <= 0 or total <= 0:
        return {k: 0 for k in counts}
    raw = {k: total * v / n for k, v in counts.items()}
    out = {k: int(math.floor(x)) for k, x in raw.items()}
    rest = total - sum(out.values())
    for k in sorted(raw, key=lambda k: (-(raw[k] - out[k]), str(k)))[:rest]:
        out[k] += 1
    return out


# --- outcomes (part B and the controls' base rates) ----------------------------------------------------------------


@dataclass
class Outcome:
    """What was capturable from the open of ``entry``: gross and net returns held h sessions, the excess over the
    venue's equal-weighted liquid half, and the docs/34 triple-barrier label (entry at that open)."""

    gross: dict[int, float | None]
    net_cto: dict[int, float | None]
    net_pea: dict[int, float | None]
    excess: dict[int, float | None]
    label: int | None
    week: str  # ISO week of the entry: the clusters of the weekly t


def iso_week(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-{w:02d}"


def forward_outcome(
    bars: Sequence[Bar],
    entry: int,
    horizons: Sequence[int],
    atr_prev: float | None,
    sigma: float | None,
    mp: MoveParams,
    cost_cto: float | None,
    cost_pea: float | None,
    day: date,
    bench: Callable[[int], float | None] | None = None,
) -> Outcome:
    """Bought at the open of ``entry`` (nothing after the previous close enters ``atr_prev``, ``sigma`` or the
    costs), sold at the close of the h-th session; net = gross − round-trip cost fraction (CTO; PEA when
    eligible); ``bench(h)``: the venue's return over the same window."""
    gross = {h: forward_from_open(bars, entry, h) for h in horizons}
    net_cto = {h: (g - cost_cto) if g is not None and cost_cto is not None else None for h, g in gross.items()}
    net_pea = {h: (g - cost_pea) if g is not None and cost_pea is not None else None for h, g in gross.items()}
    excess: dict[int, float | None] = {}
    for h, g in gross.items():
        b = bench(h) if bench is not None and g is not None else None
        excess[h] = None if g is None or b is None else g - b
    label = barrier_label(bars, entry - 1, atr_prev, sigma, mp) if entry >= 1 else None
    return Outcome(gross, net_cto, net_pea, excess, label, iso_week(day))


def mean_weekly(pairs: Sequence[tuple[str, float]], mask_below: int) -> dict[str, Any]:
    """``mean_stats`` of the values, plus the t of the weekly means (events of the same week are not independent:
    the pooled t is optimistic)."""
    out = mean_stats([x for _, x in pairs], mask_below)
    if "moyenne" not in out:
        return out
    by: dict[str, list[float]] = defaultdict(list)
    for w, x in pairs:
        by[w].append(x)
    weekly = [statistics.fmean(v) for v in by.values()]
    out["semaines"] = len(weekly)
    if len(weekly) >= max(2, mask_below):
        sd = statistics.stdev(weekly)
        out["t_semaines"] = round(statistics.fmean(weekly) / (sd / math.sqrt(len(weekly))), 2) if sd > 0 else None
    return out


def _ratio(a: Mapping[str, Any], b: Mapping[str, Any]) -> float | None:
    x, y = a.get("part"), b.get("part")
    if x is None or not y:
        return None
    return round(float(x) / float(y), 2)


def _column(rows: Sequence[Outcome], attr: str, h: int) -> list[tuple[str, float]]:
    return [(o.week, v) for o in rows if (v := getattr(o, attr).get(h)) is not None]


def event_summary(outs: Sequence[Outcome], base: Sequence[Outcome], horizons: Sequence[int],
                  p: CauseParams) -> dict[str, Any]:  # fmt: skip
    """Per horizon: gross, net CTO, net PEA, excess over the venue (mean, 95 % interval, t, weekly t), the controls'
    net CTO and the gap to it; the triple-barrier rate against the controls' base rate (lift)."""
    mb = p.mask_below
    res: dict[str, Any] = {"n": len(outs)}
    for h in horizons:
        cell: dict[str, Any] = {
            "brut": mean_weekly(_column(outs, "gross", h), mb),
            "net_cto": mean_weekly(_column(outs, "net_cto", h), mb),
            "net_pea": mean_weekly(_column(outs, "net_pea", h), mb),
            "exces_place": mean_weekly(_column(outs, "excess", h), mb),
            "controles_net_cto": mean_stats([x for _, x in _column(base, "net_cto", h)], mb),
        }
        a, b = cell["net_cto"].get("moyenne"), cell["controles_net_cto"].get("moyenne")
        cell["ecart_net_cto_controles"] = None if a is None or b is None else round(a - b, 4)
        res[str(h)] = cell
    labels = [o.label for o in outs if o.label is not None]
    base_labels = [o.label for o in base if o.label is not None]
    res["barriere"] = share_stats(sum(labels), len(labels), mb)
    res["taux_de_base"] = share_stats(sum(base_labels), len(base_labels), mb)
    res["lift_barriere"] = _ratio(res["barriere"], res["taux_de_base"])
    return res


# --- attribution (part A) ------------------------------------------------------------------------------------------


@dataclass
class Unit:
    """One rise, fall or control, its causes and its strata (no price, no identifier)."""

    group: str  # RISES | FALLS | CONTROLS
    venue_group: str
    cap: str
    year: int
    flags: dict[str, Flag]
    amplitude: str | None = None  # moves only (docs/34 size bucket)
    shape: str | None = None
    vol: str | None = None  # docs/34 volatility class of σ20 at the close of t0 − 1
    detectors: tuple[str, ...] = ()
    gap_direction: int = 0
    move_ret: float | None = None  # the value's return over the first sessions
    market_ret: float | None = None  # the venue's equal-weighted return over the same sessions
    capturable: dict[str, float | None] = field(default_factory=dict)  # cause → share still ahead once known
    outcome: Outcome | None = None  # controls: from the open after t0 (base rates of part B)


def _cause_cell(units: Sequence[Unit], cause: str, p: CauseParams, with_timing: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for g in GROUPS:
        us = [u for u in units if u.group == g]
        if g == CONTROLS and cause in NO_CONTROL:
            out[g] = {"n": 0, "sans_objet": "un contrôle n'a pas de mouvement à expliquer"}
            continue
        cov = [u for u in us if (f := u.flags.get(cause)) is not None and f.present is not None]
        k = sum(1 for u in cov if u.flags[cause].present)
        out[g] = share_stats(k, len(cov), p.mask_below)
        out[g]["unites"] = len(us)
    out["lift_hausses"] = _ratio(out[RISES], out[CONTROLS])
    out["lift_baisses"] = _ratio(out[FALLS], out[CONTROLS])
    out["ratio_hausses_baisses"] = _ratio(out[RISES], out[FALLS])
    if with_timing:
        mom: dict[str, Any] = {}
        for t in TIMINGS:
            mom[t] = {}
            for g in GROUPS:
                if g == CONTROLS and cause in NO_CONTROL:
                    continue
                cov = [u for u in units if u.group == g and (f := u.flags.get(cause)) is not None
                       and f.present is not None]  # fmt: skip
                k = sum(1 for u in cov if u.flags[cause].present and u.flags[cause].timing == t)
                mom[t][g] = share_stats(k, len(cov), p.mask_below)
        out["moment"] = mom
        caps: dict[str, Any] = {}
        for g in (RISES, FALLS):
            xs = [x for u in units if u.group == g and (x := u.capturable.get(cause)) is not None]
            caps[g] = mean_stats(xs, p.mask_below)
        out["part_capturable"] = caps
    return out


def _none_cell(units: Sequence[Unit], test: Callable[[Unit], bool], p: CauseParams) -> dict[str, Any]:
    out: dict[str, Any] = {
        g: share_stats(sum(1 for u in units if u.group == g and test(u)), sum(1 for u in units if u.group == g),
                       p.mask_below)
        for g in GROUPS
    }  # fmt: skip
    out["lift_hausses"] = _ratio(out[RISES], out[CONTROLS])
    out["lift_baisses"] = _ratio(out[FALLS], out[CONTROLS])
    out["ratio_hausses_baisses"] = _ratio(out[RISES], out[FALLS])
    return out


def _no_cause(u: Unit) -> bool:
    return primary_cause(u.flags) == NO_CAUSE


def _no_information(u: Unit) -> bool:
    return without_information(u.flags)


def attribution(units: Sequence[Unit], detectors: Sequence[str], p: CauseParams) -> dict[str, Any]:
    """Per cause: share of rises, falls and controls where it is present (Wilson 95 %, among the units its source
    covers), lift over the controls, rise / fall ratio, timing, share of the move still ahead once known; the primary
    cause; the units without any cause; per detector."""
    n = {g: sum(1 for u in units if u.group == g) for g in GROUPS}
    mb = p.mask_below
    out: dict[str, Any] = {"unites": n, "causes": {c: _cause_cell(units, c, p) for c in CAUSES}}
    prim: dict[str, Any] = {}
    for g in GROUPS:
        us = [u for u in units if u.group == g]
        counts = {c: 0 for c in PRIMARY}
        for u in us:
            counts[primary_cause(u.flags)] += 1
        prim[g] = {c: share_stats(k, len(us), mb) for c, k in counts.items()}
    out["cause_principale"] = prim
    out["sans_cause"] = _none_cell(units, _no_cause, p)
    out["sans_information_ni_marche"] = _none_cell(units, _no_information, p)
    det: dict[str, Any] = {}
    for d in detectors:
        cell: dict[str, Any] = {g: share_stats(sum(1 for u in units if u.group == g and d in u.detectors), n[g], mb)
                                for g in GROUPS}  # fmt: skip
        cell["lift_hausses"] = _ratio(cell[RISES], cell[CONTROLS])
        cell["ratio_hausses_baisses"] = _ratio(cell[RISES], cell[FALLS])
        det[d] = cell
    out["par_detecteur"] = det
    gaps: dict[str, Any] = {}
    for g in GROUPS:
        with_gap = [u for u in units if u.group == g and u.flags.get(GAP, UNKNOWN).present]
        gaps[g] = share_stats(sum(1 for u in with_gap if u.gap_direction > 0), len(with_gap), mb)
    out["sauts_haussiers_parmi_les_sauts"] = gaps
    out["premieres_seances"] = {
        g: {
            "rendement_valeur": mean_stats([u.move_ret for u in units if u.group == g and u.move_ret is not None], mb),
            "rendement_place": mean_stats([u.market_ret for u in units if u.group == g and u.market_ret is not None],
                                          mb),
        }
        for g in GROUPS
    }  # fmt: skip
    return out


BREAKDOWNS: dict[str, Callable[[Unit], str | None]] = {
    "groupe_de_places": lambda u: u.venue_group,
    "capitalisation": lambda u: u.cap,
    "annee": lambda u: str(u.year),
    "volatilite": lambda u: u.vol,
    "amplitude": lambda u: u.amplitude,
    "forme": lambda u: u.shape,
}


def breakdowns(units: Sequence[Unit], p: CauseParams) -> dict[str, dict[str, Any]]:
    """The cause shares per stratum (venue group, market cap, year, volatility; amplitude and shape of the moves —
    controls have none and are compared overall)."""
    out: dict[str, dict[str, Any]] = {}
    for name, key in BREAKDOWNS.items():
        groups: dict[str, list[Unit]] = defaultdict(list)
        for u in units:
            k = key(u)
            if k is not None:
                groups[k].append(u)
        out[name] = {
            k: {
                "unites": {g: sum(1 for u in v if u.group == g) for g in GROUPS},
                "causes": {
                    **{c: _cause_cell(v, c, p, with_timing=False) for c in CAUSES},
                    NO_CAUSE: _none_cell(v, _no_cause, p),
                    "sans_information_ni_marche": _none_cell(v, _no_information, p),
                },
            }
            for k, v in sorted(groups.items())
        }
    return out


def discoveries(events: Mapping[str, Mapping[str, Any]], horizons: Sequence[int], p: CauseParams) -> dict[str, Any]:
    """Multiple testing: every mean and rate read in part B is a cell; those whose weekly t reaches ``t_discovery``
    (and their pooled t) are listed as leads to pre-register — never as findings."""
    cells = 0
    leads: list[dict[str, Any]] = []
    for cause, ev in events.items():
        for h in horizons:
            cell = ev.get(str(h)) or {}
            for m in ("net_cto", "net_pea", "exces_place"):
                st = cell.get(m) or {}
                if "moyenne" not in st:
                    continue
                cells += 1
                tw = st.get("t_semaines")
                if tw is not None and abs(tw) >= p.t_discovery:
                    leads.append({"cause": cause, "seances": h, "mesure": m, "moyenne": st["moyenne"],
                                  "t": st.get("t"), "t_semaines": tw})  # fmt: skip
        if "part" in (ev.get("barriere") or {}):
            cells += 1
    return {"cellules_lues": cells, "seuil_t": p.t_discovery, "pistes": leads}


__all__ = [
    "CAUSES",
    "CAUSE_FR",
    "CauseParams",
    "Flag",
    "Move",
    "Outcome",
    "Unit",
    "allocate",
    "attribution",
    "breakdowns",
    "cap_class",
    "capturable_share",
    "during",
    "earnings_code",
    "entry_session",
    "event_summary",
    "explained_flag",
    "ew_return",
    "find_falls",
    "find_rises",
    "flag_from_codes",
    "flag_from_sessions",
    "forward_outcome",
    "gap_flag",
    "gap_threshold",
    "night",
    "opening_gap",
    "place_time",
    "primary_cause",
    "rvol_flag",
    "rvol_series",
    "setup_flag",
    "size_bucket",
    "timing",
    "without_information",
]
