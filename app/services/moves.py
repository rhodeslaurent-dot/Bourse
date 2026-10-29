# ruff: noqa: E501
"""docs/34 — study of the rises the tool missed (user request of 28/10/2026), exploratory.

For the European venues of the research groups (P0 / P1 / P2 — New York, London and Zurich stay unread until
registration n° 2 has run), on the stored daily history:
1. **episodes**: rises of more than ``min_rise`` out of the daily noise (``app.domain.moves``), sampled;
2. **replay**: on each session from two sessions before the low to five after it, the production rules exactly as
   the engine applies them — every detector with its production parameters, zones, admission (maximum entry price,
   R4/R5), the order at the maximum price valid ``entry.expiry_sessions``, the score (blocks T, R, M), the market
   gate of the venue (regime replayed: index vs MM50, breadth, volatility) — and the production policy
   (detectors disabled by ADR-014, watch-only by ADR-013); the same replay with every detector active says what
   the policy costs;
3. **why not**: the furthest stage each episode reached, and the simulated trade when an order would have filled;
4. **control**: every session of a random set of values, taken without looking at what followed — base rate of an
   exploitable rise (triple barrier), precision and lift of each detector and of each feature decile, net R at
   full cost (CTO and PEA).

Not replayed (said in the report): letters, news and catalysts, fundamentals, earnings dates, portfolio caps,
circuit breaker, risk level of the day; market cap and PEA eligibility are today's. Prices are those stored
(unadjusted; sessions after a corporate action are skipped as in the backtest). No raw price leaves the PC: the
report holds aggregates only.
"""

from __future__ import annotations

import json
import logging
import math
import random
import statistics
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.config import LoadedConfig
from app.domain.admission import AdmissionInput, Status, admit
from app.domain.backtest import TradeResult, simulate_trade
from app.domain.calendar import MarketCalendars
from app.domain.conjuncture import market_gate
from app.domain.costs import CostParams, ftt_rate_any, net_outcome, round_trip_fee_fraction
from app.domain.detectors import DETECTORS, DetectorContext, Setup
from app.domain.indicators import Bar, DailyFeatures, compute_daily_features, sma
from app.domain.moves import (
    Episode,
    MoveParams,
    atr_series,
    barrier_label,
    daily_sigma,
    decile_lift,
    find_episodes,
    size_bucket,
    vol_bucket,
    wilson,
)
from app.domain.regime import RegimeInputs, compute_regime
from app.domain.scoring import ScoreInput, score
from app.domain.zones import detect_zones

log = logging.getLogger("bourse.moves")
Progress = Callable[[str], None]
HOLDING_DAYS = {"days": 5, "swing_2_6w": 20, "semaines": 40}
WINDOW = 280  # sessions given to the detectors (as the backtest)
MIN_BARS = 260  # D7 needs 200 + its slope; the RS rank needs 250 sessions

STAGES = (
    "aucun_signal",
    "politique",
    "sans_objectif",
    "stop_trop_large",
    "configuration_degradee",
    "score",
    "portail",
    "resultats_imminents",
    "ordre_non_execute",
    "ordre_execute",
)
STAGE_FR = {
    "aucun_signal": "aucun détecteur ne se déclenche",
    "politique": "détecteur désactivé (ADR-014) ou en surveillance seulement (ADR-013)",
    "sans_objectif": "pas d'objectif exploitable (aucune résistance au-dessus)",
    "stop_trop_large": "stop trop large (au-delà de la limite en ADR)",
    "configuration_degradee": "configuration dégradée (support perdu ou cassure rendue)",
    "score": "score insuffisant (ratio d'admission, confiance ou risque élevé)",
    "portail": "portail de marché fermé (régime rouge de la place)",
    "resultats_imminents": "publication de résultats imminente (R14 : un stop ne protège pas d'un écart)",
    "ordre_non_execute": "ordre limite au prix maximal jamais exécuté (le cours n'est pas revenu)",
    "ordre_execute": "ordre exécuté",
}
FEATURES = (
    "rendement_jour_z",
    "ecart_ouverture",
    "volume_relatif",
    "position_dans_la_seance",
    "distance_plus_haut_52s",
    "distance_pivot_60",
    "rang_force_relative",
    "atr_pct",
    "consolidation_20",
    "rendement_5j",
    "rendement_20j",
    "rsi14",
    "au_dessus_mm50_et_mm200",
    "jour_de_publication",
)


@dataclass
class Response:
    detector: str
    stage: str
    policy_blocked: bool
    kind: str
    trade: TradeResult | None = None
    net_r_cto: float | None = None
    net_r_pea: float | None = None
    entry: float | None = None
    restricted_ok: bool | None = None  # admitted BUY path only: would it pass the restricted gate (R10)?


@dataclass
class EpisodeResult:
    group: str
    rise: float
    sessions: int
    shape: str
    vol: str
    lag_first_signal: int | None  # sessions between the low and the first detector firing (all active)
    prod_stage: str
    all_stage: str
    prod_detectors: list[str] = field(default_factory=list)
    all_detectors: list[str] = field(default_factory=list)
    captured_share: float | None = None  # (peak − entry) / (peak − low) of the executed order (all active)
    net_r_cto: float | None = None
    net_r_pea: float | None = None
    after_first_close: float | None = None  # share of the rise still to come after the first up close
    profile: dict[str, bool | None] = field(default_factory=dict)
    paris: bool = False
    prod_trade_restricted_ok: bool | None = None  # Paris, production order executed: still there under « restreint »?


@dataclass
class ControlRow:
    group: str
    vol: str
    label: int | None
    features: dict[str, float | None]
    prod_stage: str
    fired: list[str]  # detectors firing (all active)
    trades: list[tuple[str, float | None, float | None, bool]]  # detector, net R CTO, net R PEA, policy blocked


# --- context of the replay --------------------------------------------------------------------------------


@dataclass
class Replay:
    config: LoadedConfig
    det_params: dict[str, dict[str, Any]]
    disabled: set[str]
    watch_only: set[str]
    k_rr: float
    slip: float
    expiry: int
    capital: float
    risk_budget: float
    wick: float
    stop_buffer: float
    zone_p: dict[str, Any]
    scoring: dict[str, Any]
    blackout_hours: float = 48.0
    restricted_rr: float = 2.5
    restricted_score: float = 75.0
    pfu_rate: float = 0.314


def _replay_context(config: LoadedConfig) -> Replay:
    from app.services.engine import _tax

    p = config.params
    dets = {k: dict(v) for k, v in (p.detectors or {}).items() if k in DETECTORS and isinstance(v, dict)}
    sp = p.scoring or {}
    return Replay(
        config=config,
        det_params=dets,
        disabled={k for k, v in dets.items() if not v.get("enabled", False)},
        watch_only=set(sp.get("watch_only_detectors") or []),
        k_rr=float((p.admission or {}).get("min_reward_risk", 2.0)),
        slip=float(p.risk.slippage_pct),
        expiry=int((getattr(p, "entry", None) or {}).get("expiry_sessions", 3)),
        capital=float(p.capital.capital_pilote_eur),
        risk_budget=float(p.capital.capital_pilote_eur) * float(p.risk.max_risk_per_trade_pct),
        wick=float(getattr(p.risk.stop, "wick_buffer_pct", 0.005) or 0.005),
        stop_buffer=float(p.risk.stop.buffer_atr_mult),
        zone_p=dict(getattr(p, "zones", None) or {}),
        scoring=dict(sp),
        blackout_hours=float(getattr(p.risk, "earnings_blackout_hours", 48) or 48),
        restricted_rr=float((getattr(p, "market_gate", None) or {}).get("restricted_min_net_rr", 2.5)),
        restricted_score=float((getattr(p, "market_gate", None) or {}).get("restricted_min_score", 75)),
        pfu_rate=float(_tax(config).get("pfu_total_rate") or 0.0),
    )


def _costs(config: LoadedConfig, inst: Any) -> tuple[CostParams, CostParams | None]:
    """CTO cash (Saxo) as the backtest; the PEA (BoursoBank grid of the current formula) when the ISIN prefix is
    PEA-eligible. Taxes on transactions: the bare rates (no point-in-time market cap)."""
    from app.services.backtest import _cost_params, ttf_rate_of
    from app.services.engine import _fees, _tax

    cto = _cost_params(config, inst)
    pea_c = set((config.params.pea or {}).get("eligible_countries") or [])
    if inst.isin[:2] not in pea_c or (inst.currency or "EUR") != "EUR":
        return cto, None
    tax = _tax(config)  # the block in force today (dated parameters, rule 5)
    ttf = ttf_rate_of(tax, inst.isin, inst.market_cap_eur)
    ftt = ftt_rate_any((tax.get("ftt_by_country") or {}).get(inst.isin[:2]))
    return cto, CostParams(_fees(config, "pea"), ttf_rate=ttf, ftt_rate=ftt)


def _net_r(res: TradeResult, cp: CostParams | None, notional: float) -> float | None:
    if cp is None:
        return None
    return res.net_r(round_trip_fee_fraction(notional, cp))


def _score_ok(
    rp: Replay,
    det: str,
    window: list[Bar],
    f: DailyFeatures,
    rs: float | None,
    setup: Setup,
    p_max: float,
    target: float,
    light: str | None,
    adv_eur: float | None,
    inst: Any,
    next_earnings_days: int | None = None,
) -> tuple[bool, float]:
    last = window[-1]
    sc = score(
        ScoreInput(
            rs_rank=rs,
            above_mm50_mm200=bool(f.mm50 and f.mm200 and last.close > f.mm50 and last.close > f.mm200),
            mm50_above_mm200=bool(f.mm50 and f.mm200 and f.mm50 > f.mm200),
            above_rising_mm20=bool(f.mm20 and last.close > f.mm20),
            rvol=f.rvol,
            clean_trigger=True,
            over_extended=bool(f.mm20 and f.atr14 and last.close > f.mm20 + 1.5 * f.atr14),
            resistance_within_3pct=bool(target < last.close * 1.03),
            stop_distance_adr=((p_max - setup.stop) / (f.adr20 * p_max)) if f.adr20 else None,
            # history of announcements loaded (earnings-history): replayed; else unknown → the favourable case
            earnings_within_10_days=next_earnings_days is not None and next_earnings_days <= 10,
            adv_eur=adv_eur,
            market_cap_eur=inst.market_cap_eur,
            gap_pct=(last.open / window[-2].close - 1) if window[-2].close else None,
            non_eur=(inst.currency or "EUR") != "EUR",
            regime=light,
            small_cap=bool(inst.small_cap),
        ),
        list((rp.scoring.get("admission_blocks") or {}).get(det, ["T", "R", "M"])),
        float(rp.scoring.get("c_bonus_cap", 5)),
        data_complete=True,
        f_min_available=int(rp.scoring.get("f_min_available_subcriteria", 2)),
    )
    min_ratio = float(rp.scoring.get("buy_min_ratio_orange" if light == "orange" else "buy_min_ratio", 0.75))
    ok = (
        sc.admission_ratio >= min_ratio
        and sc.confidence >= int(rp.scoring.get("buy_min_confidence", 3))
        and sc.risk != "eleve"
    )
    return ok, sc.total


def responses(
    rp: Replay,
    inst: Any,
    window: list[Bar],
    after: list[Bar],
    rs: float | None,
    light: str | None,
    cto: CostParams,
    pea: CostParams | None,
    next_earnings_days: int | None = None,
) -> list[Response]:
    """What the engine would answer at the close of ``window[-1]``, per detector that fires (every detector is run
    with its production parameters; ``policy_blocked`` tells what the production policy does with it)."""
    if len(window) < MIN_BARS:
        return []
    ctx = DetectorContext(rs, [], rp.wick, rp.stop_buffer)
    fired: list[tuple[str, Setup]] = []
    zones = None
    f: DailyFeatures | None = None
    for name, dp in rp.det_params.items():
        if name == "d8_buzz_precoce":
            continue  # needs the forum radar of the day: not replayable
        if not DETECTORS[name](dp).detect(window, ctx):
            continue
        if zones is None:
            f = compute_daily_features(window)
            zp = rp.zone_p
            zones = detect_zones(
                window[-int(zp.get("lookback_sessions", 250)) :],
                f.atr14 or 0.0,
                int(zp.get("pivot_window", 5)),
                float(zp.get("merge_atr_mult", 0.5)),
                int(zp.get("min_touches", 2)),
            )
        zctx = DetectorContext(rs, zones, rp.wick, rp.stop_buffer)
        fired += [(name, x) for x in DETECTORS[name](dp).detect(window, zctx)]
    if not fired:
        return []
    f = f or compute_daily_features(window)
    adv = statistics.fmean(b.close * b.volume for b in window[-20:]) if len(window) >= 20 else None
    gate = market_gate(True, "modere", light).state  # letters assumed favourable, conjuncture moderate
    out: list[Response] = []
    for name, st in fired:
        blocked = name in rp.disabled or name in rp.watch_only
        r = Response(name, "", blocked, st.kind.value)
        if not st.targets:
            r.stage = "sans_objectif"
            out.append(r)
            continue
        target = st.targets[0]
        notional = min(
            rp.capital * float(rp.config.params.risk.max_position_pct_of_capital),
            rp.risk_budget / max(1e-9, (st.p_max_technical - st.stop) / st.p_max_technical),
        )
        adm = admit(
            AdmissionInput(
                window[-1].close,
                target,
                st.stop,
                st.kind,
                st.level_low,
                round_trip_fee_fraction(notional, cto),
                rp.k_rr,
                st.p_max_technical,
            )
        )
        if adm.status == Status.DEGRADED or adm.p_max is None:
            r.stage = "configuration_degradee"
            out.append(r)
            continue
        if st.warnings:
            r.stage = "stop_trop_large"
            out.append(r)
            continue
        if adm.status == Status.ADMITTED:  # a refused one is a limit waiting for a pullback: no score, no gate
            ok, total = _score_ok(rp, name, window, f, rs, st, adm.p_max, target, light, adv, inst, next_earnings_days)
            if not ok:
                r.stage = "score"
                out.append(r)
                continue
            # R10 on Euronext Paris: without the letters' published conditions the gate is « restreint »
            stop_exec = st.stop * (1 - rp.slip)
            shares = max(1, int(rp.risk_budget / max(1e-9, adm.p_max - stop_exec)))
            # B1 (29/10/2026): as the engine, the ratio net of fees (R4), not the after-tax one
            no = net_outcome(shares, adm.p_max, target, stop_exec, replace(cto, tax_rate=rp.pfu_rate))
            r.restricted_ok = (no.fee_net_reward_risk or 0) >= rp.restricted_rr and total >= rp.restricted_score
            if gate == "restreint_plus":
                r.stage = "portail"
                out.append(r)
                continue
            if next_earnings_days is not None and next_earnings_days * 24 <= rp.blackout_hours + 24:
                r.stage = "resultats_imminents"  # R14: a stop does not protect from the gap of an announcement
                out.append(r)
                continue
        hold = int(rp.det_params[name].get("time_stop_sessions") or HOLDING_DAYS.get(st.horizon, 20))
        res = simulate_trade(after, adm.p_max, st.stop, target, rp.slip, rp.expiry, hold)
        if res is None:
            r.stage = "ordre_non_execute"
        else:
            r.stage, r.trade, r.entry = "ordre_execute", res, res.entry
            r.net_r_cto, r.net_r_pea = _net_r(res, cto, notional), _net_r(res, pea, notional)
        out.append(r)
    return out


def _best(stages: list[str]) -> str:
    return max(stages, key=STAGES.index) if stages else "aucun_signal"


# --- data -------------------------------------------------------------------------------------------------


def _regimes(
    config: LoadedConfig,
    hist: dict[str, Any],
    groups_of: dict[str, str],
    insts: dict[str, Any],
    idx_hist: dict[str, Any],
    specs: dict[str, str],
) -> dict[tuple[str, date], str]:
    """Light of each venue on each day, replayed with the production rules (index vs MM50 for both index inputs,
    breadth of the venue's values above MM50, volatility percentile of its index; distribution days not replayed)."""
    from app.domain.indicators import atr, percentile_rank
    from app.scheduler.jobs.daily_regime import regime_params_from
    from app.services.indices import EUROPE

    rp = regime_params_from(config)
    min_n = int((config.params.regime or {}).get("breadth_min_values", 10))
    above_by_day: dict[str, dict[date, list[bool]]] = defaultdict(lambda: defaultdict(list))
    for isin, h in hist.items():
        closes = [b.close for b in h.bars]
        acc = 0.0
        for j, c in enumerate(closes):
            acc += c - (closes[j - 50] if j >= 50 else 0.0)
            if j >= 49:
                above_by_day[insts[isin].mic or ""][h.days[j]].append(c > acc / 50)
    out: dict[tuple[str, date], str] = {}
    cache: dict[str, dict[date, tuple[bool | None, float | None]]] = {}
    for mic, by_day in above_by_day.items():
        iid = specs.get(mic) if specs.get(mic) in idx_hist else specs.get(EUROPE)
        ih = idx_hist.get(iid or "")
        if ih is None:
            continue
        if iid not in cache:
            closes = [b.close for b in ih.bars]
            atrp = [
                (a / closes[k]) if (a := atr(ih.bars[max(0, k - 39) : k + 1], 14)) is not None and closes[k] else None
                for k in range(len(ih.bars))
            ]
            comp: dict[date, tuple[bool | None, float | None]] = {}
            for k in range(len(ih.bars)):
                m = sma(closes[k - 49 : k + 1], 50) if k >= 49 else None
                win = [x for x in atrp[max(0, k - 249) : k + 1] if x is not None]
                vol = percentile_rank(atrp[k], win) if atrp[k] is not None and len(win) >= 10 else None
                comp[ih.days[k]] = (closes[k] > m if m else None, vol)
            cache[iid or ""] = comp
        comp = cache[iid or ""]
        for d, flags in by_day.items():
            above, vol = comp.get(d, (None, None))
            breadth = sum(flags) / len(flags) if len(flags) >= min_n else None
            out[(mic, d)] = compute_regime(RegimeInputs(above, above, breadth, None, vol), rp).light.value
    return out


def _features(window: list[Bar], f: DailyFeatures, rs: float | None) -> dict[str, float | None]:
    last, prev = window[-1], window[-2]
    closes = [b.close for b in window]
    sigma = daily_sigma(closes, len(closes) - 1, 20)
    rng = last.high - last.low
    return {
        "rendement_jour_z": ((last.close / prev.close - 1) / sigma) if sigma and prev.close else None,
        "ecart_ouverture": (last.open / prev.close - 1) if prev.close else None,
        "volume_relatif": f.rvol,
        "position_dans_la_seance": ((last.close - last.low) / rng) if rng > 0 else None,
        "distance_plus_haut_52s": (last.close / f.high_52w - 1) if f.high_52w else None,
        "distance_pivot_60": (last.close / f.pivot_60 - 1) if f.pivot_60 else None,
        "rang_force_relative": rs,
        "atr_pct": (f.atr14 / last.close) if f.atr14 and last.close else None,
        "consolidation_20": f.consolidation_20,
        "rendement_5j": (last.close / closes[-6] - 1) if len(closes) > 6 and closes[-6] else None,
        "rendement_20j": (last.close / closes[-21] - 1) if len(closes) > 21 and closes[-21] else None,
        "rsi14": f.rsi14,
        "au_dessus_mm50_et_mm200": float(bool(f.mm50 and f.mm200 and last.close > f.mm50 > f.mm200)),
    }


class EarningsIndex:
    """Announcements of one value placed on its sessions: the reaction session (the announcement day, or the next
    session after an announcement after the close) and the days to the next announcement. A session is *known*
    only within ``margin_days`` calendar days of a loaded announcement — the whole span when consecutive ones are
    at most 2 × margin apart (half-yearly reporting) — so a hole in the history (missing years) stays unknown, as
    in the research panel (``earnings_flags``). Unknown → None, never « no announcement »."""

    def __init__(self, days: list[date], events: list[tuple[date, str]], margin_days: int = 100) -> None:
        import bisect

        self.days = days
        self.dates = sorted({d for d, _ in events})
        self.react: set[int] = set()
        for d, when in events:
            i = bisect.bisect_right(days, d) if when == "amc" else bisect.bisect_left(days, d)
            if i < len(days) and (days[i] - d).days <= 5:  # history not started yet → not its reaction
                self.react.add(i)
        self.margin = timedelta(days=margin_days)

    def covered(self, k: int) -> bool:
        import bisect

        if not self.dates:
            return False
        d = self.days[k]
        i = bisect.bisect_left(self.dates, d)
        near_next = i < len(self.dates) and self.dates[i] - d <= self.margin
        near_prev = i > 0 and d - self.dates[i - 1] <= self.margin
        return near_next or near_prev

    def is_reaction(self, k: int) -> bool | None:
        return (k in self.react) if self.covered(k) else None

    def days_to_next(self, k: int) -> int | None:
        import bisect

        if not self.covered(k):
            return None
        i = bisect.bisect_right(self.dates, self.days[k])
        if i >= len(self.dates):
            return None  # after the last loaded announcement the next one is not known
        gap = (self.dates[i] - self.days[k]).days
        regular = i > 0 and self.dates[i] - self.dates[i - 1] <= 2 * self.margin  # e.g. half-yearly reporting
        return gap if gap <= self.margin.days or regular else None  # a hole before the next one: unknown


def _earnings(s: Session, isins: list[str], hist: dict[str, Any]) -> dict[str, EarningsIndex]:
    from sqlalchemy import select

    from app.db.models import EarningsEvent

    ev: dict[str, dict[date, str]] = defaultdict(dict)
    for e in s.scalars(select(EarningsEvent).where(EarningsEvent.isin.in_(isins))):
        prev = ev[e.isin].get(e.day)
        if prev is None or prev == "unknown":
            ev[e.isin][e.day] = e.when
    return {isin: EarningsIndex(hist[isin].days, list(ev[isin].items())) for isin in isins if isin in ev}


# --- study ------------------------------------------------------------------------------------------------# --- study ------------------------------------------------------------------------------------------------


def run_study(
    s: Session,
    config: LoadedConfig,
    calendars: MarketCalendars,
    start: date,
    end: date,
    max_episodes: int = 20_000,
    control_values: int = 120,
    seed: int = 7,
    params: MoveParams | None = None,
    progress: Progress | None = None,
    max_instruments: int | None = None,
) -> dict[str, Any]:
    from app.services.backtest import load_history, rs_ranks
    from app.services.indices import index_specs
    from app.services.research import mic_groups, select_instruments

    mp = params or MoveParams()
    say = progress or (lambda m: None)
    rp = _replay_context(config)
    by_group, without = select_instruments(s, config, calendars, "all", max_instruments)  # P0 / P1 / P2 only
    groups_of = mic_groups(config, calendars)
    insts = {i.isin: i for grp in by_group.values() for i in grp}
    say(
        f"{len(insts)} valeur(s) des places européennes (groupes {', '.join(sorted(by_group))}) ; chargement de l'historique…"
    )
    hist = load_history(s, sorted(insts), start, end)
    say(f"{len(hist)} valeur(s) avec au moins 260 séances ; rangs de force relative…")
    ranks = rs_ranks(hist) if hist else {}
    specs = {x.market: x.id for x in index_specs(config)}
    idx_hist = load_history(s, sorted(set(specs.values())), start, end)
    say("régimes rejoués par place…")
    lights = _regimes(config, hist, groups_of, insts, idx_hist, specs)
    rng = random.Random(seed)
    # 1. episodes (all, then sampled)
    pool: list[tuple[str, Episode]] = []
    for isin, h in hist.items():
        for ep in find_episodes(h.bars, mp):
            if (
                ep.start >= MIN_BARS
                and start <= h.days[ep.start] <= end
                and not (h.ca_index & set(range(ep.start - 1, ep.peak + 1)))
            ):
                pool.append((isin, ep))
    n_pool = len(pool)
    sample = rng.sample(pool, max_episodes) if n_pool > max_episodes else pool
    say(f"{n_pool} hausse(s) repérée(s) ; {len(sample)} rejouée(s)")
    costs = {isin: _costs(config, insts[isin]) for isin in hist}
    earn = _earnings(s, sorted(hist), hist)
    say(f"publications de résultats connues pour {len(earn)} valeur(s) (earnings-history)")
    results: list[EpisodeResult] = []
    for n, (isin, ep) in enumerate(sample, 1):
        if n % 1000 == 0:
            say(f"hausses : {n}/{len(sample)}")
        results.append(
            _replay_episode(rp, insts[isin], hist[isin], ep, ranks, lights, costs[isin], groups_of, mp, earn.get(isin))
        )
    # 2. control: every session of a random set of values
    ctrl_isins = rng.sample(sorted(hist), min(control_values, len(hist)))
    rows: list[ControlRow] = []
    for n, isin in enumerate(ctrl_isins, 1):
        say(f"contrôle : valeur {n}/{len(ctrl_isins)}")
        rows += _control_rows(
            rp, insts[isin], hist[isin], ranks, lights, costs[isin], groups_of, mp, start, end, earn.get(isin)
        )
    say("agrégation…")
    return build_report(
        results,
        rows,
        mp,
        {
            "debut": start.isoformat(),
            "fin": end.isoformat(),
            "valeurs": len(hist),
            "groupes": sorted(by_group),
            "sans_groupe": without,
            "hausses_reperees": n_pool,
            "hausses_rejouees": len(sample),
            "valeurs_controle": len(ctrl_isins),
            "jours_controle": len(rows),
            "graine": seed,
            "valeurs_avec_publications": len(earn),
            "parametres": asdict(mp),
            "politique": {
                d: "désactivé (ADR-014)"
                if d in rp.disabled
                else "surveillance seulement (ADR-013)"
                if d in rp.watch_only
                else "actif"
                for d in rp.det_params
            },
        },
    )


def _replay_episode(
    rp: Replay,
    inst: Any,
    h: Any,
    ep: Episode,
    ranks: dict,
    lights: dict,
    costs: tuple,
    groups_of: dict[str, str],
    mp: MoveParams,
    earn: EarningsIndex | None = None,
) -> EpisodeResult:
    cto, pea = costs
    prod: list[str] = []
    allst: list[str] = []
    prod_det: set[str] = set()
    all_det: set[str] = set()
    lag: int | None = None
    best_trade: Response | None = None
    prod_exec: list[Response] = []
    for a in range(max(MIN_BARS, ep.start - 2), min(ep.start + 5, ep.peak - 1) + 1):
        window = h.bars[max(0, a - WINDOW + 1) : a + 1]
        day = h.days[a]
        rs = ranks.get((inst.isin, day))
        light = lights.get((inst.mic or "", day))
        nxt = earn.days_to_next(a) if earn is not None else None
        rsp = responses(rp, inst, window, h.bars[a + 1 :], rs, light, cto, pea, nxt)
        if rsp and lag is None:
            lag = a - ep.start
        for r in rsp:
            all_det.add(r.detector)
            allst.append(r.stage)
            prod.append("politique" if r.policy_blocked else r.stage)
            if not r.policy_blocked:
                prod_det.add(r.detector)
            if r.stage == "ordre_execute" and best_trade is None:
                best_trade = r
            if r.stage == "ordre_execute" and not r.policy_blocked:
                prod_exec.append(r)
    closes = [b.close for b in h.bars]
    low, top = closes[ep.start], closes[ep.peak]
    captured = None
    if best_trade is not None and best_trade.entry and top > low:
        captured = (top - best_trade.entry) / (top - low)
    first_up = next((j for j in range(ep.start + 1, ep.peak + 1) if closes[j] > closes[j - 1]), ep.start + 1)
    after_first = (top / closes[first_up] - 1) / ep.rise if ep.rise > 0 and first_up <= ep.peak else None
    k = ep.start + 1
    f = compute_daily_features(h.bars[max(0, k - WINDOW + 1) : k + 1])
    b, pb = h.bars[k], h.bars[k - 1]
    profile = {
        "ecart_ouverture_2pct": (b.open / pb.close - 1) >= 0.02 if pb.close else None,
        "volume_relatif_2": (f.rvol >= 2) if f.rvol is not None else None,
        "pres_du_plus_haut_52s": (b.close >= 0.97 * f.high_52w) if f.high_52w else None,
        "au_dessus_mm200": (b.close > f.mm200) if f.mm200 else None,
        "force_relative_80": (ranks.get((inst.isin, h.days[k])) or 0) >= 80
        if ranks.get((inst.isin, h.days[k])) is not None
        else None,
        "apres_baisse_10pct_sur_20j": (closes[ep.start] / max(closes[max(0, ep.start - 20) : ep.start + 1]) - 1)
        <= -0.10,
        "debut_sur_publication_de_resultats": (
            any(earn.is_reaction(j) for j in range(ep.start, min(ep.start + 3, len(h.bars))))
            if earn is not None and earn.covered(ep.start)
            else None
        ),
    }
    return EpisodeResult(
        group=groups_of.get(inst.mic or "", "?"), rise=ep.rise, sessions=ep.sessions, shape=ep.shape,
        vol=vol_bucket(ep.sigma), lag_first_signal=lag, prod_stage=_best(prod), all_stage=_best(allst),
        prod_detectors=sorted(prod_det), all_detectors=sorted(all_det), captured_share=captured,
        net_r_cto=best_trade.net_r_cto if best_trade else None, net_r_pea=best_trade.net_r_pea if best_trade else None,
        after_first_close=after_first, profile=profile, paris=inst.mic == "XPAR",
        prod_trade_restricted_ok=any(r.restricted_ok is not False for r in prod_exec) if prod_exec else None,
    )  # fmt: skip


def _control_rows(
    rp: Replay,
    inst: Any,
    h: Any,
    ranks: dict,
    lights: dict,
    costs: tuple,
    groups_of: dict[str, str],
    mp: MoveParams,
    start: date,
    end: date,
    earn: EarningsIndex | None = None,
) -> list[ControlRow]:
    cto, pea = costs
    atrs = atr_series(h.bars)
    closes = [b.close for b in h.bars]
    out: list[ControlRow] = []
    for k in range(MIN_BARS, len(h.bars) - 1):
        day = h.days[k]
        if day < start or day > end or k in h.ca_index or (k + 1) in h.ca_index:
            continue
        window = h.bars[max(0, k - WINDOW + 1) : k + 1]
        rs = ranks.get((inst.isin, day))
        f = compute_daily_features(window)
        sigma = daily_sigma(closes, k + 1, mp.sigma_sessions)
        nxt = earn.days_to_next(k) if earn is not None else None
        rsp = responses(rp, inst, window, h.bars[k + 1 :], rs, lights.get((inst.mic or "", day)), cto, pea, nxt)
        feats = _features(window, f, rs)
        react = earn.is_reaction(k) if earn is not None else None
        feats["jour_de_publication"] = None if react is None else float(react)
        out.append(ControlRow(
            group=groups_of.get(inst.mic or "", "?"), vol=vol_bucket(sigma),
            label=barrier_label(h.bars, k, atrs[k], sigma, mp), features=feats,
            prod_stage=_best(["politique" if r.policy_blocked else r.stage for r in rsp]),
            fired=sorted({r.detector for r in rsp}),
            trades=[(r.detector, r.net_r_cto, r.net_r_pea, r.policy_blocked) for r in rsp if r.stage == "ordre_execute"],
        ))  # fmt: skip
    return out


# --- report -----------------------------------------------------------------------------------------------


def _share(k: int, n: int) -> dict[str, Any]:
    lo, hi = wilson(k, n)
    return {"n": n, "k": k, "part": round(k / n, 4) if n else None, "ic95": [round(lo, 4), round(hi, 4)]}


def _mean_ci(xs: list[float]) -> dict[str, Any]:
    if not xs:
        return {"n": 0}
    m = statistics.fmean(xs)
    sd = statistics.stdev(xs) if len(xs) > 1 else 0.0
    h = 1.96 * sd / math.sqrt(len(xs)) if len(xs) > 1 else None
    return {"n": len(xs), "moyenne": round(m, 3), "ic95": [round(m - h, 3), round(m + h, 3)] if h is not None else None}


def build_report(
    results: list[EpisodeResult], rows: list[ControlRow], mp: MoveParams, meta: dict[str, Any]
) -> dict[str, Any]:
    n = len(results)
    rep: dict[str, Any] = {"meta": meta}
    # profile of the rises
    rep["profil"] = {
        "par_groupe": dict(Counter(r.group for r in results)),
        "par_taille": dict(Counter(size_bucket(r.rise) for r in results)),
        "par_forme": dict(Counter(r.shape for r in results)),
        "par_volatilite": dict(Counter(r.vol for r in results)),
        "duree_mediane_seances": statistics.median([r.sessions for r in results]) if results else None,
        "hausse_mediane": round(statistics.median([r.rise for r in results]), 4) if results else None,
        "part_restant_apres_premiere_cloture_haussiere": round(
            statistics.median([r.after_first_close for r in results if r.after_first_close is not None]), 3
        )
        if results
        else None,
        "debut_de_hausse": {
            k: _share(
                sum(1 for r in results if r.profile.get(k)), sum(1 for r in results if r.profile.get(k) is not None)
            )
            for k in (results[0].profile if results else {})
        },
    }

    # funnel
    def funnel(key: str) -> dict[str, Any]:
        c = Counter(getattr(r, key) for r in results)
        return {st: _share(c.get(st, 0), n) for st in STAGES}

    rep["entonnoir_production"] = funnel("prod_stage")
    rep["entonnoir_tous_detecteurs"] = funnel("all_stage")
    strata: dict[str, Any] = {}
    for name, keyf in (
        ("groupe", lambda r: r.group),
        ("taille", lambda r: size_bucket(r.rise)),
        ("forme", lambda r: r.shape),
        ("volatilite", lambda r: r.vol),
    ):
        sub: dict[str, Any] = {}
        for val in sorted({keyf(r) for r in results}):
            rr = [r for r in results if keyf(r) == val]
            sub[val] = {
                "n": len(rr),
                "ordre_execute_production": _share(sum(1 for r in rr if r.prod_stage == "ordre_execute"), len(rr)),
                "ordre_execute_tous": _share(sum(1 for r in rr if r.all_stage == "ordre_execute"), len(rr)),
                "aucun_signal": _share(sum(1 for r in rr if r.all_stage == "aucun_signal"), len(rr)),
            }
        strata[name] = sub
    rep["par_strate"] = strata
    det_names = sorted({d for r in results for d in r.all_detectors})
    rep["detecteurs_sur_les_hausses"] = {
        d: {"declenche": _share(sum(1 for r in results if d in r.all_detectors), n)} for d in det_names
    }
    lags = [r.lag_first_signal for r in results if r.lag_first_signal is not None]
    rep["delai_premier_signal"] = dict(sorted(Counter(lags).items()))
    par = [r for r in results if r.paris and r.prod_trade_restricted_ok is not None]
    rep["paris_portail_restreint"] = _share(sum(1 for r in par if r.prod_trade_restricted_ok), len(par))
    ex = [r for r in results if r.all_stage == "ordre_execute"]
    rep["ordres_sur_les_hausses"] = {
        "part_capturee": _mean_ci([r.captured_share for r in ex if r.captured_share is not None]),
        "r_net_cto": _mean_ci([r.net_r_cto for r in ex if r.net_r_cto is not None]),
        "r_net_pea": _mean_ci([r.net_r_pea for r in ex if r.net_r_pea is not None]),
    }
    # control: base rate, precision, lift
    lab = [r for r in rows if r.label is not None]
    base = sum(r.label or 0 for r in lab) / len(lab) if lab else None
    rep["controle"] = {
        "jours": len(rows),
        "jours_etiquetes": len(lab),
        "taux_de_base": _share(sum(r.label or 0 for r in lab), len(lab)),
    }
    rep["controle"]["taux_de_base_par_volatilite"] = {
        v: _share(sum(r.label or 0 for r in lab if r.vol == v), sum(1 for r in lab if r.vol == v))
        for v in sorted({r.vol for r in lab})
    }
    det: dict[str, Any] = {}
    for d in sorted({x for r in rows for x in r.fired}):
        fired = [r for r in lab if d in r.fired]
        hits = sum(r.label or 0 for r in fired)
        trades = [t for r in rows for t in r.trades if t[0] == d]
        det[d] = {
            "jours_declenches": len([r for r in rows if d in r.fired]),
            "frequence": round(len([r for r in rows if d in r.fired]) / len(rows), 5) if rows else None,
            "precision_hausse": _share(hits, len(fired)),
            "lift": round((hits / len(fired)) / base, 2) if fired and base else None,
            "politique": meta.get("politique", {}).get(d, "?"),
            "ordres_executes": len(trades),
            "r_net_cto": _mean_ci([t[1] for t in trades if t[1] is not None]),
            "r_net_pea": _mean_ci([t[2] for t in trades if t[2] is not None]),
        }
    rep["controle"]["detecteurs"] = det
    rep["controle"]["production"] = {st: _share(sum(1 for r in rows if r.prod_stage == st), len(rows)) for st in STAGES}
    lifts: dict[str, Any] = {}
    for feat in FEATURES:
        table = decile_lift([r.features.get(feat) for r in lab], [r.label for r in lab])
        if table:
            lifts[feat] = [
                {
                    "de": round(t.lo, 4),
                    "a": round(t.hi, 4),
                    "n": t.n,
                    "taux": round(t.rate, 4),
                    "lift": round(t.lift, 2),
                }
                for t in table
            ]
    rep["controle"]["lift_par_decile"] = lifts
    return rep


def _pct(x: Any) -> str:
    return "—" if x is None else f"{x * 100:.1f} %"


def _mci(d: dict[str, Any]) -> str:
    if not d.get("n"):
        return "—"
    ci = d.get("ic95")
    return f"{d['moyenne']:+.2f}" + (f" [{ci[0]:+.2f} ; {ci[1]:+.2f}]" if ci else "") + f" (n {d['n']})"


def render_fr(rep: dict[str, Any]) -> str:
    m = rep["meta"]
    p = m["parametres"]
    L: list[str] = []
    w = L.append
    w("ÉTUDE DES HAUSSES MANQUÉES (docs/34) — exploratoire, aucun verdict de rentabilité")
    w(
        f"Période {m['debut']} → {m['fin']} · places {', '.join(m['groupes'])} (New York, Londres, Zurich exclues : enregistrement n° 2)"
    )
    w(
        f"{m['valeurs']} valeurs · {m['hausses_reperees']} hausses repérées, {m['hausses_rejouees']} rejouées · contrôle : {m['valeurs_controle']} valeurs, {m['jours_controle']} séances · graine {m['graine']}"
    )
    w(
        f"Hausse : ≥ {p['min_rise']:.0%} et ≥ {p['sigma_k']:g} σ·√durée, zigzag max({p['reversal_min']:.0%} ; {p['reversal_atr']:g} ATR). "
        f"Hausse exploitable (contrôle) : depuis l'ouverture suivante, +max({p['min_rise']:.0%} ; {p['sigma_k']:g} σ·√{p['horizon']}) avant −{p['stop_atr']:g} ATR en {p['horizon']} séances."
    )
    w(
        "Non rejoué : lettres, nouvelles, fondamentaux, plafonds de portefeuille, coupe-circuit, niveau de risque ; "
        "portail : lettres supposées favorables ; capitalisation et éligibilité PEA d'aujourd'hui ; valeurs radiées européennes absentes (biais de survie)."
    )
    pr = rep["profil"]
    fp, fa = rep["entonnoir_production"], rep["entonnoir_tous_detecteurs"]
    w("")
    w("EN BREF")
    w(f"  hausses sans aucun détecteur déclenché (J−2 à J+5) : {_pct(fa['aucun_signal']['part'])}")
    w(
        f"  hausses arrêtées par la politique (détecteurs désactivés ou en surveillance) : {_pct(fp['politique']['part'])}"
    )
    w(
        f"  hausses avec un ordre exécuté : production {_pct(fp['ordre_execute']['part'])} · tous détecteurs actifs {_pct(fa['ordre_execute']['part'])}"
    )
    w(
        f"  part de la hausse encore à venir après la première clôture haussière (médiane) : {_pct(pr['part_restant_apres_premiere_cloture_haussiere'])}"
    )
    w(
        f"  taux de base d'une hausse exploitable sur une séance quelconque : {_pct(rep['controle']['taux_de_base']['part'])}"
    )
    w("")
    w("1. PROFIL DES HAUSSES")
    w(f"  par groupe : {pr['par_groupe']} · par taille : {pr['par_taille']} · par forme : {pr['par_forme']}")
    w(
        f"  par volatilité : {pr['par_volatilite']} · durée médiane {pr['duree_mediane_seances']} séances · hausse médiane {_pct(pr['hausse_mediane'])}"
    )
    w(
        f"  part de la hausse restant après la première clôture haussière (médiane) : {_pct(pr['part_restant_apres_premiere_cloture_haussiere'])}"
    )
    for k, v in pr["debut_de_hausse"].items():
        w(f"  au premier jour de hausse — {k} : {_pct(v['part'])} (n {v['n']})")
    for title, key in (
        ("2. ENTONNOIR — OUTIL TEL QU'EN PRODUCTION", "entonnoir_production"),
        ("3. ENTONNOIR — TOUS LES DÉTECTEURS ACTIFS (coût de la politique)", "entonnoir_tous_detecteurs"),
    ):
        w("")
        w(title + " : étape la plus avancée atteinte entre J−2 et J+5 du creux")
        for st, v in rep[key].items():
            w(f"  {STAGE_FR[st]:<75} {_pct(v['part']):>8}  IC95 [{_pct(v['ic95'][0])} ; {_pct(v['ic95'][1])}]")
    w("")
    w("4. PAR STRATE (part des hausses avec un ordre exécuté : production / tous détecteurs ; aucun signal)")
    for name, sub in rep["par_strate"].items():
        for val, v in sub.items():
            w(
                f"  {name} {val:<22} n {v['n']:>6} · {_pct(v['ordre_execute_production']['part'])} / {_pct(v['ordre_execute_tous']['part'])} · aucun signal {_pct(v['aucun_signal']['part'])}"
            )
    w("")
    w("5. DÉTECTEURS SUR LES HAUSSES (tous actifs) et délai du premier signal (séances après le creux)")
    for d, v in rep["detecteurs_sur_les_hausses"].items():
        w(f"  {d:<22} se déclenche sur {_pct(v['declenche']['part'])} des hausses")
    w(f"  délai : {rep['delai_premier_signal']}")
    o = rep["ordres_sur_les_hausses"]
    w(
        f"  ordres exécutés (tous actifs) : part de la hausse capturée {_mci(o['part_capturee'])} · R net CTO {_mci(o['r_net_cto'])} · R net PEA {_mci(o['r_net_pea'])}"
    )
    pr2 = rep.get("paris_portail_restreint") or {}
    if pr2.get("n"):
        w(
            f"  Paris : sur {pr2['n']} hausses avec un ordre exécuté en production, {_pct(pr2['part'])} le resteraient si les lettres "
            "ne validaient pas le marché (portail restreint : ratio net ≥ 2,5 et score ≥ 75, R10)"
        )
    c = rep["controle"]
    w("")
    w("6. CONTRÔLE (séances ordinaires, sans regarder la suite)")
    w(f"  taux de base d'une hausse exploitable : {_pct(c['taux_de_base']['part'])} (n {c['taux_de_base']['n']})")
    for v, x in c["taux_de_base_par_volatilite"].items():
        w(f"    volatilité {v:<20} {_pct(x['part'])} (n {x['n']})")
    w(
        "  par détecteur (tous actifs) : séances où il se déclenche · précision (hausse exploitable ensuite) · lift · R net par ordre exécuté CTO / PEA"
    )
    for d, v in c["detecteurs"].items():
        w(
            f"    {d:<22} {_pct(v['frequence'])} · {_pct(v['precision_hausse']['part'])} · lift {v['lift']} · CTO {_mci(v['r_net_cto'])} / PEA {_mci(v['r_net_pea'])} — {v['politique']}"
        )
    w(
        "  étape atteinte par séance (production) : "
        + ", ".join(f"{st} {_pct(x['part'])}" for st, x in c["production"].items() if x["k"])
    )
    w("")
    w(
        "7. LIFT DES CARACTÉRISTIQUES (contrôle) — décile le plus bas et le plus haut ; exploratoire, à confirmer par un enregistrement pré-déclaré"
    )
    for feat, table in c["lift_par_decile"].items():
        lo, hi = table[0], table[-1]
        best = max(table, key=lambda t: t["lift"])
        w(
            f"  {feat:<26} bas [{lo['de']} ; {lo['a']}] lift {lo['lift']} · haut [{hi['de']} ; {hi['a']}] lift {hi['lift']} · meilleur [{best['de']} ; {best['a']}] lift {best['lift']} (n {best['n']})"
        )
    w("")
    w(
        "Lecture : un lift > 1 dit qu'une caractéristique précède plus souvent une hausse exploitable ; il ne dit rien du gain net. "
        "Seul un enregistrement pré-déclaré, testé sur des données jamais lues, peut changer les règles (docs/31, docs/34)."
    )
    return "\n".join(L)


def write(rep: dict[str, Any], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    j, t = out_dir / f"hausses_{stamp}.json", out_dir / f"hausses_{stamp}.txt"
    j.write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    t.write_text(render_fr(rep), encoding="utf-8")
    return j, t
