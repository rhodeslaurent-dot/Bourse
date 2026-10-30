# ruff: noqa: E501
"""docs/36 — causes of the rises, step 1 (user request of 30/10/2026), exploratory, no LLM.

On the history stored on the PC (European venue groups P0 / P1 / P2, 2017 → end of the data, as docs/34):

1. **three groups** under the same rules: rises (docs/34 episodes), the symmetric falls of the same zigzag, and
   random value-sessions (controls) stratified like the rises by venue group, market cap and year;
2. **causes** of each one in the window [t0 − 5, t0 + 1] (``app.domain.move_causes``), with their timing (known
   before, before the open of t0, during): earnings (``earnings_events`` + confirmed past calendar rows),
   managers' purchases (AMF archive, rule X5 of registration n° 5, French values listed in Paris), letters'
   recommendations (``letters_study._collect``: buy / watch, name written in the letter), stored news, market and
   sector equal-weighted moves, opening gap, abnormal volume before, production detector setups (``DETECTORS`` with
   production parameters, d8 excepted), dividend detachments; a source that does not cover a unit leaves it
   « unknown », never « absent »;
3. **part A — attribution**: share of rises, falls and controls per cause, lift, rise / fall ratio, timing, share
   of the move still ahead once the cause was known, breakdowns;
4. **part B — event study**: every occurrence of each cause in the perimeter (seeded sample beyond
   ``events_max``; detector setups on a seeded sample of values, every session), bought at the next open after it
   was known: net returns at 5 / 10 / 20 sessions (CTO and PEA costs of ``moves._costs``), excess over the venue,
   docs/34 triple-barrier rate against the controls' base rate. The rows of a question that a frozen registration
   answers (earnings: n° 3; managers' purchases: n° 5) stay masked until that registration has run (ledger).

Seals of docs/34: no date before 2017-01-02, no price before 2015-10-01, New York / London / Zurich unread; a line in
the research ledger per run. Prices are those stored (unadjusted); a unit or an event whose window crosses a
corporate action (adjustment factor jump > 20 %) is skipped and counted. The report holds aggregates only (no
price, no name, no ISIN: ``assert_no_price_fields``).
"""

from __future__ import annotations

import bisect
import json
import logging
import random
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import LoadedConfig
from app.db.models import EarningsCalendarRow, EarningsEvent, InsiderDeclaration, NewsItem, NewsletterItem
from app.domain import move_causes as mc
from app.domain.calendar import MarketCalendars, VenueCalendar
from app.domain.detectors import DETECTORS, DetectorContext
from app.domain.indicators import Bar, compute_daily_features
from app.domain.letters_study import StudyParams, share_stats
from app.domain.moves import MoveParams, atr_series, daily_sigma, size_bucket, vol_bucket
from app.domain.zones import detect_zones
from app.services import letters_study as lss
from app.services.moves import MIN_BARS, WINDOW, EarningsIndex, Replay, _replay_context

log = logging.getLogger("bourse.move_causes")
Progress = Callable[[str], None]
FIRST_DAY = date(2017, 1, 2)  # before: the European history is kept for the confirmations (docs/34 §34.5)
SEAL_END = date(2015, 9, 30)  # sealed period of the research (docs/31): never read
LOOKBACK_DAYS = 420  # calendar days of history before the period (ATR, σ, detectors, RS rank), as docs/34
FORWARD_DAYS = 45  # calendar days read after the period (20 sessions of outcome)
PARIS = ZoneInfo("Europe/Paris")
REG3 = "research/prereg/2026-10-28_reg3.yaml"
REG5 = "research/prereg/2026-10-30_reg5.yaml"
REGISTERED = {mc.EV_EARNINGS: (REG3, "n° 3"), mc.EV_INSIDER: (REG5, "n° 5")}
NOT_COVERED = (
    "historique des nouvelles (seule la couverture récente de l'outil est lue : étape 2)",
    "changements de recommandations et d'objectifs d'analystes (aucune source datée à l'époque)",
    "déclarations de dirigeants hors de France (AMF seulement, archive depuis 2021-11)",
    "podcasts, vidéos et réseaux sociaux (aucun historique)",
    "indices d'entrée en indice, rachats d'actions, annonces d'OPA hors nouvelles stockées",
)


# --- prices -----------------------------------------------------------------------------------------------------


@dataclass
class Panel:
    """Daily bars of the perimeter as (sessions × values) arrays; invalid cells are NaN."""

    days: list[date]
    cols: list[str]
    col: dict[str, int]
    O: np.ndarray  # noqa: E741
    H: np.ndarray
    L: np.ndarray
    C: np.ndarray
    V: np.ndarray
    valid: np.ndarray
    ca: np.ndarray  # adjustment factor jump > ca_jump on this row of the value's own sessions (corporate action)
    div: np.ndarray  # smaller jump > dividend_min (dividend detachment)
    adj: np.ndarray  # adjustment factor known
    liq: np.ndarray  # mean traded value of the value's 20 own sessions up to the row
    row: dict[date, int] = field(default_factory=dict)


def _panel(s: Session, isins: list[str], first: date, last: date, p: mc.CauseParams) -> Panel:
    import pandas as pd

    from app.services.research import _read_price_panel

    days_np, cols, arr = _read_price_panel(s, isins, first, last) if isins else (np.array([]), [], {})
    if not cols:  # no stored price in the perimeter: an empty panel, an empty report
        arr = {k: np.empty((0, 0)) for k in ("open", "high", "low", "close", "volume", "adj_close")}
        days_np = np.array([], dtype="datetime64[D]")
    days = [d for d in days_np.astype("datetime64[D]").astype(object)]
    O, H, L, C, V, A = (arr[k] for k in ("open", "high", "low", "close", "volume", "adj_close"))  # noqa: E741
    weekday = np.array([d.weekday() < 5 for d in days], dtype=bool)
    with np.errstate(invalid="ignore"):
        valid = (np.isfinite(O) & np.isfinite(H) & np.isfinite(L) & np.isfinite(C) & (O > 0) & (H > 0) & (L > 0)
                 & (C > 0) & (H >= L) & weekday[:, None])  # fmt: skip
        adj = valid & np.isfinite(A) & (A > 0)
    for M in (O, H, L, C):
        M[~valid] = np.nan
    V = np.where(valid, np.nan_to_num(V, nan=0.0), 0.0)
    ca = np.zeros_like(valid)
    div = np.zeros_like(valid)
    liq = np.full(C.shape, np.nan)
    for j in range(len(cols)):
        r = np.flatnonzero(valid[:, j])
        if r.size == 0:
            continue
        f = np.where(adj[r, j], A[r, j] / C[r, j], np.nan)
        prev = pd.Series(f).ffill().shift(1).to_numpy()  # as the backtest: the last known factor before
        with np.errstate(invalid="ignore", divide="ignore"):
            jump = np.abs(f / prev - 1)
        ok = np.isfinite(jump)
        big = ok & (jump > p.ca_jump)
        ca[r[big], j] = True
        div[r[ok & ~big & (jump > p.dividend_min)], j] = True
        liq[r, j] = lss._rolling_mean(C[r, j] * V[r, j], 20)
    del A, arr
    return Panel(days, cols, {k: i for i, k in enumerate(cols)}, O, H, L, C, V, valid, ca, div, adj, liq,
                 {d: i for i, d in enumerate(days)})  # fmt: skip


@dataclass
class Value:
    """One value's own sessions (as docs/34: the stored bars of the value)."""

    j: int
    isin: str
    rows: np.ndarray  # panel rows of the own sessions
    days: list[date]
    bars: list[Bar]
    opens: list[float]
    closes: list[float]
    atr: list[float | None]
    rvol: list[float | None]
    ca: list[int]  # own indices (sorted)
    div: list[int]
    adj: np.ndarray

    def ca_in(self, a: int, b: int) -> bool:
        i = bisect.bisect_left(self.ca, a)
        return i < len(self.ca) and self.ca[i] <= b


def _value(pn: Panel, j: int, p: mc.CauseParams | None = None) -> Value:
    r = np.flatnonzero(pn.valid[:, j])
    o, h, lo, c, v = (M[r, j].tolist() for M in (pn.O, pn.H, pn.L, pn.C, pn.V))
    bars = [Bar(a, b, x, y, int(z)) for a, b, x, y, z in zip(o, h, lo, c, v, strict=True)]
    ca = np.flatnonzero(pn.ca[r, j]).tolist()  # suspect prices are skipped like a corporate action
    ca = sorted(set(ca) | set(mc.suspect_sessions(o, c, (p or mc.CauseParams()).suspect_ratio)))
    return Value(j, pn.cols[j], r, [pn.days[i] for i in r.tolist()], bars, o, c, atr_series(bars),
                 mc.rvol_series(v), ca, np.flatnonzero(pn.div[r, j]).tolist(), pn.adj[r, j])  # fmt: skip


def _bench(pn: Panel, cols: np.ndarray | None, a: int, b: int, base: str, exclude: int, liquid: bool,
           p: mc.CauseParams) -> float | None:  # fmt: skip
    """Equal-weighted return of ``cols`` from the open (``ouverture``) or close of row ``a`` to the close of row
    ``b``: values with valid prices and no corporate action inside, the value itself excluded; with ``liquid``, the
    liquid half of the venue as the letters study (``letters_study._bench``)."""
    if cols is None or cols.size == 0 or b < a or a < 1:
        return None
    p0 = (pn.O if base == "ouverture" else pn.C)[a, cols]
    p1 = pn.C[b, cols]
    ok = cols != exclude
    if b > a:
        ok &= ~pn.ca[a + 1 : b + 1][:, cols].any(axis=0)
    return mc.ew_return(p0, p1, ok, pn.liq[a - 1, cols] if liquid else None, p.min_peers)


# --- sources ----------------------------------------------------------------------------------------------------


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


@dataclass
class Sources:
    earnings: dict[str, list[tuple[date, str]]] = field(default_factory=dict)
    insiders: dict[str, list[tuple[datetime, float | None]]] = field(default_factory=dict)
    insider_price_ratio: float = 2.0
    letters: dict[str, list[datetime]] = field(default_factory=dict)
    news: dict[str, list[datetime]] = field(default_factory=dict)
    letters_span: tuple[date, date] | None = None
    news_span: tuple[date, date] | None = None
    insider_span: tuple[date, date] | None = None
    insider_months: set[tuple[int, int]] = field(default_factory=set)
    meta: dict[str, Any] = field(default_factory=dict)
    loaded_by_year: dict[str, Counter[int]] = field(default_factory=dict)


def _span(lo: datetime | None, hi: datetime | None) -> tuple[date, date] | None:
    return (_aware(lo).date(), _aware(hi).date()) if lo is not None and hi is not None else None


def _sources(s: Session, isins: set[str], start: date, end: date, last_day: date) -> Sources:
    src = Sources()
    meta: dict[str, Any] = {}
    # earnings: history (earnings_events) + confirmed calendar rows already past; conflicting hours → unknown
    whens: dict[str, dict[date, set[str]]] = defaultdict(lambda: defaultdict(set))
    n_hist = n_cal = 0
    for isin, d, w in s.execute(select(EarningsEvent.isin, EarningsEvent.day, EarningsEvent.when)):
        if isin in isins:
            whens[isin][d].add(w if w in ("bmo", "amc", "intraday") else "unknown")
            n_hist += 1
    for isin, d, w in s.execute(
        select(EarningsCalendarRow.isin, EarningsCalendarRow.day, EarningsCalendarRow.when).where(
            EarningsCalendarRow.confirmed.is_(True), EarningsCalendarRow.day <= last_day
        )
    ):
        if isin in isins and d not in whens[isin]:
            whens[isin][d].add(w if w in ("bmo", "amc", "intraday") else "unknown")
            n_cal += 1
    conflicts = 0
    by_year: Counter[int] = Counter()
    for isin, per_day in whens.items():
        rows: list[tuple[date, str]] = []
        for d, ws in sorted(per_day.items()):
            known = ws - {"unknown"}
            if len(known) > 1:
                conflicts += 1
            rows.append((d, next(iter(known)) if len(known) == 1 else "unknown"))
            by_year[d.year] += 1
        src.earnings[isin] = rows
    meta["resultats"] = {"lignes_historique": n_hist, "lignes_calendrier_confirme": n_cal,
                         "heures_contradictoires": conflicts,
                         "heure_inconnue": sum(1 for v in src.earnings.values() for _, w in v if w == "unknown"),
                         "valeurs": len(src.earnings)}  # fmt: skip
    src.loaded_by_year[mc.EARNINGS] = by_year
    # managers' purchases: the AMF archive, rule X5 of registration n° 5 (any role, on market, shares, ≥ 10 000 €)
    src.insider_span = _span(*s.execute(select(func.min(InsiderDeclaration.market_timestamp),
                                               func.max(InsiderDeclaration.market_timestamp))).one())  # fmt: skip
    src.insider_months = {
        (_aware(ts).year, _aware(ts).month) for (ts,) in s.execute(select(InsiderDeclaration.market_timestamp))
    }
    try:
        from app.domain.research.insider_triggers import OK, CellRule, rules_from, screen
        from app.services.insider_research import load_rows
        from app.services.research import load_prereg

        plan = load_prereg(REG5)
        rules = rules_from(plan.assumptions)
        rule = CellRule.of(next(c for c in plan.cells if c.family == "X5").params)
        src.insider_price_ratio = rules.max_price_ratio
        funnel: Counter[str] = Counter()
        iy: Counter[int] = Counter()
        for r in load_rows(s):
            why = screen(r, rules, rule)
            funnel[why] += 1
            if why == OK and r.isin in isins:
                ts = _aware(r.published_at)
                src.insiders.setdefault(str(r.isin), []).append((ts, r.price))
                iy[ts.astimezone(PARIS).year] += 1
        meta["dirigeants"] = {"regle": "X5 de l'enregistrement n° 5 (toute fonction, acquisition en bourse d'actions, ≥ 10 000 €)",
                              "entonnoir": dict(sorted(funnel.items())),
                              "retenues_dans_le_perimetre": sum(len(v) for v in src.insiders.values())}  # fmt: skip
        src.loaded_by_year[mc.INSIDER] = iy
    except (OSError, StopIteration, KeyError, ValueError) as exc:  # the plan file is the only source of the rule
        meta["dirigeants"] = {"indisponible": f"règle X5 du plan n° 5 illisible ({type(exc).__name__})"}
    # letters: buy / watch whose name or ISIN is written in the letter (letters study)
    src.letters_span = _span(*s.execute(select(func.min(NewsletterItem.received_at),
                                               func.max(NewsletterItem.received_at))).one())  # fmt: skip
    excl: Counter[str] = Counter()
    lmeta: dict[str, Any] = {}
    ly: Counter[int] = Counter()
    for c in lss._collect(s, start - timedelta(days=10), end, None, excl, lmeta):
        if c.isin in isins:
            src.letters.setdefault(c.isin, []).append(c.received)
            ly[c.received.astimezone(PARIS).year] += 1
    meta["lettres"] = {"recommandations_lues": lmeta.get("recommandations_lues", 0),
                       "retenues_dans_le_perimetre": sum(len(v) for v in src.letters.values()),
                       "exclusions": dict(sorted(excl.items()))}  # fmt: skip
    src.loaded_by_year[mc.LETTER] = ly
    # stored news: coverage = the tool's collection span (fetched_at)
    src.news_span = _span(*s.execute(select(func.min(NewsItem.fetched_at), func.max(NewsItem.fetched_at))).one())
    ny: Counter[int] = Counter()
    n_news = 0
    for isins_json, pub in s.execute(
        select(NewsItem.isins, NewsItem.published_at).where(NewsItem.published_at.is_not(None))
    ):
        for isin in isins_json or []:
            if isinstance(isin, str) and isin in isins:
                ts = _aware(pub)
                src.news.setdefault(isin, []).append(ts)
                ny[ts.year] += 1
                n_news += 1
    meta["nouvelles"] = {"mentions_dans_le_perimetre": n_news,
                         "collecte": [str(x) for x in src.news_span] if src.news_span else None}  # fmt: skip
    src.loaded_by_year[mc.NEWS] = ny
    src.meta = meta
    return src


def _venue(calendars: MarketCalendars, mic: str) -> tuple[ZoneInfo, time, time, VenueCalendar | None]:
    cal = calendars.venues.get(mic)
    if cal is None:
        return PARIS, time(9, 0), time(17, 30), None
    return cal.tz, cal.open_time, cal.close_time, cal


def _close_on(cal: VenueCalendar | None, default: time, d: date) -> time:
    if cal is not None and d.year in cal.known_years:
        ses = cal.session(d)
        if ses.close_time is not None:
            return ses.close_time
    return default


def _codes(v: Value, mic: str, src: Sources, calendars: MarketCalendars, p: mc.CauseParams,
           counts: Counter[str]) -> dict[str, list[int]]:  # fmt: skip
    """Time codes of the information events of one value on its own sessions."""
    tz, ot, ct, cal = _venue(calendars, mic)

    def at(ts: datetime) -> int | None:
        local = _aware(ts).astimezone(tz)
        return mc.place_time(local, ot, _close_on(cal, ct, local.date()), v.days, p.max_gap_days)

    out: dict[str, list[int]] = {}
    out[mc.EARNINGS] = sorted(c for d, w in src.earnings.get(v.isin, [])
                              if (c := mc.earnings_code(d, w, v.days, p.max_gap_days)) is not None)  # fmt: skip
    ins: list[int] = []
    for ts, px in src.insiders.get(v.isin, []):
        c = at(ts)
        if c is None:
            counts["dirigeant_sans_seance_proche"] += 1
            continue
        close = v.closes[mc.code_session(c)]
        ratio = (px or 0.0) / close if close > 0 else float("inf")
        if not (1 / src.insider_price_ratio <= ratio <= src.insider_price_ratio):
            counts["dirigeant_prix_incoherent"] += 1  # a misread price (registration n° 5 rule)
            continue
        ins.append(c)
    out[mc.INSIDER] = sorted(ins)
    out[mc.LETTER] = sorted(c for ts in src.letters.get(v.isin, []) if (c := at(ts)) is not None)
    out[mc.NEWS] = sorted(c for ts in src.news.get(v.isin, []) if (c := at(ts)) is not None)
    return out


def _within(span: tuple[date, date] | None, a: date, b: date) -> bool:
    return span is not None and span[0] <= a and b <= span[1]


# --- detectors --------------------------------------------------------------------------------------------------


def _fired(rp: Replay, window: list[Bar], rs: float | None) -> list[str]:
    """Detectors (production parameters, d8 excepted: it needs the forum radar of the day) with a setup at the
    close of ``window[-1]`` — the two-step detection of ``moves.responses`` (without, then with the zones)."""
    if len(window) < MIN_BARS:
        return []
    ctx = DetectorContext(rs, [], rp.wick, rp.stop_buffer)
    zones = None
    out: list[str] = []
    for name, dp in rp.det_params.items():
        if name == "d8_buzz_precoce" or not DETECTORS[name](dp).detect(window, ctx):
            continue
        if zones is None:
            f = compute_daily_features(window)
            zp = rp.zone_p
            zones = detect_zones(window[-int(zp.get("lookback_sessions", 250)) :], f.atr14 or 0.0,
                                 int(zp.get("pivot_window", 5)), float(zp.get("merge_atr_mult", 0.5)),
                                 int(zp.get("min_touches", 2)))  # fmt: skip
        if DETECTORS[name](dp).detect(window, DetectorContext(rs, zones, rp.wick, rp.stop_buffer)):
            out.append(name)
    return out


# --- study ------------------------------------------------------------------------------------------------------


@dataclass
class Ctx:
    config: LoadedConfig
    calendars: MarketCalendars
    p: mc.CauseParams
    mp: MoveParams
    rp: Replay
    costs: lss.Ctx
    pn: Panel
    src: Sources
    ranks: lss.GroupCloses
    insts: dict[str, Any]
    groups_of: dict[str, str]
    mic_cols: dict[str, np.ndarray]
    sector_cols: dict[str, np.ndarray]
    counts: Counter[str] = field(default_factory=Counter)


def _strata(c: Ctx, isin: str) -> tuple[str, str]:
    inst = c.insts[isin]
    return c.groups_of.get(inst.mic or "", "?"), mc.cap_class(inst.market_cap_eur, c.p)


def _outcome(c: Ctx, v: Value, e: int) -> mc.Outcome:
    """Bought at the open of own session ``e``, everything known at the close of e − 1."""
    p, mp = c.p, c.mp
    inst = c.insts[v.isin]
    atr_prev = v.atr[e - 1]
    sigma = daily_sigma(v.closes, e, mp.sigma_sessions)
    cto, pea = lss._costs(c.costs, inst)
    rf = mp.stop_atr * atr_prev / v.closes[e - 1] if atr_prev and v.closes[e - 1] > 0 else None
    cost_cto = c.costs.frac(cto, rf) if rf else None
    cost_pea = c.costs.frac(pea, rf) if rf and pea is not None else None
    cols = c.mic_cols.get(inst.mic or "")

    def bench(h: int) -> float | None:
        k = e + h - 1
        return (
            _bench(c.pn, cols, int(v.rows[e]), int(v.rows[k]), "ouverture", v.j, True, p) if k < len(v.rows) else None
        )

    return mc.forward_outcome(v.bars, e, p.horizons, atr_prev, sigma, mp, cost_cto, cost_pea, v.days[e], bench)


def _unit(c: Ctx, v: Value, codes: dict[str, list[int]], eidx: EarningsIndex | None, t0: int, group: str,
          move: mc.Move | None) -> mc.Unit:  # fmt: skip
    p = c.p
    inst = c.insts[v.isin]
    vg, cap = _strata(c, v.isin)
    n = len(v.bars)
    d_lo, d_hi = v.days[max(0, t0 - p.window_before)], v.days[min(n - 1, t0 + p.window_after)]
    flags: dict[str, mc.Flag] = {}
    lo_adj = max(0, t0 - p.window_before - 1)
    flags[mc.CORPORATE] = mc.flag_from_sessions(v.div, t0, p, known=bool(v.adj[lo_adj : t0 + p.window_after + 1].all()))
    flags[mc.EARNINGS] = mc.flag_from_codes(codes[mc.EARNINGS], t0, p, eidx is not None and eidx.covered(t0))
    paris_fr = v.isin.startswith("FR") and inst.mic == "XPAR"
    flags[mc.INSIDER] = mc.flag_from_codes(codes[mc.INSIDER], t0, p, paris_fr and _within(c.src.insider_span, d_lo, d_hi)
                                           and (v.days[t0].year, v.days[t0].month) in c.src.insider_months)  # fmt: skip
    flags[mc.LETTER] = mc.flag_from_codes(codes[mc.LETTER], t0, p, _within(c.src.letters_span, d_lo, d_hi))
    flags[mc.NEWS] = mc.flag_from_codes(codes[mc.NEWS], t0, p, _within(c.src.news_span, d_lo, d_hi))
    # market / sector over the first sessions of the move (controls: same span, descriptive only)
    m = p.first_sessions if move is None else min(p.first_sessions, move.sessions)
    end_s = min(t0 + m - 1, n - 1)
    move_ret = v.closes[end_s] / v.closes[t0 - 1] - 1
    a, b = int(v.rows[t0 - 1]), int(v.rows[end_s])
    mkt = _bench(c.pn, c.mic_cols.get(inst.mic or ""), a, b, "cloture", v.j, True, p)
    sec = _bench(c.pn, c.sector_cols.get(inst.sector or ""), a, b, "cloture", v.j, False, p) if inst.sector else None
    if move is None:
        flags[mc.MARKET] = flags[mc.SECTOR] = mc.UNKNOWN  # sans objet: a control has no move to explain
    else:
        flags[mc.MARKET] = mc.explained_flag(move_ret, mkt, move.sign, end_s, p)
        flags[mc.SECTOR] = mc.explained_flag(move_ret, sec, move.sign, end_s, p)
    gap = mc.opening_gap(v.opens[t0], v.closes[t0 - 1])
    flags[mc.GAP], gdir = mc.gap_flag(gap, mc.gap_threshold(v.closes[t0 - 1], v.atr[t0 - 1], p), t0)
    flags[mc.RVOL] = mc.rvol_flag(v.rvol, t0, p)
    dets: list[str] = []
    for k in range(t0 - p.setup_sessions, t0):
        if k < 0:
            continue
        window = v.bars[max(0, k - WINDOW + 1) : k + 1]
        for d in _fired(c.rp, window, c.ranks.rank(v.isin, v.days[k])):
            if d not in dets:
                dets.append(d)
    flags[mc.SETUP] = mc.setup_flag(dets, t0)
    capt: dict[str, float | None] = {}
    if move is not None:
        for cause, f in flags.items():
            if f.present and f.entry is not None:
                capt[cause] = mc.capturable_share(v.closes, v.opens, move, f.entry)
    u = mc.Unit(group, vg, cap, v.days[t0].year, flags,
                amplitude=size_bucket(move.magnitude) if move is not None else None,
                shape=move.shape if move is not None else None, detectors=tuple(sorted(dets)), gap_direction=gdir,
                move_ret=move_ret if end_s > t0 - 1 else None, market_ret=mkt, capturable=capt,
                vol=vol_bucket(daily_sigma(v.closes, t0, c.mp.sigma_sessions)))  # fmt: skip
    if move is None:  # control: the base rates of part B, from the open after t0
        e = t0 + 1
        if e + max(p.horizons) - 1 < n and not v.ca_in(e - p.comparability_before, e + max(p.horizons)):
            u.outcome = _outcome(c, v, e)
    return u


def _refractory(entries: list[int], gap: int) -> list[int]:
    out: list[int] = []
    for e in sorted(set(entries)):
        if not out or e - out[-1] >= gap:
            out.append(e)
    return out


def _screen_events(v: Value, raw: Mapping[str, list[int]], p: mc.CauseParams, counts: dict[str, Counter[str]],
                   years: dict[str, Counter[int]]) -> dict[str, list[int]]:  # fmt: skip
    """One occurrence per value and cause every ``refractory`` sessions (the first, outcome-free), then only those
    with the 20 sessions of outcome stored and no corporate action from the inputs (ATR, σ, RVOL) to the exit."""
    n, hmax = len(v.bars), max(p.horizons)
    out: dict[str, list[int]] = {}
    for cause, es in raw.items():
        kept: list[int] = []
        first = _refractory(es, p.refractory)
        for e in first:
            years[cause][v.days[e - 1].year] += 1
            if e + hmax - 1 >= n:
                counts[cause]["fin_des_donnees"] += 1
            elif v.ca_in(e - p.comparability_before, e + hmax):
                counts[cause]["operation_sur_titres"] += 1
            else:
                kept.append(e)
        counts[cause]["occurrences"] += len(es)
        counts[cause]["apres_delai"] += len(first)
        out[cause] = kept
    return out


def _events_of(c: Ctx, v: Value, codes: dict[str, list[int]], lo_k: int, hi_k: int,
               counts: dict[str, Counter[str]], years: dict[str, Counter[int]]) -> dict[str, list[int]]:  # fmt: skip
    """Entry sessions of every occurrence of each cause (detector setups apart), one per value and cause every
    ``refractory`` sessions, with 20 sessions of data after and no corporate action from the inputs to the exit."""
    p = c.p
    raw: dict[str, list[int]] = defaultdict(list)
    for k in range(max(lo_k, 1), hi_k + 1):
        thr = mc.gap_threshold(v.closes[k - 1], v.atr[k - 1], p)
        g = mc.opening_gap(v.opens[k], v.closes[k - 1])
        if thr is not None and g is not None and abs(g) >= thr:
            raw[mc.EV_GAP_UP if g > 0 else mc.EV_GAP_DOWN].append(k + 1)  # known at the open of k: bought at k + 1
        rv = v.rvol[k]
        if rv is not None and rv >= p.rvol_min:
            raw[mc.EV_RVOL].append(k + 1)  # known at the close of k
    for cause in (mc.EV_EARNINGS, mc.EV_INSIDER, mc.EV_LETTER):
        for code in codes[cause]:
            e = mc.entry_session(code)
            if lo_k <= e - 1 <= hi_k:
                raw[cause].append(e)
    return _screen_events(v, raw, p, counts, years)


def _setup_events(c: Ctx, v: Value, lo_k: int, hi_k: int, counts: dict[str, Counter[str]],
                  years: dict[str, Counter[int]]) -> dict[str, list[int]]:  # fmt: skip
    p = c.p
    raw: dict[str, list[int]] = defaultdict(list)
    for k in range(lo_k, hi_k + 1):
        window = v.bars[max(0, k - WINDOW + 1) : k + 1]
        for d in _fired(c.rp, window, c.ranks.rank(v.isin, v.days[k])):
            raw[mc.SETUP_PREFIX + d].append(k + 1)
    return _screen_events(v, raw, p, counts, years)


def registered_state(ledger: Sequence[Mapping[str, str]]) -> dict[str, dict[str, Any]]:
    """For each cause whose question a frozen registration answers: has that plan run (a ``run`` line of its hash in
    the research ledger)? Until then its part-B row stays masked (docs/36: the confirmatory reading comes first)."""
    from app.domain.research.prereg import file_sha256

    out: dict[str, dict[str, Any]] = {}
    for cause, (path, label) in REGISTERED.items():
        try:
            sha = file_sha256(Path(path).read_bytes())
        except OSError:
            sha = None
        ran = sha is not None and any(r.get("kind", "run") == "run" and r.get("prereg_sha256") == sha for r in ledger)
        out[cause] = {"enregistrement": label, "execute": ran}
    return out


def run_study(
    s: Session,
    config: LoadedConfig,
    calendars: MarketCalendars,
    start: date,
    end: date,
    rises: int = 1500,
    falls: int = 1500,
    controls: int = 1500,
    seed: int = 20261030,
    setup_values: int = 120,
    events_max: int | None = None,
    progress: Progress | None = None,
    max_instruments: int | None = None,
    ledger: Sequence[Mapping[str, str]] = (),
    params: mc.CauseParams | None = None,
    move_params: MoveParams | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    from app.services.research import mic_groups, select_instruments

    if start < FIRST_DAY:
        raise ValueError("avant 2017 les données européennes sont réservées à la confirmation (docs/34 §34.5)")
    p = params or mc.CauseParams()
    if events_max is not None:
        p = replace(p, events_max=events_max)
    mp = move_params or MoveParams()
    say = progress or (lambda m: None)
    rng = random.Random(seed)
    by_group, without = select_instruments(s, config, calendars, "all", max_instruments)  # P0 / P1 / P2 only
    groups_of = mic_groups(config, calendars)
    insts = {i.isin: i for grp in by_group.values() for i in grp}
    first = max(SEAL_END + timedelta(days=1), start - timedelta(days=LOOKBACK_DAYS))
    last = min(today or date.today(), end + timedelta(days=FORWARD_DAYS))
    say(
        f"{len(insts)} valeur(s) des places européennes (groupes {', '.join(sorted(by_group))}) ; chargement des cours…"
    )
    pn = _panel(s, sorted(insts), first, last, p)
    say(f"{len(pn.cols)} valeur(s) avec des cours ; sources (résultats, dirigeants, lettres, nouvelles)…")
    src = _sources(s, set(pn.cols), start, end, last)
    mic_cols: dict[str, list[int]] = defaultdict(list)
    sector_cols: dict[str, list[int]] = defaultdict(list)
    for j, isin in enumerate(pn.cols):
        mic_cols[insts[isin].mic or ""].append(j)
        sector = insts[isin].sector
        if sector:
            sector_cols[str(sector)].append(j)
    rp = _replay_context(config)
    c = Ctx(config, calendars, p, mp, rp, lss._ctx(config, StudyParams()), pn, src,
            lss.GroupCloses(pn.row, pn.col, pn.C), insts, groups_of,
            {k: np.array(v, dtype=np.int64) for k, v in mic_cols.items()},
            {k: np.array(v, dtype=np.int64) for k, v in sector_cols.items()})  # fmt: skip
    reg = registered_state(ledger)
    masked = {cause for cause, st in reg.items() if not st["execute"]}
    # 1. pools: moves, eligible control sessions, events (outcome-free)
    pool: dict[str, list[tuple[int, mc.Move, int]]] = {mc.RISES: [], mc.FALLS: []}
    skipped: Counter[str] = Counter()
    elig: dict[tuple[str, str, int], list[tuple[int, np.ndarray]]] = defaultdict(list)
    ev_counts: dict[str, Counter[str]] = defaultdict(Counter)
    ev_years: dict[str, Counter[int]] = defaultdict(Counter)
    ev_pool: dict[str, list[tuple[int, int]]] = defaultdict(list)
    codes_of: dict[int, dict[str, list[int]]] = {}
    spans: dict[int, tuple[int, int]] = {}
    n_values = 0
    for j, isin in enumerate(pn.cols):
        if j % 200 == 0:
            say(f"repérage : valeur {j + 1}/{len(pn.cols)}")
        v = _value(pn, j, p)
        n = len(v.bars)
        if n < MIN_BARS + 2:
            continue
        lo_k = max(MIN_BARS, bisect.bisect_left(v.days, start))
        hi_k = min(bisect.bisect_right(v.days, end) - 1, n - 2)
        if hi_k < lo_k:
            continue
        n_values += 1
        spans[j] = (lo_k, hi_k)
        for m in [*mc.find_rises(v.bars, mp), *mc.find_falls(v.bars, mp)]:
            if m.start < MIN_BARS or not (start <= v.days[m.start] <= end) or m.t0 + p.window_after >= n:
                continue
            if v.ca_in(m.t0 - p.comparability_before, max(m.end, m.t0 + p.window_after)):
                skipped[m.direction] += 1  # prices not comparable (as docs/34)
                continue
            pool[m.direction].append((j, m, v.days[m.t0].year))
        ks = np.arange(lo_k, hi_k + 1)
        bad = np.zeros(n, dtype=bool)
        for k in v.ca:
            bad[max(0, k - p.window_after) : min(n, k + p.comparability_before + 1)] = True
        ks = ks[~bad[ks]]
        vg, cap = _strata(c, isin)
        yrs = np.array([v.days[k].year for k in ks.tolist()], dtype=np.int64)
        for y in np.unique(yrs).tolist():
            elig[(vg, cap, int(y))].append((j, ks[yrs == y].astype(np.int32)))
        codes = _codes(v, insts[isin].mic or "", src, calendars, p, c.counts)
        codes_of[j] = codes
        for cause, es in _events_of(c, v, codes, lo_k, hi_k, ev_counts, ev_years).items():
            ev_pool[cause] += [(j, e) for e in es]
    say(f"{len(pool[mc.RISES])} hausse(s) et {len(pool[mc.FALLS])} baisse(s) repérée(s) sur {n_values} valeur(s)")
    # 2. draws (seeded, in a fixed order)
    sample = {g: rng.sample(pool[g], min(k, len(pool[g]))) for g, k in ((mc.RISES, rises), (mc.FALLS, falls))}
    strata = Counter((*_strata(c, pn.cols[j]), year) for j, _, year in sample[mc.RISES])
    alloc = mc.allocate(dict(sorted(strata.items())), controls)
    ctrl: list[tuple[int, int]] = []
    shortfall = 0
    for stratum, k_s in sorted(alloc.items()):
        cells = elig.get(stratum, [])
        sizes = [len(a) for _, a in cells]
        total = sum(sizes)
        cum = np.cumsum(sizes) if sizes else np.array([], dtype=np.int64)
        for x in sorted(rng.sample(range(total), min(k_s, total))):
            i = int(np.searchsorted(cum, x, side="right"))
            j, arr = cells[i]
            ctrl.append((j, int(arr[x - (int(cum[i - 1]) if i else 0)])))
        shortfall += max(0, k_s - total)
    ev_sample: dict[str, list[tuple[int, int]]] = {}
    for cause in sorted(ev_pool):
        if cause in masked:
            continue
        lst = ev_pool[cause]
        ev_sample[cause] = rng.sample(lst, p.events_max) if len(lst) > p.events_max else lst
    setup_pool = sorted(spans)
    setup_js = sorted(rng.sample(setup_pool, min(setup_values, len(setup_pool))))
    # 3. flags, outcomes
    need: dict[int, dict[str, list[Any]]] = defaultdict(lambda: defaultdict(list))
    for g in (mc.RISES, mc.FALLS):
        for j, m, _ in sample[g]:
            need[j][g].append(m)
    for j, k in ctrl:
        need[j][mc.CONTROLS].append(k)
    for cause, lst in ev_sample.items():
        for j, e in lst:
            need[j]["ev:" + cause].append(e)
    for j in setup_js:
        need[j]["setups"].append(True)
    units: list[mc.Unit] = []
    outs: dict[str, list[mc.Outcome]] = defaultdict(list)
    setup_counts: dict[str, Counter[str]] = defaultdict(Counter)
    setup_years: dict[str, Counter[int]] = defaultdict(Counter)
    setup_pool_ev: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for n_done, j in enumerate(sorted(need), 1):
        if n_done % 100 == 0:
            say(f"mesures : valeur {n_done}/{len(need)}")
        v = _value(pn, j, p)
        codes = codes_of[j]
        er = src.earnings.get(v.isin)
        eidx = EarningsIndex(v.days, er, p.earnings_margin_days) if er else None
        todo = need[j]
        for g in (mc.RISES, mc.FALLS):
            for m in todo.get(g, []):
                units.append(_unit(c, v, codes, eidx, m.t0, g, m))
        for k in todo.get(mc.CONTROLS, []):
            units.append(_unit(c, v, codes, eidx, k, mc.CONTROLS, None))
        for key, es in todo.items():
            if key.startswith("ev:"):
                outs[key[3:]] += [_outcome(c, v, e) for e in es]
        if todo.get("setups"):
            lo_k, hi_k = spans[j]
            for cause, es in _setup_events(c, v, lo_k, hi_k, setup_counts, setup_years).items():
                setup_pool_ev[cause] += [(j, e) for e in es]
    # detector setups: the sampled values' occurrences (seeded cap), measured in a last pass
    setup_need: dict[int, list[tuple[str, int]]] = defaultdict(list)
    for cause in sorted(setup_pool_ev):
        lst = setup_pool_ev[cause]
        for j, e in rng.sample(lst, p.events_max) if len(lst) > p.events_max else lst:
            setup_need[j].append((cause, e))
    for j in sorted(setup_need):
        v = _value(pn, j, p)
        for cause, e in setup_need[j]:
            outs[cause].append(_outcome(c, v, e))
    say(f"{len(units)} unité(s) mesurée(s) ; agrégation…")
    base = [u.outcome for u in units if u.group == mc.CONTROLS and u.outcome is not None]
    det_names = sorted(d for d in rp.det_params if d != "d8_buzz_precoce")
    policy = {d: "désactivé (ADR-014)" if d in rp.disabled else "surveillance seulement (ADR-013)"
              if d in rp.watch_only else "actif" for d in det_names}  # fmt: skip
    events: dict[str, Any] = {}
    all_counts = {**ev_counts, **setup_counts}
    all_years = {**ev_years, **setup_years}
    for cause in sorted(set(all_counts) | set(REGISTERED)):
        cnt = all_counts.get(cause, Counter())
        head = {"occurrences": cnt.get("occurrences", 0), "apres_delai": cnt.get("apres_delai", 0),
                "ecartees_operation_sur_titres": cnt.get("operation_sur_titres", 0),
                "ecartees_fin_des_donnees": cnt.get("fin_des_donnees", 0),
                "par_annee": {str(y): k for y, k in sorted(all_years.get(cause, Counter()).items())}}  # fmt: skip
        if cause in masked:
            events[cause] = {
                **head,
                "masque_registre": f"lu après l'exécution de l'enregistrement {reg[cause]['enregistrement']} (research run)",
            }
            continue
        o = outs.get(cause, [])
        events[cause] = {**head, "etudiees": len(o), **mc.event_summary(o, base, p.horizons, p)}
        if cause.startswith(mc.SETUP_PREFIX):
            events[cause]["politique"] = policy.get(cause[len(mc.SETUP_PREFIX) :], "?")
    rep: dict[str, Any] = {
        "meta": {
            "debut": start.isoformat(),
            "fin": end.isoformat(),
            "groupes": sorted(by_group),
            "sans_groupe": without,
            "valeurs": n_values,
            "graine": seed,
            "hausses_reperees": len(pool[mc.RISES]),
            "hausses_etudiees": len(sample[mc.RISES]),
            "baisses_reperees": len(pool[mc.FALLS]),
            "baisses_etudiees": len(sample[mc.FALLS]),
            "mouvements_ecartes_operation_sur_titres": dict(skipped),
            "controles_demandes": controls,
            "controles_tires": len(ctrl),
            "controles_manquants": shortfall,
            "controles_avec_issue": len(base),
            "valeurs_configurations": len(setup_js),
            "parametres": {**asdict(p), "horizons": list(p.horizons)},
            "parametres_mouvements": asdict(mp),
            "politique_detecteurs": policy,
            "registre": reg,
            "sources": src.meta,
            "placements_ecartes": dict(sorted(c.counts.items())),
            "fin_des_donnees": max((d for d in pn.days), default=None),
            "non_couvert": list(NOT_COVERED),
        },  # fmt: skip
        "couverture": _coverage(units, src, p),
        "attribution": mc.attribution(units, det_names, p),
        "decoupages": mc.breakdowns(units, p),
        "evenements": events,
    }
    rep["tests_multiples"] = mc.discoveries(
        {k: v for k, v in events.items() if "masque_registre" not in v}, p.horizons, p
    )
    rep["tests_multiples"]["comparaisons_partie_a"] = 3 * len(mc.CAUSES)
    return rep


def _coverage(units: Sequence[mc.Unit], src: Sources, p: mc.CauseParams) -> dict[str, Any]:
    """Per source and year of t0: share of the units (the three groups; moves only for market and sector) whose
    source covers the window, and the events loaded that year."""
    years = sorted({u.year for u in units})
    out: dict[str, Any] = {}
    for cause in (mc.EARNINGS, mc.INSIDER, mc.LETTER, mc.NEWS, mc.CORPORATE, mc.MARKET, mc.SECTOR, mc.GAP, mc.RVOL):
        per: dict[str, Any] = {}
        for y in years:
            us = [u for u in units if u.year == y and not (cause in mc.NO_CONTROL and u.group == mc.CONTROLS)]
            k = sum(1 for u in us if u.flags[cause].present is not None)
            per[str(y)] = share_stats(k, len(us), 1)
            loaded = src.loaded_by_year.get(cause)
            if loaded is not None:
                per[str(y)]["chargees"] = loaded.get(y, 0)
        out[cause] = per
    return out


# --- report -----------------------------------------------------------------------------------------------------


def _p(d: Mapping[str, Any] | None, ci: bool = True) -> str:
    if not d:
        return "—"
    if "sans_objet" in d:
        return "sans objet"
    if not d.get("n"):
        return "n 0"
    if "masque" in d:
        return f"masqué (n {d['n']})"
    s = f"{d['part'] * 100:.1f} %"
    if ci and d.get("ic95"):
        s += f" [{d['ic95'][0] * 100:.1f} ; {d['ic95'][1] * 100:.1f}]"
    return s + f" (n {d['n']})"


def _x(v: Any) -> str:
    return "—" if v is None else f"{v:.2f}"


MEASURE_FR = {"net_cto": "net CTO", "net_pea": "net PEA", "exces_place": "excès sur la place"}


def _ev_label(cause: str, x: Mapping[str, Any]) -> str:
    if cause.startswith(mc.SETUP_PREFIX):
        return f"configuration {cause[len(mc.SETUP_PREFIX) :]} ({x.get('politique', '?')})"
    return mc.EVENT_FR.get(cause, cause)


def _m(d: Mapping[str, Any] | None, pct: bool = True) -> str:
    if not d or not d.get("n"):
        return "—"
    if "masque" in d:
        return f"masqué (n {d['n']})"
    k, u = (100.0, " %") if pct else (1.0, "")
    s = f"{d['moyenne'] * k:+.2f}{u}"
    if d.get("ic95"):
        s += f" [{d['ic95'][0] * k:+.2f} ; {d['ic95'][1] * k:+.2f}]"
    if d.get("t") is not None:
        s += f" t {d['t']:+.1f}"
    if d.get("t_semaines") is not None:
        s += f" (sem. {d['t_semaines']:+.1f})"
    return s + f" n {d['n']}"


def render_md(rep: dict[str, Any]) -> str:
    m, att, ev = rep["meta"], rep["attribution"], rep["evenements"]
    par = m["parametres"]
    L: list[str] = []
    w = L.append
    w("# Causes des hausses — étape 1 : causes calculables (docs/36)")
    w("")
    w("> **Exploratoire.** Ce rapport ne change aucune règle et ne désigne aucun déclencheur : une piste ne devient une règle "
      "qu'au terme d'un plan pré-enregistré, testé sur des données jamais lues (docs/31). Agrégats seulement : aucun cours, "
      "aucun nom, aucun ISIN. Aucun modèle de langage n'est utilisé.")  # fmt: skip
    w("")
    w(
        f"- Période {m['debut']} → {m['fin']} · places européennes {', '.join(m['groupes'])} (New York, Londres, Zurich non lues : enregistrement n° 2) · {m['valeurs']} valeurs · graine {m['graine']}."
    )
    w(f"- **Hausses** : {m['hausses_etudiees']} tirées parmi {m['hausses_reperees']} (≥ {m['parametres_mouvements']['min_rise']:.0%} et ≥ {m['parametres_mouvements']['sigma_k']:g} σ·√durée, zigzag de docs/34). "
      f"**Baisses** : {m['baisses_etudiees']} parmi {m['baisses_reperees']} (le symétrique, même zigzag). **Contrôles** : {m['controles_tires']} séances tirées au hasard, "
      f"stratifiées comme les hausses (groupe de places, capitalisation, année) ; manquants {m['controles_manquants']}.")  # fmt: skip
    w(
        f"- t0 = première séance du mouvement (la séance après le creux ou le sommet ; pour un contrôle, la séance tirée). Fenêtre des causes : [t0 − {par['window_before']} ; t0 + {par['window_after']}]."
    )
    w("- Moments : **connue avant** (au plus tard à la clôture de t0 − 1), **avant l'ouverture de t0** (nuit précédente), **pendant** (de l'ouverture de t0 à la clôture de t0 + 1). "
      "Une source qui ne couvre pas une unité la laisse « inconnue », jamais « absente » (les parts sont calculées sur les unités couvertes).")  # fmt: skip
    w("- **Non couvert** : " + " ; ".join(m["non_couvert"]) + ".")
    w(f"- Tests multiples : {rep['tests_multiples']['cellules_lues']} moyennes et taux lus en partie B, {rep['tests_multiples']['comparaisons_partie_a']} comparaisons en partie A. "
      f"Une piste exige t ≥ {par['t_discovery']:g} (Harvey, Liu & Zhu 2016), sur le t des moyennes hebdomadaires ; même alors, c'est une hypothèse à pré-enregistrer, pas une découverte.")  # fmt: skip
    w("")
    w("## En bref")
    w("")
    sc, si = att["sans_cause"], att["sans_information_ni_marche"]
    w(
        f"- Hausses **sans aucune cause calculable** : {_p(sc['hausses'])} (baisses {_p(sc['baisses'], False)}, contrôles {_p(sc['controles'], False)})."
    )
    w(
        f"- Hausses **sans information ni mouvement de marché ou de secteur** (cible de l'étape 2, historique des nouvelles) : {_p(si['hausses'])}."
    )
    ranked = sorted(
        ((c, x) for c, x in att["causes"].items() if (x.get("lift_hausses") or 0) > 1),
        key=lambda kv: -kv[1]["lift_hausses"],
    )
    if ranked:
        w("- Causes plus fréquentes avant ou pendant une hausse **que lors d'une séance ordinaire** (lift > 1) : "
          + " ; ".join(f"{mc.CAUSE_FR[c]} × {_x(x['lift_hausses'])} (hausse/baisse {_x(x['ratio_hausses_baisses'])})" for c, x in ranked[:4]) + ".")  # fmt: skip
    flat = [
        mc.CAUSE_FR[c]
        for c, x in att["causes"].items()
        if x.get("ratio_hausses_baisses") is not None and 0.8 <= x["ratio_hausses_baisses"] <= 1.25
    ]
    if flat:
        w(
            "- Causes qui précèdent **autant les baisses que les hausses** (rapport entre 0,8 et 1,25 : n'expliquent pas une hausse) : "
            + ", ".join(flat)
            + "."
        )
    leads = rep["tests_multiples"]["pistes"]
    w(
        f"- Partie B : {len(leads)} cellule(s) atteignent |t hebdomadaire| ≥ {par['t_discovery']:g}"
        + (
            " — "
            + " ; ".join(
                f"{mc.EVENT_FR.get(x['cause'], x['cause'])}, {x['seances']} séances, {MEASURE_FR.get(x['mesure'], x['mesure'])} {x['moyenne'] * 100:+.2f} % (t hebdomadaire {x['t_semaines']:+.1f})"
                for x in leads[:6]
            )
            if leads
            else ""
        )
        + "."
    )
    w("")
    w("## Couverture des sources par année (part des unités couvertes ; publications chargées)")
    w("")
    cov = rep["couverture"]
    years = sorted({y for x in cov.values() for y in x})
    w("| Source | " + " | ".join(years) + " |")
    w("|---|" + "---|" * len(years))
    for cause, per in cov.items():
        cells = []
        for y in years:
            d = per.get(y) or {}
            s = "—" if not d.get("n") else f"{d['part'] * 100:.0f} %"
            if "chargees" in d:
                s += f" ({d['chargees']})"
            cells.append(s)
        w(f"| {mc.CAUSE_FR[cause]} | " + " | ".join(cells) + " |")
    w("")
    w(
        "Sources : "
        + "; ".join(f"{k} {v}" for k, v in m["sources"].items())
        + f" · placements écartés {m['placements_ecartes'] or 'aucun'}."
    )
    w("")
    w("## A. Attribution : quelle cause, à quel moment, et contre les baisses et les séances ordinaires")
    w("")
    w("Part des unités (couvertes par la source) où la cause est présente, IC 95 % de Wilson. Lift = part des hausses ÷ part des contrôles ; "
      "hausse/baisse = part des hausses ÷ part des baisses (≈ 1 : la cause ne distingue pas une hausse d'une baisse). "
      "Mouvement de marché et de secteur : sans objet pour un contrôle (aucun mouvement à expliquer).")  # fmt: skip
    w("")
    w("| Cause | Hausses | Baisses | Contrôles | Lift hausses | Lift baisses | Hausse/baisse |")
    w("|---|---|---|---|---|---|---|")
    for cause, x in att["causes"].items():
        w(
            f"| {mc.CAUSE_FR[cause]} | {_p(x['hausses'])} | {_p(x['baisses'])} | {_p(x['controles'])} | {_x(x['lift_hausses'])} | {_x(x['lift_baisses'])} | {_x(x['ratio_hausses_baisses'])} |"
        )
    for label, key in (
        ("aucune cause trouvée", "sans_cause"),
        ("sans information ni marché", "sans_information_ni_marche"),
    ):
        x = att[key]
        w(
            f"| **{label}** | {_p(x['hausses'])} | {_p(x['baisses'])} | {_p(x['controles'])} | {_x(x['lift_hausses'])} | {_x(x['lift_baisses'])} | {_x(x['ratio_hausses_baisses'])} |"
        )
    w("")
    w(
        "### Moment de la cause (part des unités couvertes) et part du mouvement encore capturable une fois la cause connue"
    )
    w("")
    w("Capturable : (clôture du sommet − ouverture qui suit la connaissance de la cause) ÷ amplitude du mouvement, moyenne ; 0 si la cause n'est connue qu'après le sommet. "
      "Un saut d'ouverture n'est achetable qu'à l'ouverture suivante.")  # fmt: skip
    w("")
    w("| Cause | Moment | Hausses | Baisses | Contrôles |")
    w("|---|---|---|---|---|")
    for cause, x in att["causes"].items():
        for t in mc.TIMINGS:
            row = x["moment"][t]
            if not any((row.get(g) or {}).get("k") for g in mc.GROUPS):
                continue
            w(
                f"| {mc.CAUSE_FR[cause]} | {mc.TIMING_FR[t]} | {_p(row.get('hausses'), False)} | {_p(row.get('baisses'), False)} | {_p(row.get('controles'), False) if 'controles' in row else 'sans objet'} |"
            )
        cp = x["part_capturable"]
        if cp["hausses"].get("n"):
            w(f"| {mc.CAUSE_FR[cause]} | part capturable | {_m(cp['hausses'])} | {_m(cp['baisses'])} | — |")
    w("")
    w(
        "### Cause principale (priorité fixe : information, puis marché, secteur, puis observables de cours et de volume)"
    )
    w("")
    w("| Cause principale | Hausses | Baisses | Contrôles |")
    w("|---|---|---|---|")
    for cause in mc.PRIMARY:
        w(
            f"| {mc.CAUSE_FR[cause]} | {_p(att['cause_principale']['hausses'][cause], False)} | {_p(att['cause_principale']['baisses'][cause], False)} | {_p(att['cause_principale']['controles'][cause], False)} |"
        )
    w("")
    w("### Configurations des détecteurs de production à t0 − 3 … t0 − 1 (politique actuelle rappelée)")
    w("")
    w("| Détecteur | Politique | Hausses | Baisses | Contrôles | Lift hausses | Hausse/baisse |")
    w("|---|---|---|---|---|---|---|")
    for d, x in att["par_detecteur"].items():
        w(
            f"| {d} | {m['politique_detecteurs'].get(d, '?')} | {_p(x['hausses'], False)} | {_p(x['baisses'], False)} | {_p(x['controles'], False)} | {_x(x['lift_hausses'])} | {_x(x['ratio_hausses_baisses'])} |"
        )
    g = att["sauts_haussiers_parmi_les_sauts"]
    ps = att["premieres_seances"]
    w("")
    w(f"Sauts d'ouverture haussiers parmi les sauts : hausses {_p(g['hausses'], False)}, baisses {_p(g['baisses'], False)}, contrôles {_p(g['controles'], False)}. "
      f"Premières séances (rendement moyen de la valeur / de la place équipondérée) : hausses {_m(ps['hausses']['rendement_valeur'])} / {_m(ps['hausses']['rendement_place'])} ; "
      f"baisses {_m(ps['baisses']['rendement_valeur'])} / {_m(ps['baisses']['rendement_place'])} ; contrôles {_m(ps['controles']['rendement_valeur'])} / {_m(ps['controles']['rendement_place'])}.")  # fmt: skip
    w("")
    w("### Découpages (part des hausses / des baisses / des contrôles où la cause est présente)")
    w("")
    w(
        f"Cellules en % des unités couvertes : hausses / baisses / contrôles ; « · » moins de {par['mask_below']} unités couvertes (masqué) ; « s.o. » sans objet."
    )
    for dim, per in rep["decoupages"].items():
        w("")
        w(f"**{dim}**")
        w("")
        w("| Strate | Unités H / B / C | " + " | ".join(mc.CAUSE_FR[c] for c in mc.CAUSES) + " | aucune cause |")
        w("|---|---|" + "---|" * (len(mc.CAUSES) + 1))
        for val, x in per.items():
            u = x["unites"]
            cells = []
            for cause in (*mc.CAUSES, mc.NO_CAUSE):
                cc = x["causes"][cause]
                cells.append(" / ".join(_short(cc.get(gr)) for gr in mc.GROUPS))
            w(f"| {val} | {u['hausses']} / {u['baisses']} / {u['controles']} | " + " | ".join(cells) + " |")
    w("")
    w("## B. Étude d'événements : ce qui restait à prendre une fois la cause connue")
    w("")
    w(f"Chaque occurrence de la cause dans le périmètre (une par valeur et par cause toutes les {par['refractory']} séances, la première), achetée à **l'ouverture qui suit sa connaissance** "
      f"(un saut d'ouverture : l'ouverture suivante), revendue à la clôture de la h-ième séance. Net : moins l'aller-retour complet (CTO comptant ; PEA quand la valeur est éligible), "
      f"taille au budget de risque avec un stop à {m['parametres_mouvements']['stop_atr']:g} ATR. Excès : moins la moitié liquide de la place, équipondérée. Barrière de docs/34 : "
      f"+max({m['parametres_mouvements']['min_rise']:.0%} ; {m['parametres_mouvements']['sigma_k']:g} σ·√{m['parametres_mouvements']['horizon']}) avant −{m['parametres_mouvements']['stop_atr']:g} ATR en {m['parametres_mouvements']['horizon']} séances, "
      f"contre le taux de base des contrôles. Au-delà de {par['events_max']} occurrences, tirage avec la graine ; configurations : {m['valeurs_configurations']} valeurs tirées, toutes leurs séances. "
      f"Moins de {par['mask_below']} observations : masqué. t entre parenthèses : t des moyennes hebdomadaires (les événements d'une même semaine ne sont pas indépendants).")  # fmt: skip
    w("")
    w(
        "| Événement | Occurrences (retenues / étudiées) | Séances | Net CTO | Net PEA | Excès sur la place | Contrôles net CTO |"
    )
    w("|---|---|---|---|---|---|---|")
    for cause, x in ev.items():
        label = _ev_label(cause, x)
        if "masque_registre" in x:
            w(f"| {label} | {x['occurrences']} | — | {x['masque_registre']} | | | |")
            continue
        kept = x["apres_delai"] - x["ecartees_operation_sur_titres"] - x["ecartees_fin_des_donnees"]
        for h in par["horizons"]:
            cell = x.get(str(h)) or {}
            w(
                f"| {label} | {kept} / {x['etudiees']} | {h} | {_m(cell.get('net_cto'))} | {_m(cell.get('net_pea'))} | {_m(cell.get('exces_place'))} | {_m(cell.get('controles_net_cto'))} |"
            )
    w("")
    w("| Événement | Barrière atteinte | Taux de base (contrôles) | Lift |")
    w("|---|---|---|---|")
    for cause, x in ev.items():
        if "masque_registre" in x:
            continue
        label = _ev_label(cause, x)
        w(f"| {label} | {_p(x['barriere'])} | {_p(x['taux_de_base'], False)} | {_x(x['lift_barriere'])} |")
    w("")
    w("Non mesurés en partie B : mouvement de marché et de secteur (ce ne sont pas des événements d'une valeur : leur équivalent achetable est l'indice), "
      "nouvelles stockées (couverture trop courte : étape 2), opérations sur titres et dividendes (mécaniques).")  # fmt: skip
    w("")
    w("## Lecture")
    w("")
    w("- Une cause n'explique une hausse que si elle est **plus fréquente avant les hausses qu'avant les baisses et les séances ordinaires** (lift > 1 et hausse/baisse > 1), "
      "et n'est **exploitable** que si ce qui restait à prendre **après qu'elle a été connue** est positif net de frais, au-delà du taux de base (partie B).")  # fmt: skip
    w("- La partie A sélectionne sur le résultat (on part des mouvements) : elle classe des hypothèses. La partie B ne regarde pas la suite pour choisir ses événements : "
      "c'est elle qui dit si une cause se joue. Ni l'une ni l'autre ne change une règle (docs/31) ; une piste devient un plan pré-enregistré, testé sur des données jamais lues.")  # fmt: skip
    w(
        "- Capitalisation et secteur d'aujourd'hui ; valeurs radiées absentes (biais de survie) ; cours non ajustés (fenêtres traversant une opération sur titres, ou un saut de cours suspect de × 4 ou ÷ 4 en une séance, écartées)."
    )
    return "\n".join(L) + "\n"


def _short(d: Mapping[str, Any] | None) -> str:
    if not d:
        return "—"
    if "sans_objet" in d:
        return "s.o."
    if not d.get("n") or "masque" in d:
        return "·"
    return f"{d['part'] * 100:.0f}"


def write(rep: dict[str, Any], out_dir: Path) -> tuple[Path, Path]:
    """``causes_<horodatage>.json`` and ``.md`` (French); refuses any price, name or ISIN in the report."""
    from app.domain.research.report import assert_no_price_fields

    assert_no_price_fields(rep)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    j, t = out_dir / f"causes_{stamp}.json", out_dir / f"causes_{stamp}.md"
    j.write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    t.write_text(render_md(rep), encoding="utf-8")
    return j, t
