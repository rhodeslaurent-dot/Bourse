# ruff: noqa: E501 — long French report lines
"""Portfolio review (docs/22 §22.5, lot 2.5): every open position reviewed with the exit rules
S1–S6, the stop rules (invariant > placement > ratchet) and the events a stop does not cover;
then the book's synthesis: risk caps A/B, exposure by account / sector / country / currency, idle
cash (R20), sanctuarised DCA pocket (R17), tax-harvest positions (R18), order book and the five best
new ideas of the day. Deterministic; the review is stored once written (never corrected)."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import LoadedConfig
from app.db.models import (
    Account,
    FeatureDaily,
    Instrument,
    MacroEvent,
    PortfolioReview,
    PositionRow,
    PriceEod,
    Proposal,
    Signal,
)
from app.domain.exits import ExitParams, PositionFacts, review_position
from app.domain.heat import HeatLine, HeatParams, heat_state
from app.domain.indicators import compute_daily_features, sma
from app.domain.stops import Zone
from app.domain.timeutil import PARIS
from app.domain.zones import detect_zones
from app.services.instruments import name_of
from app.services.market_data import fx_to_eur, load_bars

log = logging.getLogger("bourse.review")
ACCOUNT_LABELS = {"pea": "PEA", "cto_cash": "CTO comptant", "cto_srd": "CTO SRD"}


def exit_params(config: LoadedConfig) -> ExitParams:
    p = config.params
    ex = (p.detectors or {}).get("exits") or {}
    stop = p.risk.stop
    return ExitParams(
        reduce_at_target1_pct=float(stop.reduce_at_target1_pct),
        time_stop_sessions=int(ex.get("time_stop_sessions_default", 40)),
        rs_rank_exit=float(ex.get("rs_rank_exit", 50)),
        thesis_mm20_days=int(ex.get("thesis_mm20_close_below_days", 2)),
        earnings_blackout_hours=float(p.risk.earnings_blackout_hours),
        earnings_policy=str(p.risk.earnings_policy_on_position),
        wick_buffer_pct=float(getattr(stop, "wick_buffer_pct", None) or 0.005),
        pyramiding_min_gain_r=float((p.risk.pyramiding or {}).get("min_gain_r", 1.0)),
        event_stop_distance_pct=float((getattr(p, "events", None) or {}).get("stop_distance_pct", 0.02)),
    )


def _pockets(config: LoadedConfig) -> tuple[set[str], dict[str, str]]:
    pk = getattr(config.params, "pockets", None) or {}
    dca = set((pk.get("dca") or {}).get("isins") or [])
    tax = {str(x.get("isin")): str(x.get("target_date") or "") for x in (pk.get("tax_harvest") or []) if x.get("isin")}
    return dca, tax


def _origin(s: Session, isin: str) -> tuple[list[float], str | None]:
    """Targets and detector of the proposal the position comes from (latest taken one)."""
    p = s.scalar(select(Proposal).where(Proposal.isin == isin, Proposal.status == "taken").order_by(Proposal.id.desc()))
    if p is None:
        return [], None
    sig = s.get(Signal, p.signal_id)
    return [float(t) for t in (p.targets or [])], (sig.detector if sig else None)


def _next_macro_hours(s: Session, now: datetime, keywords: list[str]) -> float | None:
    for m in s.scalars(
        select(MacroEvent)
        .where(MacroEvent.ts >= now, MacroEvent.ts <= now + timedelta(days=3), MacroEvent.impact == "High")
        .order_by(MacroEvent.ts)
    ):
        if any(k.lower() in m.title.lower() for k in keywords):
            ts = m.ts.replace(tzinfo=UTC) if m.ts.tzinfo is None else m.ts
            return (ts - now).total_seconds() / 3600
    return None


def _media_sells(s: Session, isin: str, now: datetime) -> list[str]:
    """docs/23 §23.7: a sale cited by a media source on a held value (sales carry more information than
    buys — Kerl-Walter, Lidén): shown, never acted on automatically."""
    from app.services.media import recent

    return [
        f"vente citée par {m.source} le {m.published_at:%d/%m}" + (f" ({m.speaker})" if m.speaker else "")
        for m in recent(s, isin, now, 28)
        if m.stance == "vente"
    ][:3]


def review_lines(s: Session, config: LoadedConfig, day: date, now: datetime) -> list[dict[str, Any]]:
    from app.services.engine import MACRO_KEYWORDS_DEFAULT

    params = exit_params(config)
    dca, tax = _pockets(config)
    keywords = list((getattr(config.params, "events", None) or {}).get("macro_keywords") or MACRO_KEYWORDS_DEFAULT)
    macro_h = _next_macro_hours(s, now, keywords)
    accounts = {a.id: a for a in s.scalars(select(Account))}
    zp = getattr(config.params, "zones", None) or {}
    out = []
    for pos in s.scalars(select(PositionRow).where(PositionRow.closed_at.is_(None), PositionRow.qty_held > 0)):
        inst = s.get(Instrument, pos.isin)
        bars = load_bars(s, pos.isin, end=day, limit=260)
        acc = accounts.get(pos.account_id)
        line: dict[str, Any] = {
            "isin": pos.isin,
            "nom": name_of(pos.isin),
            "compte": ACCOUNT_LABELS.get(acc.type, acc.type) if acc else "?",
            "quantite": pos.qty_held,
            "pru": pos.avg_price,
            "stop": pos.stop_current,
            "protection": pos.protection_state,
        }
        if pos.isin in dca:
            line.update(
                {"poche": "dca", "verdict": "SANCTUARISÉE", "signaux": ["poche d'investissement progressif (R17)"]}
            )
            out.append(line)
            continue
        if pos.isin in tax:
            line.update(
                {
                    "poche": "fiscale",
                    "verdict": "CESSION FISCALE",
                    "signaux": [
                        f"moins-value conservée pour être cristallisée le {tax[pos.isin] or 'date à fixer'} (R18)"
                    ],
                }
            )
            out.append(line)
            continue
        if not bars:
            line.update({"verdict": "À VÉRIFIER", "signaux": ["aucun cours stocké : revue impossible"]})
            out.append(line)
            continue
        try:
            closes = [b.close for b in bars]
            below = 0
            for k in range(1, 6):
                if len(closes) >= 20 + k - 1:
                    m = sma(closes[: len(closes) - k + 1], 20)
                    if m is not None and closes[-k] < m:
                        below += 1
                        continue
                break
            f = compute_daily_features(bars)
            zones = detect_zones(
                bars,
                f.atr14 or 0.0,
                int(zp.get("pivot_window", 5)),
                float(zp.get("merge_atr_mult", 0.5)),
                int(zp.get("min_touches", 2)),
            )
            targets, detector = _origin(s, pos.isin)
            opened = (
                pos.opened_at.replace(tzinfo=UTC) if pos.opened_at and pos.opened_at.tzinfo is None else pos.opened_at
            )
            held = (
                len(
                    list(s.scalars(select(PriceEod.day).where(PriceEod.isin == pos.isin, PriceEod.day > opened.date())))
                )
                if opened
                else 0
            )
            feats = s.scalar(
                select(FeatureDaily).where(FeatureDaily.isin == pos.isin).order_by(FeatureDaily.day.desc())
            )
            earn_h = None
            if inst and inst.earnings_next_date:
                earn_h = (
                    datetime.combine(inst.earnings_next_date, datetime.min.time(), tzinfo=PARIS) - now
                ).total_seconds() / 3600
            rv = review_position(
                PositionFacts(
                    entry_price=pos.avg_price,
                    stop=pos.stop_current,
                    stop_initial=pos.stop_initial,
                    close=closes[-1],
                    closes_below_mm20=below,
                    rs_rank=feats.rs_rank if feats else None,
                    targets=targets,
                    sessions_held=held,
                    detector=detector,
                    obstacles=[z.high for z in zones if z.low > pos.avg_price],
                    supports=[Zone(z.low, z.high) for z in zones if z.high < closes[-1]],
                    earnings_in_hours=earn_h,
                    macro_event_in_hours=macro_h,
                ),
                params,
            )
            line.update(
                {
                    "cours": closes[-1],
                    "gain_pct": round(rv.gain_pct, 4),
                    "r": round(rv.r_multiple, 2) if rv.r_multiple is not None else None,
                    "verdict": rv.verdict,
                    "alleger_pct": rv.reduce_pct,
                    "stop_propose": round(rv.stop.stop, 4) if rv.stop else None,
                    "stop_change": bool(rv.stop and rv.stop.changed),
                    "stop_raison": rv.stop.reason if rv.stop else None,
                    "signaux": rv.signals + _media_sells(s, pos.isin, now),
                    "notes": rv.notes + (rv.stop.notes if rv.stop else []),
                    "objectifs": targets,
                    "seances": held,
                }
            )
        except Exception as exc:  # noqa: BLE001 — one bad position never stops the review
            log.warning("revue : %s ignorée (%s)", pos.isin, exc.__class__.__name__, exc_info=True)
            line.update({"verdict": "À VÉRIFIER", "signaux": [f"revue impossible ({exc.__class__.__name__})"]})
        out.append(line)
    return out


def synthesis(
    s: Session, config: LoadedConfig, day: date, now: datetime, lines: list[dict[str, Any]]
) -> dict[str, Any]:
    from app.services.engine import todays_ranked
    from app.services.macro import market_context, series

    p = config.params
    cap = float(p.capital.capital_pilote_eur)
    risk = p.risk
    ctxm = market_context(s, config, day)
    hp = HeatParams(
        cap,
        dict(getattr(risk, "max_open_risk_pct_by_gate", None) or {"ouvert": 0.05, "restreint": 0.035}),
        float(getattr(risk, "max_line_risk_pct_of_cap", 0.30)),
        float(getattr(risk, "reserved_signal_budget_pct", 0.0075)),
        int(getattr(risk, "reserved_budget_age_sessions", 10)),
        float(getattr(risk, "srd_risk_weight", 1.25)),
        float(getattr(risk, "heat_a_soft_cap_pct", 0.075)),
    )
    accounts = {a.id: a for a in s.scalars(select(Account))}
    dca, tax = _pockets(config)
    heat_lines, expo = [], {"compte": {}, "secteur": {}, "pays": {}, "devise": {}}
    for pos in s.scalars(select(PositionRow).where(PositionRow.closed_at.is_(None), PositionRow.qty_held > 0)):
        inst = s.get(Instrument, pos.isin)
        fx = fx_to_eur(s, pos.currency) or 1.0
        price = pos.last_price or pos.avg_price
        acc = accounts.get(pos.account_id)
        excluded = "dca" if pos.isin in dca else "tax_harvest" if pos.isin in tax else None
        heat_lines.append(
            HeatLine(
                pos.isin,
                pos.qty_held,
                pos.avg_price,
                price,
                pos.stop_current,
                fx,
                srd=bool(acc and acc.type == "cto_srd"),
                excluded=excluded,
            )  # fmt: skip
        )
        value = pos.qty_held * price * fx
        for k, v in (
            ("compte", ACCOUNT_LABELS.get(acc.type, "?") if acc else "?"),
            ("secteur", (inst.sector if inst else None) or "inconnu"),
            ("pays", pos.isin[:2]),
            ("devise", pos.currency),
        ):
            expo[k][v] = round(expo[k].get(v, 0.0) + value, 2)
    gate_key = "ouvert" if ctxm.gate == "ouvert" else "restreint"
    st = heat_state(heat_lines, 0.0, gate_key, hp)
    cash = {ACCOUNT_LABELS.get(a.type, a.type): a.cash for a in accounts.values()}
    msci = series(s, "msci_world", day, limit=260)
    dd = (msci[-1].value / max(x.value for x in msci) - 1) if msci else None
    acc_pct = float(((getattr(p, "pockets", None) or {}).get("dca") or {}).get("acceleration_drawdown_pct", 0.15))
    ideas = [
        {"rang": x.rank, "isin": x.isin, "nom": name_of(x.isin), "action": x.action, "compte": x.account_recommended}
        for x in todays_ranked(s, day)
        if x.rank is not None
    ][:5]
    verdicts: dict[str, int] = {}
    for ln in lines:
        verdicts[ln["verdict"]] = verdicts.get(ln["verdict"], 0) + 1
    return {
        "positions": len(lines),
        "verdicts": verdicts,
        "risque": {
            "convention_b_eur": round(st.heat_b_eur, 2),
            "plafond_eur": round(st.cap_eur, 2),
            "portail": ctxm.gate,
            "convention_a_eur": round(st.heat_a_eur, 2),
            "plafond_souple_a_eur": round(st.heat_a_soft_cap_eur, 2),
            "alertes": st.alerts,
        },
        "exposition": expo,
        "tresorerie": cash,
        "tresorerie_note": "trésorerie non investie : envisager un placement monétaire (aucun produit proposé, R20)",
        "poche_dca": {
            "isins": sorted(dca),
            "msci_world_repli_pct": round(dd, 4) if dd is not None else None,
            "acceleration": bool(dd is not None and dd <= -acc_pct),
        },
        "positions_fiscales": tax,
        "idees_du_jour": ideas,
        "contexte": {"risque_conjoncturel": ctxm.level, "portail": ctxm.gate, "raison": ctxm.gate_reason},
    }


def run_review(
    s: Session, config: LoadedConfig, trigger: str = "manual", now: datetime | None = None
) -> PortfolioReview:
    now = now or datetime.now(UTC)
    day = now.astimezone(PARIS).date()
    lines = review_lines(s, config, day, now)
    rev = PortfolioReview(
        ts=now, day=day, trigger=trigger, lines=_json(lines), synthesis=_json(synthesis(s, config, day, now, lines))
    )
    s.add(rev)
    s.flush()
    return rev


def telegram_summary(rev: PortfolioReview) -> list[str]:
    syn = rev.synthesis or {}
    out = [
        f"{syn.get('positions', 0)} position(s) : "
        + ", ".join(f"{k} {v}" for k, v in (syn.get("verdicts") or {}).items())
    ]
    for ln in rev.lines or []:
        if ln.get("verdict") in ("VENDRE", "ALLÉGER") or ln.get("stop_change"):
            stop = f" · stop → {ln['stop_propose']}" if ln.get("stop_change") else ""
            out.append(f"{ln['nom']} : {ln['verdict']}{stop}")
    r = syn.get("risque") or {}
    out.append(
        f"Risque ouvert {r.get('convention_b_eur')} € / plafond {r.get('plafond_eur')} € (portail {r.get('portail')})"
    )
    if (syn.get("poche_dca") or {}).get("acceleration"):
        out.append("Poche ETF : clause d'accélération déclenchée (MSCI World)")
    ideas = syn.get("idees_du_jour") or []
    if ideas:
        out.append("Idées : " + ", ".join(f"#{i['rang']} {i['nom']}" for i in ideas[:3]))
    out.append("Détail : page Revue de portefeuille")
    return out[:11]


def _json(obj: Any) -> Any:
    import json

    return json.loads(json.dumps(obj, default=str))
