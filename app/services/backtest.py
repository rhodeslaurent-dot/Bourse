# ruff: noqa: E501
"""docs/27 — backtest of the detectors at full cost on the stored daily history.

Replays, session by session, exactly the production rules: detectors (same parameters), zones, admission
(maximum entry price, R4), limit order valid ``entry.expiry_sessions``, slipped stop, first exploitable
target, time stop; costs of the cash account (commission, TTF, change). Statistics by detector, status at
the signal, regime, year, and in-sample / out-of-sample halves. Known biases are written in the run's notes
and shown with every figure (survivorship, no historical fundamentals or letters, unadjusted prices)."""

from __future__ import annotations

import logging
import statistics
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import LoadedConfig
from app.db.models import BacktestRun, BacktestTrade, Instrument, PriceEod
from app.domain.admission import AdmissionInput, Status, admit
from app.domain.backtest import simulate_trade, summarize
from app.domain.costs import CostParams, ftt_rate_any, round_trip_fee_fraction
from app.domain.detectors import DETECTORS, DetectorContext
from app.domain.indicators import Bar, compute_daily_features, sma
from app.domain.zones import detect_zones

log = logging.getLogger("bourse.backtest")
HOLDING_DAYS = {"days": 5, "swing_2_6w": 20, "semaines": 40}
CA_JUMP = 0.2  # change of the adjustment factor (adj_close / close) between two sessions → corporate action


@dataclass
class History:
    days: list[date]
    bars: list[Bar]
    ca_index: set[int] = field(default_factory=set)  # sessions after a split / corporate action


def load_history(s: Session, isins: list[str], start: date, end: date, lookback_days: int = 420) -> dict[str, History]:
    out: dict[str, History] = {}
    lo = start - timedelta(days=lookback_days)
    for isin in isins:
        rows = list(
            s.scalars(
                select(PriceEod)
                .where(PriceEod.isin == isin, PriceEod.day >= lo, PriceEod.day <= end)
                .order_by(PriceEod.day)
            )
        )
        if len(rows) < 260:
            continue
        h = History([r.day for r in rows], [Bar(r.open, r.high, r.low, r.close, r.volume) for r in rows])
        prev = None
        for k, r in enumerate(rows):
            f = (r.adj_close / r.close) if r.adj_close and r.close else None
            if f and prev and abs(f / prev - 1) > CA_JUMP:
                h.ca_index.add(k)
            prev = f or prev
        out[isin] = h
    return out


def rs_ranks(hist: dict[str, History]) -> dict[tuple[str, date], float]:
    """Cross-sectional percentile (0–100) of the RS score (0.4·r12m + 0.2·r6m + 0.2·r3m + 0.2·r1m), as in
    features_daily, computed for every session with the population of that session."""
    import pandas as pd

    closes = pd.DataFrame({isin: pd.Series([b.close for b in h.bars], index=h.days) for isin, h in hist.items()})
    closes = closes.sort_index()
    r = lambda n: closes / closes.shift(n) - 1  # noqa: E731
    score = 0.4 * r(250) + 0.2 * r(125) + 0.2 * r(63) + 0.2 * r(21)
    ranks = score.rank(axis=1, pct=True) * 100
    out: dict[tuple[str, date], float] = {}
    for day, row in ranks.iterrows():
        for isin, v in row.items():
            if v == v:  # not NaN
                out[(str(isin), day)] = float(v)  # type: ignore[arg-type]
    return out


def regimes(hist: History | None) -> dict[date, str]:
    """« haussier » when the index closes above its 200-session average, else « baissier » (segmentation)."""
    if hist is None:
        return {}
    closes = [b.close for b in hist.bars]
    out = {}
    for k in range(200, len(closes)):
        m = sma(closes[: k + 1], 200)
        if m:
            out[hist.days[k]] = "haussier" if closes[k] > m else "baissier"
    return out


def _cost_params(config: LoadedConfig, inst: Instrument) -> CostParams:
    from app.services.engine import _fees

    p = config.params
    tax = (p.tax[0].model_dump() if p.tax and hasattr(p.tax[0], "model_dump") else {}) if p.tax else {}
    ttf = (
        float(tax.get("ttf_rate") or 0.004) if inst.isin.startswith("FR") and (inst.market_cap_eur or 0) >= 1e9 else 0.0
    )
    fx = float(((p.brokers or {}).get("saxo") or {}).get("fx_fee_pct") or 0) if inst.currency != "EUR" else 0.0
    ftt = ftt_rate_any((tax.get("ftt_by_country") or {}).get(inst.isin[:2]))
    return CostParams(_fees(config, "cto_cash"), ttf_rate=ttf, ftt_rate=ftt, fx_fee_pct=fx)


def run_backtest(
    s: Session,
    config: LoadedConfig,
    start: date,
    end: date,
    isins: list[str] | None = None,
    detectors: list[str] | None = None,
    progress: Callable[[int, int], None] | None = None,
    markets: str | None = None,
) -> BacktestRun:
    p = config.params
    det_params = {k: v for k, v in (p.detectors or {}).items() if k in DETECTORS and (not detectors or k in detectors)}
    det_params = {k: v for k, v in det_params.items() if v.get("enabled", False)}
    zp = getattr(p, "zones", None) or {}
    stop_p = p.risk.stop
    wick = float(getattr(stop_p, "wick_buffer_pct", 0.005) or 0.005)
    k_rr = float((p.admission or {}).get("min_reward_risk", 2.0))
    slip = float(p.risk.slippage_pct)
    expiry = int((getattr(p, "entry", None) or {}).get("expiry_sessions", 3))
    capital = float(p.capital.capital_pilote_eur)
    risk_budget = capital * float(p.risk.max_risk_per_trade_pct)
    insts = {
        i.isin: i
        for i in s.scalars(select(Instrument).where(Instrument.in_universe.is_(True)))
        if not isins or i.isin in isins
    }
    scope = markets or str(((config.raw or {}).get("backtest") or {}).get("markets") or "europe")
    if scope == "europe" and not isins:
        # New York, London and Zurich are the test data of registration n° 2 (docs/33): the backtest — weekly job
        # included — never reads them; only the European research groups P0 / P1 / P2 (as the run-2 reference)
        from app.services.engine import _calendars
        from app.services.research import mic_groups

        cals = _calendars()
        groups = mic_groups(config, cals) if cals is not None else {}
        insts = {k: v for k, v in insts.items() if v.mic in groups}
    hist = load_history(s, list(insts), start, end)
    ranks = rs_ranks(hist) if hist else {}
    from app.services.indices import EUROPE, index_specs

    specs = {x.market: x.id for x in index_specs(config)}  # docs/32: each value on its own venue's index
    idx_hist = load_history(s, sorted(set(specs.values())), start, end)
    reg_by_market = {m: regimes(idx_hist.get(i)) for m, i in specs.items()}
    run = BacktestRun(
        created_at=datetime.now(UTC), params_version=p.version, start=start, end=end, instruments=len(hist),
        settings={"detecteurs": sorted(det_params), "compte": "cto_cash", "slippage": slip, "validite_ordre": expiry,
                  "ratio_min": k_rr, "budget_risque_eur": risk_budget},
    )  # fmt: skip
    s.add(run)
    s.flush()
    excluded_ca = 0
    trades: list[BacktestTrade] = []
    for n_done, (isin, h) in enumerate(hist.items(), 1):
        if progress:
            progress(n_done, len(hist))
        inst = insts[isin]
        cp = _cost_params(config, inst)
        reg = reg_by_market.get(inst.mic or "") or reg_by_market.get(EUROPE) or {}
        busy_until: dict[str, int] = {}
        for i in range(259, len(h.bars) - 1):
            day = h.days[i]
            if day < start or day > end:
                continue
            window = h.bars[max(0, i - 279) : i + 1]
            ctx = DetectorContext(ranks.get((isin, day)), [], wick, float(stop_p.buffer_atr_mult))
            setups = []
            for name, dp in det_params.items():
                if busy_until.get(name, -1) >= i:
                    continue
                found = DETECTORS[name](dp).detect(window, ctx)
                if found:
                    f = compute_daily_features(window)
                    zones = detect_zones(
                        window[-int(zp.get("lookback_sessions", 250)) :], f.atr14 or 0.0,
                        int(zp.get("pivot_window", 5)), float(zp.get("merge_atr_mult", 0.5)), int(zp.get("min_touches", 2)),
                    )  # fmt: skip
                    zctx = DetectorContext(ctx.rs_rank, zones, ctx.wick_buffer_pct, ctx.stop_buffer_atr_mult)
                    setups += [(name, dp, x) for x in DETECTORS[name](dp).detect(window, zctx)]
            for name, dp, st in setups:
                if not st.targets:
                    continue  # « pas d'objectif exploitable »: WATCH in production, never a trade
                target = st.targets[0]
                notional = min(
                    capital * float(p.risk.max_position_pct_of_capital),
                    risk_budget / max(1e-9, (st.p_max_technical - st.stop) / st.p_max_technical),
                )
                frac = round_trip_fee_fraction(notional, cp)
                adm = admit(
                    AdmissionInput(
                        window[-1].close, target, st.stop, st.kind, st.level_low, frac, k_rr, st.p_max_technical
                    )
                )
                if adm.status in (Status.DEGRADED, Status.NOT_TESTABLE) or adm.p_max is None:
                    continue
                hold = int(dp.get("time_stop_sessions") or HOLDING_DAYS.get(st.horizon, 20))
                after = h.bars[i + 1 :]
                res = simulate_trade(after, adm.p_max, st.stop, target, slip, expiry, hold)
                if res is None:
                    busy_until[name] = i + expiry
                    continue
                a, b = i + 1 + res.entry_index, i + 1 + res.exit_index
                busy_until[name] = b
                if any(a <= k <= b for k in h.ca_index):
                    excluded_ca += 1
                    continue
                trades.append(
                    BacktestTrade(
                        run_id=run.id, isin=isin, detector=name, signal_day=day, entry_day=h.days[a], exit_day=h.days[b],
                        entry=res.entry, exit=res.exit, stop=st.stop, target=target, reason=res.reason,
                        status="admis" if adm.status == Status.ADMITTED else "refuse", warned=bool(st.warnings),
                        regime=reg.get(day), r_gross=round(res.gross_r, 4), r_net=round(res.net_r(frac), 4),
                        mfe_r=round(res.mfe_r, 4), mae_r=round(res.mae_r, 4), sessions=res.exit_index - res.entry_index + 1,
                    )
                )  # fmt: skip
    s.add_all(trades)
    run.summary = summarize_run(trades, start, end)
    run.summary["portefeuille"] = portfolio_summary(trades, config, start, end)
    run.notes = [
        "biais de survie : univers actuel (les sociétés radiées depuis sont absentes) — résultats optimistes",
        "sans fondamentaux, lettres ni consensus historiques : ni bloc F, ni portail des lettres, ni score d'admission",
        "objectif des analystes non disponible dans le passé : seuls les objectifs techniques sont testés",
        f"{excluded_ca} trade(s) exclu(s) : opération sur titres (division…) pendant la position, cours non ajustés",
        "compte comptant (frais du CTO), avant impôt ; une règle « découverte » exige t ≥ 3 (Harvey, Liu & Zhu)",
    ]
    s.flush()
    return run


def portfolio_summary(trades: list[BacktestTrade], config: LoadedConfig, start: date, end: date) -> dict[str, Any]:
    """docs/27 §27.7 — the production trades replayed as a portfolio (open-risk cap ÷ risk per trade
    concurrent trades), without and with the rolling circuit breaker, on the whole run and the control period.
    Adoption rule for a guard: max drawdown −20 % or better for at most 10 % of the yearly R lost."""
    from app.domain.backtest import BreakerRule, ReplayTrade, portfolio_replay

    risk = config.params.risk
    caps = getattr(risk, "max_open_risk_pct_by_gate", None) or {"ouvert": 0.05}
    slots = max(1, int(float(caps.get("ouvert", 0.05)) / float(risk.max_risk_per_trade_pct) + 1e-9))
    extra = risk.circuit_breaker.model_extra or {}
    rule = BreakerRule(
        float(extra.get("loss_5_r", 4)), float(extra.get("loss_21_r", 8)), float(extra.get("peak_reduce_r", 10)),
        float(extra.get("peak_block_r", 12)), float(extra.get("peak_reduce_multiplier", 0.75)),
    )  # fmt: skip
    mid = start + (end - start) * 3 / 5
    prod = [t for t in trades if t.status == "admis" and not t.warned]
    out: dict[str, Any] = {"lignes_simultanees": slots, "note": "R réalisé seulement (latent ignoré), 1 R par trade"}
    for name, sel in (("tout", prod), ("controle", [t for t in prod if t.signal_day >= mid])):
        rt = [ReplayTrade(t.entry_day, t.exit_day, t.r_net) for t in sel]
        if not rt:
            continue
        a, b = portfolio_replay(rt, slots), portfolio_replay(rt, slots, rule)
        verdict = None
        if a.max_drawdown_r < 0 and a.r_per_year:
            dd_gain = 1 - b.max_drawdown_r / a.max_drawdown_r
            r_loss = 1 - (b.r_per_year or 0) / a.r_per_year if a.r_per_year > 0 else None
            verdict = "adopter" if dd_gain >= 0.2 and r_loss is not None and r_loss <= 0.1 else "non démontré"
        out[name] = {"sans_coupe_circuit": a.as_dict(), "avec_coupe_circuit": b.as_dict(), "verdict_garde_fou": verdict}
    return out


def summarize_run(trades: list[BacktestTrade], start: date, end: date) -> dict[str, Any]:
    """Statistics of the net R by segment; ``essais`` counts the cells reported (multiple testing)."""
    mid = start + (end - start) * 3 / 5
    cells: dict[str, list[BacktestTrade]] = defaultdict(list)
    for t in trades:
        clean = t.status == "admis" and not t.warned
        keys = [f"detecteur:{t.detector}", f"detecteur_statut:{t.detector}:{t.status}{':avertissement' if t.warned else ''}",
                f"annee:{t.detector}:{t.signal_day.year}", f"periode:{t.detector}:{'apprentissage' if t.signal_day < mid else 'controle'}"]  # fmt: skip
        if t.regime:
            keys.append(f"regime:{t.detector}:{t.regime}")
        if clean:
            keys.append(f"production:{t.detector}")  # what the engine would actually propose (admitted, no warning)
            keys.append(f"production_{'apprentissage' if t.signal_day < mid else 'controle'}:{t.detector}")
        for k in keys:
            cells[k].append(t)
    out: dict[str, Any] = {"trades": len(trades), "essais": len(cells), "segments": {}}
    for k, ts in sorted(cells.items()):
        sm = summarize([t.r_net for t in ts], [t.sessions for t in ts]).as_dict()
        sm["mfe_median_r"] = round(sorted(t.mfe_r for t in ts)[len(ts) // 2], 3)
        sm["mae_median_r"] = round(sorted(t.mae_r for t in ts)[len(ts) // 2], 3)
        sm["sorties"] = {r: sum(t.reason == r for t in ts) for r in ("target", "stop", "gap_stop", "time")}
        out["segments"][k] = sm
    return out


def prior_expected_r(s: Session, detector: str, shrinkage_k: int = 20) -> tuple[float, int] | None:
    """Prior expected net R of the detector while the live history is insufficient (docs/27 §27.5, revised
    after the 25/10/2026 review): **control period only** (out of the learning sample), production conditions,
    used only if the control interval is above 0 and the learning period has the same sign; then **halved**
    (survivorship, multiple testing) and weighted as at most 30 trades: 0.5 × μ × min(n, 30) / (min(n, 30) + k)."""
    run = s.scalar(select(BacktestRun).order_by(BacktestRun.id.desc()))
    segs = ((run.summary or {}).get("segments") or {}) if run else {}
    ctl, app = segs.get(f"production_controle:{detector}"), segs.get(f"production_apprentissage:{detector}")
    if not ctl or not ctl.get("n") or ctl.get("mean_r") is None or ctl.get("ci_low") is None:
        return None
    if ctl["ci_low"] <= 0 or not app or (app.get("mean_r") or 0) <= 0:
        return None  # not robust out of sample: no prior (the ranking stays « historique insuffisant »)
    n = int(ctl["n"])
    w = min(n, 30)
    return 0.5 * float(ctl["mean_r"]) * w / (w + shrinkage_k), n


def diagnostics(s: Session, run_id: int | None = None) -> dict[str, Any]:
    """Why a detector wins or loses, from the stored trades of a run (production conditions: admitted, no
    warning): gross R (the edge before costs) vs net R (after costs), how trades end, how far they went in
    the trade's favour before ending (MFE) and against it (MAE), the planned reward / risk, and the result by
    market regime. Facts for a decision on the exits — no parameter is changed here."""
    run = s.get(BacktestRun, run_id) if run_id else s.scalar(select(BacktestRun).order_by(BacktestRun.id.desc()))
    if run is None:
        return {}
    rows = list(
        s.scalars(
            select(BacktestTrade).where(
                BacktestTrade.run_id == run.id, BacktestTrade.status == "admis", BacktestTrade.warned.is_(False)
            )
        )
    )
    by: dict[str, list[BacktestTrade]] = defaultdict(list)
    for t in rows:
        by[t.detector].append(t)

    def med(xs: list[float]) -> float | None:
        return round(statistics.median(xs), 2) if xs else None

    out: dict[str, Any] = {"run": run.id, "start": run.start.isoformat(), "end": run.end.isoformat(), "detecteurs": {}}
    for det, ts in sorted(by.items()):
        n = len(ts)
        planned = [(t.target - t.entry) / (t.entry - t.stop) for t in ts if t.entry > t.stop]
        losers = [t for t in ts if t.r_net < 0]
        regimes_: dict[str, list[float]] = defaultdict(list)
        for t in ts:
            regimes_[t.regime or "inconnu"].append(t.r_net)
        out["detecteurs"][det] = {
            "n": n,
            "r_brut_moyen": round(statistics.fmean(t.r_gross for t in ts), 3),
            "r_net_moyen": round(statistics.fmean(t.r_net for t in ts), 3),
            "cout_moyen_r": round(statistics.fmean(t.r_gross - t.r_net for t in ts), 3),
            "sorties": {
                r: round(sum(t.reason == r for t in ts) / n, 2) for r in ("target", "stop", "gap_stop", "time")
            },
            "gain_vise_r_median": med(planned),
            "mfe_median_r": med([t.mfe_r for t in ts]),
            "mae_median_r": med([t.mae_r for t in ts]),
            "part_mfe_1r": round(sum(t.mfe_r >= 1 for t in ts) / n, 2),
            "perdants_passes_par_1r": round(sum(t.mfe_r >= 1 for t in losers) / len(losers), 2) if losers else None,
            "duree_mediane": med([float(t.sessions) for t in ts]),
            "par_regime": {k: {"n": len(v), "r_net_moyen": round(statistics.fmean(v), 3)} for k, v in regimes_.items()},
        }
    return out
