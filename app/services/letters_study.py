# ruff: noqa: E501
"""docs/20 §20.11 — historical study of the letters' recommendations (user request of 29/10/2026), exploratory.

On the history stored on the PC, for each recommendation of a paid letter — a value whose ISIN is resolved, with
the direction « achat » or « à surveiller » (extraction at the reception, or an explicit opinion of the review),
**whose name or ISIN is actually written in the letter** (``mentions_recheck.written``: the former ticker-in-a-word
matches are left out) — placed on its reaction session with the venue's own sessions:

- forward returns at 1, 3, 5, 10, 20 sessions from the reaction open and from the first close after the reception:
  gross, net of the round-trip cost, abnormal (the venue's own index when stored, else the equal-weighted liquid
  half of the venue that day) and in excess of the controls; MFE / MAE in ATR;
- three entries × three exits (``app.domain.letters_study``) in net R at full cost (CTO cash, PEA when eligible);
- 20 controls per recommendation (same day, same venue, neighbouring volatility, seeded) with the same rules;
- breakdowns, and a « letters vs tool » table (a detector setup within ±3 sessions).

European venue groups only (P0 / P1 / P2: New York, London and Zurich stay unread until registration n° 2 has
run); no recommendation before 2017-01-02 is studied and no price of the sealed period (before 2015-10-01) is read.
The report holds aggregates only (no price, no name, no ISIN — ``assert_no_price_fields``); every run is written in
the research ledger. Prices are those stored (unadjusted, as the letters' levels); a window crossing a corporate
action is left out.
"""

from __future__ import annotations

import json
import logging
import random
import statistics
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import LoadedConfig
from app.db.models import Instrument, LetterOpinion, NewsletterItem, NewsletterValue, PriceEod
from app.domain import letters_study as ls
from app.domain.admission import AdmissionInput, Status, admit
from app.domain.backtest import simulate_trade
from app.domain.calendar import MarketCalendars, VenueCalendar
from app.domain.costs import CostParams, round_trip_fee_fraction
from app.domain.detectors import DETECTORS, DetectorContext
from app.domain.indicators import Bar, atr
from app.domain.zones import detect_zones

log = logging.getLogger("bourse.letters_study")
Progress = Callable[[str], None]
FIRST_DAY = date(2017, 1, 2)  # before: the European history is kept for the confirmations (docs/34 §34.5)
SEAL_END = date(2015, 9, 30)  # sealed period of the research (docs/31): never read
CA_JUMP = 0.2  # as the backtest: a jump of adj_close / close → corporate action
HOLDING_DAYS = {"days": 5, "swing_2_6w": 20, "semaines": 40}
LOOKBACK_DAYS = 420  # calendar days of history before the first letter (ATR, zones, RS rank)
FORWARD_DAYS = 60  # calendar days after the last letter (3 sessions of order + 20 of holding)
ACTIONS = {"achat": "buy", "surveiller": "watch"}
EXCLUSION_FR = {
    "valeur_non_resolue": "valeur sans ISIN résolu",
    "nom_absent_du_texte": "nom ni ISIN écrits dans la lettre (ancienne lecture d'un code dans un mot)",
    "valeur_absente_du_referentiel": "ISIN absent du référentiel",
    "pas_une_action": "pas une action (ETF, indice…)",
    "hors_perimetre_europeen": "place hors des groupes européens P0/P1/P2 (New York, Londres, Zurich : enregistrement n° 2)",
    "pas_de_cours": "aucun cours stocké pour la valeur",
    "historique_termine": "historique terminé avant la séance de réaction",
    "pas_de_cours_a_la_seance_de_reaction": "pas de cours de la valeur à la séance de réaction",
    "historique_insuffisant": "moins de 20 séances avant la lettre (ATR inconnu)",
    "doublon_meme_seance": "même source, même valeur, même séance de réaction (comptée une fois)",
}


# --- prices ---------------------------------------------------------------------------------------------------


def _rolling_mean(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        c = np.cumsum(np.insert(x, 0, 0.0))
        out[n - 1 :] = (c[n:] - c[:-n]) / n
    return out


@dataclass
class Series:
    """One value's own sessions (unadjusted prices), its corporate actions and its matching measures."""

    isin: str
    days: list[date]
    op: np.ndarray
    hi: np.ndarray
    lo: np.ndarray
    cl: np.ndarray
    vo: np.ndarray
    ca: set[int]
    pos: dict[date, int]
    vol: np.ndarray  # simple ATR14 ÷ close (control matching only)
    liq: np.ndarray  # mean traded value of the 20 sessions up to the session

    def bars(self, a: int, b: int) -> list[Bar]:
        a, b = max(0, a), min(len(self.days), b)
        return [
            Bar(float(self.op[i]), float(self.hi[i]), float(self.lo[i]), float(self.cl[i]), int(self.vo[i]))
            for i in range(a, b)
        ]

    def ca_between(self, a: int, b: int) -> bool:
        """A corporate action between the session ``a`` (excluded) and ``b`` (included)."""
        return any(a < k <= b for k in self.ca)


def _load_series(s: Session, isin: str, first: date, last: date) -> Series | None:
    rows = s.execute(
        select(
            PriceEod.day,
            PriceEod.open,
            PriceEod.high,
            PriceEod.low,
            PriceEod.close,
            PriceEod.adj_close,
            PriceEod.volume,
        )
        .where(PriceEod.isin == isin, PriceEod.day >= first, PriceEod.day <= last)
        .order_by(PriceEod.day)
    ).all()
    keep = [r for r in rows if r[0].weekday() < 5 and min(r[1], r[2], r[3], r[4]) > 0 and r[2] >= r[3]]
    if len(keep) < 30:
        return None
    days = [r[0] for r in keep]
    op = np.array([float(r[1]) for r in keep])
    hi = np.array([float(r[2]) for r in keep])
    lo = np.array([float(r[3]) for r in keep])
    cl = np.array([float(r[4]) for r in keep])
    vo = np.array([float(r[6] or 0) for r in keep])
    ca: set[int] = set()
    prev = None
    for k, r in enumerate(keep):
        f = (r[5] / r[4]) if r[5] and r[4] else None
        if f and prev and abs(f / prev - 1) > CA_JUMP:
            ca.add(k)
        prev = f or prev
    pc = np.r_[cl[0], cl[:-1]]
    tr = np.maximum(hi - lo, np.maximum(np.abs(hi - pc), np.abs(lo - pc)))
    return Series(isin, days, op, hi, lo, cl, vo, ca, {d: i for i, d in enumerate(days)},
                  _rolling_mean(tr, 14) / cl, _rolling_mean(cl * vo, 20))  # fmt: skip


@dataclass
class Venue:
    """The venue's sessions (its calendar where encoded, else the days its values traded) and its price matrices."""

    mic: str
    tz: ZoneInfo
    open_time: time
    close_time: time
    cal: VenueCalendar | None
    sessions: list[date]
    row: dict[date, int]
    series: dict[str, Series]
    isins: list[str]
    col: dict[str, int]
    O: np.ndarray  # noqa: E741
    C: np.ndarray
    VOL: np.ndarray
    LIQ: np.ndarray
    CA: np.ndarray
    index: Series | None = None
    ew_cache: dict[tuple[str, int, int, str], float | None] = field(default_factory=dict)

    def close_on(self, d: date) -> time:
        if self.cal is not None and d.year in self.cal.known_years:
            ses = self.cal.session(d)
            if ses.close_time is not None:
                return ses.close_time
        return self.close_time


def _venue(mic: str, cal: VenueCalendar | None, series: dict[str, Series]) -> Venue:
    cnt = Counter(d for se in series.values() for d in se.days)
    days: set[date] = set()
    if cnt:
        thr = max(1.0, 0.2 * statistics.median(cnt.values()))
        for d, k in cnt.items():
            known = cal is not None and d.year in cal.known_years
            if cal.is_open(d) if known and cal is not None else k >= thr:
                days.add(d)
        if cal is not None:  # an encoded session without any stored price stays a session (a hole, not a holiday)
            d, last = min(cnt), max(cnt)
            while d <= last:
                if d.year in cal.known_years and cal.is_open(d):
                    days.add(d)
                d += timedelta(days=1)
    sessions = sorted(days)
    row = {d: i for i, d in enumerate(sessions)}
    isins = sorted(series)
    shape = (len(sessions), len(isins))
    O, C, VOL, LIQ = (np.full(shape, np.nan) for _ in range(4))  # noqa: E741
    CA = np.zeros(shape, dtype=bool)
    for j, isin in enumerate(isins):
        se = series[isin]
        idx = np.array([row.get(d, -1) for d in se.days])
        m = idx >= 0
        O[idx[m], j], C[idx[m], j] = se.op[m], se.cl[m]
        VOL[idx[m], j], LIQ[idx[m], j] = se.vol[m], se.liq[m]
        for k in se.ca:
            if row.get(se.days[k]) is not None:
                CA[row[se.days[k]], j] = True
    tz = cal.tz if cal is not None else ZoneInfo("Europe/Paris")
    return Venue(mic, tz, cal.open_time if cal else time(9, 0), cal.close_time if cal else time(17, 30), cal, sessions,
                 row, series, isins, {k: j for j, k in enumerate(isins)}, O, C, VOL, LIQ, CA)  # fmt: skip


@dataclass
class GroupCloses:
    """Closes of the loaded venues of a group, for the relative-strength rank the detectors need (as the backtest:
    0.4·r12m + 0.2·r6m + 0.2·r3m + 0.2·r1m, percentile among the group's values that day)."""

    row: dict[date, int]
    col: dict[str, int]
    C: np.ndarray
    cache: dict[int, np.ndarray] = field(default_factory=dict)

    def rank(self, isin: str, d: date) -> float | None:
        t, j = self.row.get(d), self.col.get(isin)
        if t is None or j is None or t < 250:
            return None
        sc = self.cache.get(t)
        if sc is None:
            c = self.C
            with np.errstate(divide="ignore", invalid="ignore"):
                sc = (
                    0.4 * (c[t] / c[t - 250] - 1)
                    + 0.2 * (c[t] / c[t - 125] - 1)
                    + 0.2 * (c[t] / c[t - 63] - 1)
                    + 0.2 * (c[t] / c[t - 21] - 1)
                )
            self.cache[t] = sc
        x = sc[j]
        if not np.isfinite(x):
            return None
        valid = sc[np.isfinite(sc)]
        return float(((valid < x).sum() + ((valid == x).sum() + 1) / 2) / len(valid) * 100)


def _group_closes(venues: list[Venue]) -> GroupCloses:
    days = sorted({d for v in venues for d in v.sessions})
    row = {d: i for i, d in enumerate(days)}
    isins = [i for v in venues for i in v.isins]
    C = np.full((len(days), len(isins)), np.nan)
    j0 = 0
    for v in venues:
        rows = np.array([row[d] for d in v.sessions], dtype=int)
        if len(rows):
            C[rows, j0 : j0 + len(v.isins)] = v.C
        j0 += len(v.isins)
    return GroupCloses(row, {k: j for j, k in enumerate(isins)}, C)


# --- recommendations ------------------------------------------------------------------------------------------


@dataclass
class Reco:
    isin: str
    source: str
    direction: str
    origin: str  # extraction | revue
    levels: dict[str, Any] | None
    received: datetime
    item_id: int
    mic: str = ""
    group: str = ""
    r_venue: int = -1
    c_venue: int = -1
    day: date | None = None


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _collect(
    s: Session, start: date, end: date, sources: list[str] | None, excl: Counter[str], meta: dict[str, Any]
) -> list[Reco]:
    """Recommendations whose name or ISIN is written in the letter (extraction and review), 45 days before the
    period included (for « nouvelle / répétée »)."""
    from app.services.mentions_recheck import keys_by_isin, written

    lo = datetime.combine(start - timedelta(days=45), time(), UTC)
    hi = datetime.combine(end + timedelta(days=2), time(), UTC)
    q = select(NewsletterItem).where(NewsletterItem.received_at >= lo, NewsletterItem.received_at < hi)
    if sources:
        q = q.where(NewsletterItem.source.in_(sources))
    items = {i.id: i for i in s.scalars(q)}
    p_lo = datetime.combine(start, time(), UTC)
    p_hi = datetime.combine(end + timedelta(days=1), time(), UTC)
    meta["lettres_de_la_periode"] = sum(1 for i in items.values() if p_lo <= _aware(i.received_at) < p_hi)
    meta["lettres_par_source"] = dict(
        sorted(Counter(i.source for i in items.values() if p_lo <= _aware(i.received_at) < p_hi).items())
    )
    cands: list[Reco] = []
    neutral = 0
    for v in s.scalars(select(NewsletterValue).where(NewsletterValue.item_id.in_(list(items) or [-1]))):
        if v.direction not in ("buy", "watch"):
            neutral += 1
            continue
        if not v.isin:
            excl["valeur_non_resolue"] += 1
            continue
        it = items[v.item_id]
        cands.append(Reco(v.isin, it.source, v.direction, "extraction", v.levels, _aware(it.received_at), it.id))
    for op in s.scalars(
        select(LetterOpinion).where(
            LetterOpinion.day >= start - timedelta(days=45), LetterOpinion.day <= end + timedelta(days=2)
        )
    ):
        if not op.explicit or op.action not in ACTIONS:
            continue
        if not op.isin:
            excl["valeur_non_resolue"] += 1
            continue
        for iid in op.item_ids or []:
            it = items.get(iid) if isinstance(iid, int) else None
            if it is not None:
                cands.append(Reco(op.isin, it.source, ACTIONS[op.action], "revue", None, _aware(it.received_at), it.id))
    meta["mentions_neutres_ou_ventes"] = neutral
    keys = keys_by_isin(s, {c.isin for c in cands})
    seen: set[tuple[int, str]] = set()
    out: list[Reco] = []
    for c in sorted(cands, key=lambda c: (c.item_id, c.isin, c.origin != "extraction")):
        if (c.item_id, c.isin) in seen:
            continue  # the review repeats what the extraction read in the same letter
        seen.add((c.item_id, c.isin))
        it = items[c.item_id]
        if not written(f"{it.subject}\n{it.body_text}", c.isin, keys.get(c.isin, set())):
            excl["nom_absent_du_texte"] += 1
            continue
        out.append(c)
    meta["recommandations_lues"] = len(cands)
    return out


# --- context of the measures --------------------------------------------------------------------------------------


@dataclass
class Ctx:
    config: LoadedConfig
    p: ls.StudyParams
    capital: float
    risk_budget: float
    max_line: float
    slip: float
    wick: float
    stop_buffer: float
    zone_p: dict[str, Any]
    k_rr: float
    expiry: int
    min_price: float
    det_params: dict[str, dict[str, Any]]
    costs: dict[str, tuple[CostParams, CostParams | None]] = field(default_factory=dict)

    def frac(self, cp: CostParams | None, risk_fraction: float) -> float | None:
        """Round-trip cost fraction at the size the risk budget gives (capped by the line limit), as the backtest."""
        if cp is None:
            return None
        notional = min(self.max_line, self.risk_budget / max(1e-9, risk_fraction))
        return round_trip_fee_fraction(notional, cp)


def _ctx(config: LoadedConfig, p: ls.StudyParams) -> Ctx:
    pr = config.params
    capital = float(pr.capital.capital_pilote_eur)
    return Ctx(
        config=config, p=p, capital=capital, risk_budget=capital * float(pr.risk.max_risk_per_trade_pct),
        max_line=capital * float(pr.risk.max_position_pct_of_capital), slip=float(pr.risk.slippage_pct),
        wick=float(getattr(pr.risk.stop, "wick_buffer_pct", 0.005) or 0.005), stop_buffer=float(pr.risk.stop.buffer_atr_mult),
        zone_p=dict(getattr(pr, "zones", None) or {}), k_rr=float((pr.admission or {}).get("min_reward_risk", 2.0)),
        expiry=int((getattr(pr, "entry", None) or {}).get("expiry_sessions", 3)),
        min_price=float((pr.universe or {}).get("min_price", 1.0)),
        det_params={k: dict(v) for k, v in (pr.detectors or {}).items() if k in DETECTORS and isinstance(v, dict) and k != "d8_buzz_precoce"},
    )  # fmt: skip


def _costs(ctx: Ctx, inst: Instrument) -> tuple[CostParams, CostParams | None]:
    from app.services.moves import _costs as costs_of

    if inst.isin not in ctx.costs:
        ctx.costs[inst.isin] = costs_of(ctx.config, inst)
    return ctx.costs[inst.isin]


@dataclass
class Measured:
    fwd: dict[str, dict[int, float | None]]
    mfe: float | None
    mae: float | None
    arms: dict[str, tuple[str, float | None, float | None]]  # arm → (status, net R CTO, net R PEA)


def _measure(
    ctx: Ctx,
    se: Series,
    r: int,
    c: int | None,
    window: list[Bar],
    atr_v: float,
    ref_close: float,
    lv: ls.LetterLevels,
    costs: tuple[CostParams, CostParams | None],
    arms: set[str] | None = None,
) -> Measured:
    """Forward returns, excursions and the entry × exit arms of one value from its reaction session ``r`` (``c``:
    first close after the reception; ``window``: the bars up to the reference close, the last one known before the
    reaction open). ``arms``: the arms to simulate (the recommended value's applicable ones for a control)."""
    p = ctx.p
    horizon = max(max(p.horizons), p.excursion_sessions, p.entry_valid_sessions + p.max_hold) + 2
    a = min(r, c if c is not None else r) - 1 - p.pre_move_sessions
    seg = se.bars(a, r + horizon)
    off = max(0, a)
    rs = r - off
    fwd: dict[str, dict[int, float | None]] = {"ouverture": {}, "cloture": {}}
    for h in p.horizons:
        fwd["ouverture"][h] = None if se.ca_between(r, r + h - 1) else ls.forward_from_open(seg, rs, h)
        fwd["cloture"][h] = None if c is None or se.ca_between(c, c + h) else ls.forward_from_close(seg, c - off, h)
    exc = (
        None
        if se.ca_between(r, r + p.excursion_sessions - 1)
        else ls.excursions_atr(seg, rs, p.excursion_sessions, atr_v)
    )
    cto, pea = costs
    zones: list[Any] | None = None

    def r1(planned: float) -> float | None:
        nonlocal zones
        if zones is None:
            zp = ctx.zone_p
            zones = detect_zones(window[-int(zp.get("lookback_sessions", 250)) :], atr_v, int(zp.get("pivot_window", 5)),
                                 float(zp.get("merge_atr_mult", 0.5)), int(zp.get("min_touches", 2)))  # fmt: skip
        return ls.structure_stop(window, planned, atr_v, zones, ctx.wick, ctx.stop_buffer, p.structure_lookback)

    entries: dict[str, tuple[ls.EntryRule, float] | None] = {
        "E1": (ls.EntryRule("open"), ref_close),
        "E2": (
            ls.EntryRule("buy_stop", lv.trigger, lv.trigger + p.buy_stop_cap_atr * atr_v, p.entry_valid_sessions),
            lv.trigger,
        )
        if lv.trigger
        else None,
        "E3": (ls.EntryRule("limit", lv.buy, None, p.entry_valid_sessions), lv.buy) if lv.buy else None,
    }
    out: dict[str, tuple[str, float | None, float | None]] = {}
    after = seg[rs:]
    for e, spec in entries.items():
        for x in ls.EXITS:
            arm = f"{e}_{x}"
            if arms is not None and arm not in arms:
                continue
            if spec is None:
                out[arm] = (ls.NOT_APPLICABLE, None, None)
                continue
            rule, planned = spec
            ex: ls.ExitRule | None
            if x == "X1":
                ex = ls.ExitRule(stop=lv.stop, target=lv.target, max_hold=p.max_hold) if lv.stop and lv.target else None
            elif x == "X2":
                if not lv.target:
                    ex = None
                else:
                    st = r1(planned)
                    if st is None:
                        out[arm] = (ls.INCOHERENT, None, None)
                        continue
                    ex = ls.ExitRule(stop=st, target=lv.target, max_hold=p.max_hold)
            else:
                ex = ls.ExitRule(stop_atr=p.atr_stop, target_atr=p.atr_target, atr=atr_v, max_hold=p.max_hold)
            if ex is None:
                out[arm] = (ls.NOT_APPLICABLE, None, None)
                continue
            res = ls.simulate(after, rule, ex, ctx.slip)
            if isinstance(res, str):
                out[arm] = (res, None, None)
            elif se.ca_between(r + res.entry_index, r + res.exit_index):
                out[arm] = (ls.CORPORATE_ACTION, None, None)
            else:
                f_cto, f_pea = ctx.frac(cto, res.risk_fraction), ctx.frac(pea, res.risk_fraction)
                out[arm] = (ls.EXECUTED, res.net_r(f_cto or 0.0), res.net_r(f_pea) if f_pea is not None else None)
    return Measured(fwd, exc[0] if exc else None, exc[1] if exc else None, out)


def _transposed(lv: ls.LetterLevels, ref: float, atr_v: float, ref_c: float, atr_c: float) -> ls.LetterLevels:
    t = ls.transpose
    return ls.LetterLevels(t(lv.trigger, ref, atr_v, ref_c, atr_c), t(lv.buy, ref, atr_v, ref_c, atr_c),
                           t(lv.stop, ref, atr_v, ref_c, atr_c), t(lv.target, ref, atr_v, ref_c, atr_c), lv.kind)  # fmt: skip


def _bench(venue: Venue, base: str, d0: date, d1: date, exclude: str, kinds: Counter[str]) -> float | None:
    """Return of the venue's index over the same window when stored (an open that is not inside its session's range
    is not trusted), else of the equal-weighted liquid half of the venue (values with no corporate action inside)."""
    idx = venue.index
    if idx is not None:
        a, b = idx.pos.get(d0), idx.pos.get(d1)
        if a is not None and b is not None and b >= a:
            if base == "cloture":
                kinds["indice"] += 1
                return float(idx.cl[b] / idx.cl[a] - 1)
            if idx.lo[a] <= idx.op[a] <= idx.hi[a]:
                kinds["indice"] += 1
                return float(idx.cl[b] / idx.op[a] - 1)
    a2, b2 = venue.row.get(d0), venue.row.get(d1)
    if a2 is None or b2 is None or b2 < a2 or a2 == 0:
        kinds["indisponible"] += 1
        return None
    key = (base, a2, b2, exclude)
    if key not in venue.ew_cache:
        p0 = venue.O[a2] if base == "ouverture" else venue.C[a2]
        p1, liq = venue.C[b2], venue.LIQ[a2 - 1]
        ok = np.isfinite(p0) & np.isfinite(p1) & (p0 > 0) & np.isfinite(liq)
        if b2 > a2:
            ok &= ~venue.CA[a2 + 1 : b2 + 1].any(axis=0)
        j = venue.col.get(exclude)
        if j is not None:
            ok[j] = False
        val = None
        if ok.sum() >= 5:
            sel = ok & (liq >= np.median(liq[ok]))
            val = float(np.mean(p1[sel] / p0[sel] - 1))
        venue.ew_cache[key] = val
    val = venue.ew_cache[key]
    kinds["equipondere" if val is not None else "indisponible"] += 1
    return val


def _tool(ctx: Ctx, se: Series, r: int, gc: GroupCloses | None, cto: CostParams) -> tuple[list[str], float | None]:
    """Detectors (production parameters, d8 excepted: it needs the forum radar of the day) that had a setup at the
    closes from ``tool_window`` sessions before the reference close to ``tool_window`` after it, and the net R (CTO)
    of the tool's own first trade, simulated as the backtest does (admission at the maximum price, limit order)."""
    names: list[str] = []
    net: float | None = None
    w = ctx.p.tool_window
    for k in range(r - 1 - w, r - 1 + w + 1):
        if k < 259 or k >= len(se.days) - 1:
            continue
        window = se.bars(k - 279, k + 1)
        rs = gc.rank(se.isin, se.days[k]) if gc is not None else None
        base_ctx = DetectorContext(rs, [], ctx.wick, ctx.stop_buffer)
        zones = None
        for name, dp in ctx.det_params.items():
            if not DETECTORS[name](dp).detect(window, base_ctx):
                continue
            if zones is None:
                zp = ctx.zone_p
                zones = detect_zones(window[-int(zp.get("lookback_sessions", 250)) :], atr(window, 14) or 0.0,
                                     int(zp.get("pivot_window", 5)), float(zp.get("merge_atr_mult", 0.5)), int(zp.get("min_touches", 2)))  # fmt: skip
            setups = DETECTORS[name](dp).detect(window, DetectorContext(rs, zones, ctx.wick, ctx.stop_buffer))
            if setups and name not in names:
                names.append(name)
            for st in setups:
                if net is not None or not st.targets or st.p_max_technical <= st.stop:
                    continue
                target = st.targets[0]
                notional = min(
                    ctx.max_line, ctx.risk_budget / max(1e-9, (st.p_max_technical - st.stop) / st.p_max_technical)
                )
                frac = round_trip_fee_fraction(notional, cto)
                adm = admit(
                    AdmissionInput(
                        window[-1].close, target, st.stop, st.kind, st.level_low, frac, ctx.k_rr, st.p_max_technical
                    )
                )
                if adm.status in (Status.DEGRADED, Status.NOT_TESTABLE) or adm.p_max is None:
                    continue
                hold = int(dp.get("time_stop_sessions") or HOLDING_DAYS.get(st.horizon, 20))
                res = simulate_trade(
                    se.bars(k + 1, k + 1 + ctx.expiry + hold + 2),
                    adm.p_max,
                    st.stop,
                    target,
                    ctx.slip,
                    ctx.expiry,
                    hold,
                )
                if res is not None and not se.ca_between(k + 1 + res.entry_index, k + 1 + res.exit_index):
                    net = res.net_r(frac)
    return names, net


# --- study --------------------------------------------------------------------------------------------------------


def _index_ids(config: LoadedConfig) -> set[str]:
    reg = getattr(config.params, "regime", None) or {}
    ids = set((reg.get("index_isins") or {}).values())
    ids |= {str(v.get("id")) for v in (reg.get("indices_by_mic") or {}).values() if isinstance(v, dict)}
    ids.add(str((reg.get("europe_index") or {}).get("id")))
    return ids


def build_rows(
    s: Session,
    config: LoadedConfig,
    calendars: MarketCalendars,
    start: date,
    end: date,
    sources: list[str] | None = None,
    params: ls.StudyParams | None = None,
    progress: Progress | None = None,
    today: date | None = None,
) -> tuple[list[ls.RecRow], dict[str, Any]]:
    """The measured recommendations of the period (one ``RecRow`` each) and the counts of the report header."""
    from app.services.indices import index_specs
    from app.services.research import mic_groups

    p = params or ls.StudyParams()
    say = progress or (lambda m: None)
    ctx = _ctx(config, p)
    groups_of = mic_groups(config, calendars)  # P0 / P1 / P2 only
    excl: Counter[str] = Counter()
    meta: dict[str, Any] = {}
    cands = _collect(s, start, end, sources, excl, meta)
    insts = {
        i.isin: i for i in s.scalars(select(Instrument).where(Instrument.isin.in_({c.isin for c in cands} or {""})))
    }
    idx_ids = _index_ids(config)
    kept: list[Reco] = []
    for c in cands:
        inst = insts.get(c.isin)
        if inst is None:
            excl["valeur_absente_du_referentiel"] += 1
        elif (inst.kind or "stock") != "stock" or c.isin in idx_ids:
            excl["pas_une_action"] += 1
        elif inst.mic not in groups_of:
            excl["hors_perimetre_europeen"] += 1
        else:
            c.mic, c.group = inst.mic, groups_of[inst.mic]
            kept.append(c)
    today = today or date.today()
    venues: dict[str, Venue] = {}
    if kept:
        first = max(SEAL_END + timedelta(days=1), min(c.received.date() for c in kept) - timedelta(days=LOOKBACK_DAYS))
        last = min(today, max(c.received.date() for c in kept) + timedelta(days=FORWARD_DAYS))
        specs = {x.market: x.id for x in index_specs(config)}
        for mic in sorted({c.mic for c in kept}):
            peers = [
                i.isin for i in s.scalars(select(Instrument).where(Instrument.mic == mic).order_by(Instrument.isin))
                if (i.kind or "stock") == "stock" and i.isin not in idx_ids and not i.isin.startswith("IDX.")
            ]  # fmt: skip
            say(f"place {mic} : chargement des cours de {len(peers)} valeur(s)…")
            series = {i: se for i in peers if (se := _load_series(s, i, first, last)) is not None}
            v = _venue(mic, calendars.venues.get(mic), series)
            if specs.get(mic):
                v.index = _load_series(s, specs[mic], first, last)
            venues[mic] = v
    groups: dict[str, GroupCloses] = {}
    for g in sorted({c.group for c in kept}):
        groups[g] = _group_closes([v for m, v in venues.items() if groups_of.get(m) == g])
    # placement on the reaction session of the value's own venue
    placed: list[Reco] = []
    for c in sorted(kept, key=lambda c: (c.received, c.item_id, c.isin)):
        v = venues[c.mic]
        local = c.received.astimezone(v.tz)
        rc = ls.reaction_sessions(local, v.open_time, v.close_on(local.date()), v.sessions)
        if rc is None:
            if start <= local.date() <= end:
                excl["historique_termine"] += 1
            continue
        c.r_venue, c.c_venue = rc
        c.day = v.sessions[rc[0]]
        placed.append(c)
    seen: set[tuple[str, str, date]] = set()
    unique: list[Reco] = []
    for c in placed:
        key = (c.source, c.isin, c.day or date.min)
        if key in seen:
            if c.day is not None and start <= c.day <= end:
                excl["doublon_meme_seance"] += 1
            continue
        seen.add(key)
        unique.append(c)
    history: dict[tuple[str, str], list[int]] = defaultdict(list)
    treated: dict[tuple[str, date], set[str]] = defaultdict(set)
    for c in unique:
        treated[(c.mic, c.day or date.min)].add(c.isin)
    todo = [c for c in unique if c.day is not None and start <= c.day <= end]
    kinds: Counter[str] = Counter()
    ctrl_meta = {"tires": 0, "elargis": 0, "manquants": 0}
    dropped_levels = 0
    rows: list[ls.RecRow] = []
    for n_done, c in enumerate(unique, 1):
        prev = history[(c.source, c.isin)]
        nov = ls.novelty(prev, c.r_venue, p.novelty_sessions)
        prev.append(c.r_venue)
        if c.day is None or not (start <= c.day <= end):
            continue  # before the period: history of « nouvelle / répétée » only
        if n_done % 25 == 0:
            say(f"recommandations : {n_done}/{len(unique)}")
        v = venues[c.mic]
        se = v.series.get(c.isin)
        if se is None:
            excl["pas_de_cours"] += 1
            continue
        r = se.pos.get(c.day)
        if r is None:
            excl["pas_de_cours_a_la_seance_de_reaction"] += 1
            continue
        cpos = se.pos.get(v.sessions[c.c_venue])
        ref = r - 1
        window = se.bars(ref - 279, ref + 1)
        atr_v = ls.atr_at(window, len(window) - 1) if ref >= 20 else None
        if atr_v is None or atr_v <= 0:
            excl["historique_insuffisant"] += 1
            continue
        ref_close = float(se.cl[ref])
        lv, dropped = ls.letter_levels(c.levels, ref_close, p.level_band)
        dropped_levels += len(dropped)
        costs = _costs(ctx, insts[c.isin])
        m = _measure(ctx, se, r, cpos, window, atr_v, ref_close, lv, costs)
        intraday = c.r_venue == c.c_venue + 1  # received during the session: its open is the last price known
        pm = None
        if cpos is not None:
            a0 = max(0, cpos - 1 - p.pre_move_sessions)
            pm = ls.pre_move(se.bars(a0, cpos + 1), cpos - a0, intraday, p.pre_move_sessions)
        bench: dict[str, dict[int, float | None]] = {"ouverture": {}, "cloture": {}}
        for h in p.horizons:
            j = r + h - 1
            bench["ouverture"][h] = (
                _bench(v, "ouverture", c.day, se.days[j], c.isin, kinds) if j < len(se.days) else None
            )
            jc = (cpos + h) if cpos is not None else None
            bench["cloture"][h] = (
                _bench(v, "cloture", se.days[cpos], se.days[jc], c.isin, kinds)
                if cpos is not None and jc is not None and jc < len(se.days)
                else None
            )
        row = ls.RecRow(
            isin=c.isin, day=c.day, source=c.source, direction=c.direction, origin=c.origin, levels=lv.kind,
            group=c.group, year=c.day.year, pre_up=None if pm is None else pm > p.pre_move_up, novelty=nov,
            fwd=m.fwd, bench=bench, ctrl_fwd={"ouverture": defaultdict(list), "cloture": defaultdict(list)},
            cost=ctx.frac(costs[0], p.atr_stop * atr_v / ref_close), mfe=m.mfe, mae=m.mae,
            arms={k: ls.ArmOutcome(st, net, pea) for k, (st, net, pea) in m.arms.items()},
        )  # fmt: skip
        # controls: same day, same venue, neighbouring volatility, seeded
        rr = v.row.get(c.day)
        keys: list[str] = []
        if rr is not None and rr > 0:
            pr = rr - 1
            ok = (
                np.isfinite(v.C[pr])
                & np.isfinite(v.O[rr])
                & np.isfinite(v.VOL[pr])
                & np.isfinite(v.LIQ[pr])
                & (v.C[pr] >= ctx.min_price)
            )
            skip = treated[(c.mic, c.day)] | {c.isin}
            cand = [
                ls.Candidate(v.isins[j], float(v.VOL[pr, j]), float(v.LIQ[pr, j]))
                for j in np.flatnonzero(ok)
                if v.isins[j] not in skip
            ]
            rng = random.Random(f"{p.seed}|{c.isin}|{c.day.isoformat()}|{c.source}")
            vol_v, liq_v = float(se.vol[ref]), float(se.liq[ref])
            if np.isfinite(vol_v) and vol_v > 0:
                keys, widened = ls.pick_controls(
                    cand, vol_v, liq_v if np.isfinite(liq_v) else None, p.controls, rng, p.vol_band, p.liquidity_share
                )
                ctrl_meta["elargis"] += int(widened)
        ctrl_meta["tires"] += len(keys)
        ctrl_meta["manquants"] += p.controls - len(keys)
        applicable = {k for k, o in row.arms.items() if o.status != ls.NOT_APPLICABLE}
        for key in keys:
            cs = v.series[key]
            rc_ = cs.pos[c.day]
            refc = rc_ - 1
            win_c = cs.bars(refc - 279, refc + 1)
            atr_c = ls.atr_at(win_c, len(win_c) - 1) if refc >= 14 else None
            if atr_c is None or atr_c <= 0:
                continue
            ref_c = float(cs.cl[refc])
            cc = cs.pos.get(v.sessions[c.c_venue])
            mc = _measure(
                ctx,
                cs,
                rc_,
                cc,
                win_c,
                atr_c,
                ref_c,
                _transposed(lv, ref_close, atr_v, ref_c, atr_c),
                _costs(ctx, _inst(s, insts, key)),
                applicable,
            )
            for base in ls.BASES:
                for h, x in mc.fwd[base].items():
                    if x is not None:
                        row.ctrl_fwd[base][h].append(x)
            if mc.mfe is not None and mc.mae is not None:
                row.ctrl_mfe.append(mc.mfe)
                row.ctrl_mae.append(mc.mae)
            for arm, (st, net, _) in mc.arms.items():
                if st == ls.NOT_APPLICABLE:
                    continue
                row.arms[arm].controls_tried += 1
                if st == ls.EXECUTED and net is not None:
                    row.arms[arm].controls_cto.append(net)
        row.ctrl_fwd = {b: dict(x) for b, x in row.ctrl_fwd.items()}
        row.tool, row.tool_net = _tool(ctx, se, r, groups.get(c.group), costs[0])
        rows.append(row)
    say(f"{len(rows)} recommandation(s) mesurée(s)")
    meta.update({
        "recommandations_retenues": len(rows),
        "recommandations_placees": len(todo),
        "exclusions": dict(sorted(excl.items())),
        "niveaux_ecartes": dropped_levels,
        "places": {m: len(v.series) for m, v in sorted(venues.items())},
        "indices_des_places": sorted(m for m, v in venues.items() if v.index is not None),
        "references": dict(sorted(kinds.items())),
        "controles": ctrl_meta,
        "fin_des_donnees": max((se.days[-1] for v in venues.values() for se in v.series.values()), default=None),
    })  # fmt: skip
    return rows, meta


def _inst(s: Session, cache: dict[str, Instrument], isin: str) -> Instrument:
    if isin not in cache:
        inst = s.get(Instrument, isin)
        if inst is None:  # pragma: no cover — a value with prices always has its referential line
            raise KeyError(isin)
        cache[isin] = inst
    return cache[isin]


def run_study(
    s: Session,
    config: LoadedConfig,
    calendars: MarketCalendars,
    start: date,
    end: date,
    sources: list[str] | None = None,
    params: ls.StudyParams | None = None,
    progress: Progress | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    p = params or ls.StudyParams()
    rows, meta = build_rows(s, config, calendars, start, end, sources, p, progress, today)
    pol_dis = {
        k for k, v in (config.params.detectors or {}).items() if isinstance(v, dict) and not v.get("enabled", False)
    }
    watch = set((config.params.scoring or {}).get("watch_only_detectors") or [])
    meta.update({
        "debut": start.isoformat(), "fin": end.isoformat(), "sources": sources or "toutes", "graine": p.seed,
        "parametres": asdict(p),
        "seuils": {"recommandations_min": p.min_recommendations, "trades_min_par_bras": p.min_trades,
                   "masque_sous": p.mask_below},
        "politique_detecteurs": {d: "désactivé (ADR-014)" if d in pol_dis else "surveillance seulement (ADR-013)" if d in watch else "actif"
                                 for d in sorted(_ctx(config, p).det_params)},
        "couts": "aller-retour complet comme le backtest : courtage, TTF (capitalisation actuelle), taxes étrangères, change ; CTO comptant Saxo, PEA BoursoBank si éligible ; taille = budget de risque ÷ distance au stop glissé, plafonnée à la ligne maximale",
    })  # fmt: skip
    return {
        "meta": meta,
        "ensemble": ls.summarize(rows, p),
        "decoupages": ls.breakdowns(rows, p),
        "lettres_et_outil": ls.letters_vs_tool(rows, p),
    }


# --- report ---------------------------------------------------------------------------------------------------------


def _pct(d: dict[str, Any] | None, key: str = "moyenne") -> str:
    if not d or not d.get("n"):
        return "—"
    if "masque" in d:
        return f"masqué (n {d['n']})"
    x = d.get(key if key in d else "part")
    return "—" if x is None else f"{x * 100:+.2f} %"


def _share(d: dict[str, Any] | None) -> str:
    if not d or not d.get("n"):
        return "—"
    if "masque" in d:
        return f"masqué (n {d['n']})"
    lo, hi = d["ic95"]
    return f"{d['part'] * 100:.0f} % [{lo * 100:.0f} ; {hi * 100:.0f}]"


def _r(d: dict[str, Any] | None, pct: bool = False) -> str:
    """Mean with its interval and t (net R, or a return in % when ``pct``)."""
    if not d or not d.get("n"):
        return "—"
    if "masque" in d:
        return f"masqué (n {d['n']})"
    k = 100.0 if pct else 1.0
    u = " %" if pct else ""
    s = f"{d['moyenne'] * k:+.2f}{u}"
    if d.get("ic95"):
        s += f" [{d['ic95'][0] * k:+.2f} ; {d['ic95'][1] * k:+.2f}]"
    if d.get("t") is not None:
        s += f" t {d['t']:+.1f}"
    return s + f" (n {d['n']})"


def _t(d: dict[str, Any] | None) -> str:
    return "—" if not d or d.get("t") is None else f"{d['t']:+.1f}"


def render_md(rep: dict[str, Any]) -> str:
    m, ens = rep["meta"], rep["ensemble"]
    par = m["parametres"]
    n = ens["n"]
    L: list[str] = []
    w = L.append
    w("# Étude historique des recommandations des lettres (docs/20 §20.11)")
    w("")
    w("> **Exploratoire.** Ce rapport ne change aucune règle : une règle d'entrée ou de sortie n'est adoptée qu'au terme "
      "d'un plan pré-enregistré, testé sur des données jamais lues (docs/31). Agrégats seulement : aucun cours, aucun nom, aucun ISIN.")  # fmt: skip
    w("")
    w("## En bref")
    w("")
    src = m["sources"] if isinstance(m["sources"], str) else ", ".join(m["sources"])
    w(
        f"- Période {m['debut']} → {m['fin']} (séance de réaction) · sources : {src} · {m.get('lettres_de_la_periode', 0)} lettre(s) reçue(s) dans la période."
    )
    w(f"- **{n} recommandation(s) retenue(s)** (achat ou à surveiller, ISIN résolu, nom ou ISIN écrit dans la lettre, places européennes P0/P1/P2) ; "
      f"{m['exclusions'].get('nom_absent_du_texte', 0)} ancienne(s) lecture(s) écartée(s) : le nom n'est pas dans le texte.")  # fmt: skip
    seuil = m["seuils"]
    if n < seuil["recommandations_min"]:
        w(f"- **Échantillon insuffisant : {n} recommandation(s) pour {seuil['recommandations_min']} exigées avant toute conclusion.** "
          "Les chiffres ci-dessous décrivent l'historique ; ils ne permettent pas de dire si les lettres ont un avantage.")  # fmt: skip
    else:
        w(
            f"- Seuil de {seuil['recommandations_min']} recommandations atteint ; chaque bras exige en plus {seuil['trades_min_par_bras']} trades exécutés."
        )
    r20 = ens["rendements"]["ouverture"].get("20") or {}
    r5 = ens["rendements"]["ouverture"].get("5") or {}
    w(
        f"- Après l'ouverture de la séance de réaction, rendement **anormal** moyen : 5 séances {_r(r5.get('anormal'), True)} ; 20 séances {_r(r20.get('anormal'), True)}."
    )
    w(
        f"- Excès sur les contrôles (même jour, même place, volatilité voisine) à 20 séances : {_r(r20.get('exces_controles'), True)} ; net de frais, part des recommandations positives : {_share(r20.get('part_positive_nette'))}."
    )
    arms = ens["bras"]
    ok = [(k, a) for k, a in arms.items() if a["seuils_atteints"] and a["exces_apparie"].get("t") is not None]
    if ok:
        best = max(ok, key=lambda kv: kv[1]["exces_apparie"]["t"])
        w(
            f"- Bras au t le plus élevé contre les contrôles (descriptif, à ne pas retenir sans plan) : {best[0]} — excès apparié {_r(best[1]['exces_apparie'])}."
        )
    else:
        w(
            f"- Aucun bras n'atteint {seuil['trades_min_par_bras']} trades exécutés avec un excès apparié mesurable : pas de règle d'entrée ou de sortie lisible."
        )
    w("- Repère : une découverte exige t ≥ 3 (Harvey, Liu & Zhu), car de nombreux découpages sont lus ici.")
    w("")
    w("## 1. Données et exclusions")
    w("")
    w(
        f"- Recommandations lues : {m.get('recommandations_lues', 0)} · mentions neutres ou avis de vente (non comptés) : {m.get('mentions_neutres_ou_ventes', 0)} · niveaux écartés (nom inconnu ou incompatible avec le cours) : {m.get('niveaux_ecartes', 0)}."
    )
    w(
        f"- Lettres par source : {m.get('lettres_par_source') or '—'} · places lues (valeurs avec cours) : {m.get('places') or '—'} · indice de place en base : {', '.join(m.get('indices_des_places') or []) or 'aucun'}."
    )
    w(
        f"- Références des rendements anormaux (fenêtres) : {m.get('references') or '—'} · contrôles tirés {m['controles']['tires']}, élargis (volatilité la plus proche) {m['controles']['elargis']}, manquants {m['controles']['manquants']} · dernier cours lu {m.get('fin_des_donnees') or '—'} (au plus {FORWARD_DAYS} jours après la dernière lettre)."
    )
    w("")
    w("| Exclusion | Nombre |")
    w("|---|---|")
    for k, v in (m.get("exclusions") or {}).items():
        w(f"| {EXCLUSION_FR.get(k, k)} | {v} |")
    if not m.get("exclusions"):
        w("| aucune | 0 |")
    w("")
    w("## 2. Rendements après la recommandation")
    w("")
    w("Base « ouverture » : achat à l'ouverture de la séance de réaction, revente à la clôture de la h-ième séance. Base « clôture » : depuis la première clôture après la réception. "
      "Net : moins l'aller-retour complet (CTO). Anormal : moins l'indice de la place (sinon la moitié liquide de la place, équipondérée). Excès : moins la moyenne des contrôles.")  # fmt: skip
    w("")
    w("| Base | Séances | Brut | Net CTO | Anormal | Excès sur les contrôles | Positives nettes |")
    w("|---|---|---|---|---|---|---|")
    for base, by_h in ens["rendements"].items():
        for h, x in by_h.items():
            w(
                f"| {base} | {h} | {_r(x['brut'], True)} | {_r(x['net_cto'], True)} | {_r(x['anormal'], True)} | {_r(x['exces_controles'], True)} | {_share(x['part_positive_nette'])} |"
            )
    ex = ens["excursions_atr"]
    w("")
    w(
        f"Excursions sur {par['excursion_sessions']} séances depuis l'ouverture, en ATR : favorable {_r(ex['mfe'])} (contrôles {_r(ex['mfe_controles'])}) ; défavorable {_r(ex['mae'])} (contrôles {_r(ex['mae_controles'])})."
    )
    w("")
    w("## 3. Règles d'entrée et de sortie (R net, coût complet)")
    w("")
    for k, v in ls.ENTRY_FR.items():
        w(f"- **{k}** : {v}")
    for k, v in ls.EXIT_FR.items():
        w(
            f"- **{k}** : {v} ; sortie au temps après {par['max_hold']} séances ; stop et objectif dans la même séance → stop d'abord."
        )
    w("")
    w(
        "| Bras | Applicables | Exécutés | R net CTO | Gagnants | R net PEA | Contrôles R net | Excès apparié | Seuils atteints |"
    )
    w("|---|---|---|---|---|---|---|---|---|")
    for k, a in arms.items():
        w(
            f"| {k} | {a['applicables']} | {a['executes']} ({_share(a['taux_execution'])}) | {_r(a['r_net_cto'])} | {_share(a['gagnants'])} | {_r(a['r_net_pea'])} | {_r(a['controles']['r_net_cto'])} | {_r(a['exces_apparie'])} | {'oui' if a['seuils_atteints'] else 'non'} |"
        )
    w("")
    w("Pourquoi un bras n'a pas de trade :")
    w("")
    for k, a in arms.items():
        why = ", ".join(f"{ls.STATUS_FR.get(s, s)} {c}" for s, c in a["issues"].items() if s != ls.EXECUTED)
        w(f"- {k} : {why or '—'}")
    w("")
    w("## 4. Découpages")
    w("")
    w(
        "Colonnes : n · rendement anormal à 5 et 20 séances (ouverture) · excès sur les contrôles à 20 séances (t) · E1·X3 R net · excès apparié E1·X3 (t) · E1·X1 R net."
    )
    titles = {"source": "Par source", "sens": "Achat ou à surveiller", "niveaux": "Avec ou sans niveaux", "groupe_de_places": "Par groupe de places",
              "annee": "Par année", "deja_en_hausse_avant_reception": f"Déjà en hausse de plus de {par['pre_move_up'] * 100:.0f} % sur {par['pre_move_sessions']} séances avant la réception",
              "nouveaute": "Nouvelle recommandation ou répétée (même source, même valeur, 20 séances)", "origine": "Origine (extraction à la réception ou avis de la revue)"}  # fmt: skip
    for name, groups in rep["decoupages"].items():
        w("")
        w(f"### {titles.get(name, name)}")
        w("")
        w(
            "| Groupe | n | Anormal 5 s. | Anormal 20 s. | Excès contrôles 20 s. | E1·X3 R net | Excès apparié E1·X3 | E1·X1 R net |"
        )
        w("|---|---|---|---|---|---|---|---|")
        for g, x in groups.items():
            o5, o20 = x["rendements"]["ouverture"].get("5") or {}, x["rendements"]["ouverture"].get("20") or {}
            e13, e11 = x["bras"]["E1_X3"], x["bras"]["E1_X1"]
            w(
                f"| {g} | {x['n']} | {_pct(o5.get('anormal'))} | {_pct(o20.get('anormal'))} | {_pct(o20.get('exces_controles'))} (t {_t(o20.get('exces_controles'))}) | {_r(e13['r_net_cto'])} | {_r(e13['exces_apparie'])} | {_r(e11['r_net_cto'])} |"
            )
    lt = rep["lettres_et_outil"]
    w("")
    w(f"## 5. Lettres et outil (configuration d'un détecteur à ± {lt['fenetre_seances']} séances)")
    w("")
    w(
        f"- Détecteurs rejoués avec leurs paramètres de production (d8 excepté : il exige le radar des forums du jour) ; politique actuelle : {m.get('politique_detecteurs')}."
    )
    w(f"- Configurations par détecteur : {lt['par_detecteur'] or 'aucune'}.")
    for lab, key in (
        ("Avec une configuration de l'outil", "avec_configuration"),
        ("Sans configuration", "sans_configuration"),
    ):
        x = lt[key]
        o20 = x["rendements"]["ouverture"].get("20") or {}
        w(
            f"- {lab} : n {x['n']} · anormal 20 séances {_r(o20.get('anormal'), True)} · E1·X3 {_r(x['bras']['E1_X3']['r_net_cto'])} · E1·X1 {_r(x['bras']['E1_X1']['r_net_cto'])}."
        )
    w(
        f"- Trade de l'outil sur ces valeurs (prix maximal, ordre limite, comme le backtest) : {_r(lt['trade_de_l_outil']['r_net_cto'])} · gagnants {_share(lt['trade_de_l_outil']['gagnants'])}."
    )
    w(
        "- Écart apparié lettre − outil : "
        + " ; ".join(f"{k} {_r(v)}" for k, v in lt["ecart_lettre_moins_outil"].items())
        + "."
    )
    w("")
    w("## 6. Méthode et limites")
    w("")
    w(
        "- **Séance de réaction** : la séance elle-même si la lettre arrive avant l'ouverture de la place de la valeur, sinon la séance suivante (calendrier de la place quand il est encodé, sinon les jours où ses valeurs ont coté). Niveaux, ATR et stop de structure : dernière clôture connue avant cette ouverture. Aucune donnée postérieure n'entre dans une décision."
    )
    w(
        f"- **Contrôles** : {par['controls']} valeurs par recommandation, même place, même séance, liquidité au moins {par['liquidity_share'] * 100:.0f} % de celle de la valeur (ou la médiane de la place), ATR % dans un rapport {1 / par['vol_band']:.2f}–{par['vol_band']:.2f} (sinon les plus proches), tirées avec la graine {m['graine']} ; mêmes entrées et sorties, niveaux de la lettre transposés en ATR depuis la clôture de référence. Les valeurs recommandées le même jour en sont exclues."
    )
    w(
        f"- **Excès apparié** : R net de la recommandation moins la moyenne des contrôles exécutés (au moins {par['min_control_trades']}). Intervalles à 95 % (normale), proportions avec Wilson ; une statistique sur moins de {seuil['masque_sous']} observations est masquée."
    )
    w(f"- **Coûts** : {m['couts']}. Avant impôt.")
    w(
        "- Cours stockés non ajustés (comme les niveaux des lettres) ; une fenêtre qui traverse une opération sur titres est écartée. Recommandations successives d'une même valeur : non indépendantes (découpage « nouveauté ») — le t est optimiste."
    )
    w(
        "- Biais : référentiel d'aujourd'hui (valeurs radiées absentes des contrôles si leurs cours manquent), capitalisations et TTF d'aujourd'hui, rang de force relative recalculé sur les places chargées, lettres lues par l'outil (une valeur que la lecture a manquée n'est pas comptée)."
    )
    w(
        "- Pour aller plus loin : écrire un plan pré-enregistré (docs/31) avec la règle choisie **avant** de lire de nouvelles lettres, puis la tester sur les lettres reçues après ce plan."
    )
    return "\n".join(L) + "\n"


def write(rep: dict[str, Any], out_dir: Path) -> tuple[Path, Path]:
    """``letters_study_<horodatage>.json`` and ``.md`` (French); refuses any price, name or ISIN in the report."""
    from app.domain.research.report import assert_no_price_fields

    assert_no_price_fields(rep)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    j, t = out_dir / f"letters_study_{stamp}.json", out_dir / f"letters_study_{stamp}.md"
    j.write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    t.write_text(render_md(rep), encoding="utf-8")
    return j, t
