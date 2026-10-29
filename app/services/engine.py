"""Proposal engine (docs/22 §22.4, lot 2.3): detectors → admission → full cost per account →
sizing → caps → market gate → events → score → ranking → proposal with a frozen snapshot.

Every number comes from stored data and dated parameters through the pure functions of
``app.domain`` (CLAUDE.md rule 3); a missing value stays missing and is shown as such (rule 2).
The engine never places an order (rule 1): it prepares limit-order cards at the maximum entry price.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import LoadedConfig
from app.db.models import (
    Account,
    FeatureDaily,
    Instrument,
    InstrumentAlias,
    MacroEvent,
    NewsItem,
    NewsletterItem,
    NewsletterValue,
    PositionRow,
    PriceEod,
    Proposal,
    Signal,
)
from app.domain.admission import AdmissionInput, Status, admit
from app.domain.admission import Setup as SetupKind
from app.domain.calendar import load_market_calendars, order_expiry_session
from app.domain.costs import (
    CostParams,
    alt_listing_saving,
    envelope_tax_rate,
    foreign_tax,
    net_outcome,
    round_trip_fee_fraction,
)
from app.domain.detectors import DetectorContext, Setup, run_detectors
from app.domain.events import EventParams, abs_gaps, event_exposure
from app.domain.heat import (
    HeatLine,
    HeatParams,
    OverrideParams,
    check_new_line,
    heat_state,
    override_eligible,
    override_fits,
)
from app.domain.indicators import compute_daily_features
from app.domain.ranking import RankItem, rank
from app.domain.risk import (
    EntryOrderKind,
    FeeSchedule,
    SizingInput,
    combine_size_factors,
    gap_risk_multiplier,
    size_position,
    stop_exec_for,
)
from app.domain.scoring import ScoreInput, score
from app.domain.timeutil import PARIS
from app.domain.zones import detect_zones
from app.services.decisions import create_proposal, expire_proposals, record_signal
from app.services.market_data import fx_to_eur, load_bars

log = logging.getLogger("bourse.engine")

HOLDING_DAYS = {"days": 5, "swing_2_6w": 20, "semaines": 40}
MACRO_KEYWORDS_DEFAULT = ["Rate", "CPI", "Non-Farm", "Monetary Policy", "Refinancing", "Federal Funds", "FOMC"]


def enabled(config: LoadedConfig) -> bool:
    p = config.params
    return bool(getattr(p, "admission", None) and p.detectors and p.scoring)


# --- inputs -------------------------------------------------------------------------------------


def _letter_levels(s: Session, isin: str, day: date, window_days: int = 5) -> list[dict[str, Any]]:
    """Testable letter setups (R15): explicit invalidation + numeric target written in a letter."""
    since = datetime.combine(day - timedelta(days=window_days), datetime.min.time(), tzinfo=UTC)
    out = []
    for v, it in s.execute(
        select(NewsletterValue, NewsletterItem)
        .join(NewsletterItem, NewsletterItem.id == NewsletterValue.item_id)
        .where(NewsletterValue.isin == isin, NewsletterItem.received_at >= since)
        .order_by(NewsletterItem.received_at.desc())
    ):
        lv = v.levels or {}
        if v.direction in ("buy", "watch"):
            out.append({"source": it.source, "item_id": it.id, "received_at": it.received_at, "levels": lv})
    return out


REGULATORY_TYPES = ("transaction_dirigeant", "franchissement_seuil", "rachat_actions")


def _insider_buy_days(s: Session, isin: str, now: datetime, window_days: int) -> int | None:
    """Days since the latest purchase by a manager of the company within the window (docs/23 §23.3)."""
    best: int | None = None
    for n in s.scalars(select(NewsItem).where(NewsItem.fetched_at >= now - timedelta(days=window_days))):
        c = n.classification or {}
        if isin not in (n.isins or []) or c.get("type") != "transaction_dirigeant" or c.get("direction") != "positif":
            continue
        ts = n.published_at or n.fetched_at
        ts = ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts
        days = max(0, (now - ts).days)
        best = days if best is None else min(best, days)
    return best


def _catalyst(s: Session, isin: str, now: datetime) -> tuple[str | None, bool, int | None]:
    best: tuple[str | None, bool, int | None] = (None, False, None)
    order = {"forte": 3, "moyenne": 2, "faible": 1}
    for n in s.scalars(select(NewsItem).where(NewsItem.fetched_at >= now - timedelta(days=5))):
        if isin not in (n.isins or []):
            continue
        c = n.classification or {}
        if c.get("direction") != "positif" or c.get("type") in REGULATORY_TYPES:
            continue  # a manager's purchase scores on its own (docs/23 §23.3), a buyback is a fact, not a catalyst
        mag = c.get("magnitude")
        if mag in order and order[mag] > order.get(best[0] or "", 0):
            ts = n.published_at or n.fetched_at
            ts = ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts
            best = (mag, True, (now - ts).days)
    return best


@dataclass
class Book:
    """Portfolio facts needed for the caps (positions and cash per account)."""

    lines: list[HeatLine]
    cash: dict[str, float | None]
    sectors: dict[str, int]


def _book(s: Session) -> Book:
    accounts = {a.id: a for a in s.scalars(select(Account))}
    lines, sectors = [], {}
    for p in s.scalars(select(PositionRow).where(PositionRow.closed_at.is_(None), PositionRow.qty_held > 0)):
        acc = accounts.get(p.account_id)
        opened = p.opened_at.replace(tzinfo=UTC) if p.opened_at and p.opened_at.tzinfo is None else p.opened_at
        age = (datetime.now(UTC) - opened).days * 5 // 7 if opened else 0
        lines.append(
            HeatLine(
                p.isin,
                p.qty_held,
                p.avg_price,
                p.last_price or p.avg_price,
                p.stop_current,
                fx_to_eur(s, p.currency) or 1.0,
                srd=(p.mode == "srd") or bool(acc and acc.type == "cto_srd"),
                age_sessions=age,
            )
        )
        inst = s.get(Instrument, p.isin)
        if inst and inst.sector:
            sectors[inst.sector] = sectors.get(inst.sector, 0) + 1
    cash = {a.type: a.cash for a in accounts.values()}
    return Book(lines, cash, sectors)


# --- evaluation ---------------------------------------------------------------------------------


@dataclass
class Evaluation:
    isin: str
    detector: str
    source: str
    setup: Setup
    action: str
    price: float
    price_status: str
    details: dict[str, Any] = field(default_factory=dict)
    blocked: list[str] = field(default_factory=list)
    passed: list[str] = field(default_factory=list)
    account: str | None = None
    shares: int = 0
    size_eur: float | None = None
    risk_eur: float | None = None
    net_rr: float | None = None
    net_gain_pct: float | None = None
    expected_r: float | None = None
    admitted: bool = False


def _fees(config: LoadedConfig, account: str) -> FeeSchedule:
    if account == "pea":  # BoursoBank: the grid of the current formula, PEA cap included
        from app.services.broker_plan import current_fees

        cur = current_fees(config, pea=True)
        if cur is not None:
            return cur
    brokers = config.params.brokers or {}
    name = "boursobank" if account == "pea" else "saxo"
    b = brokers.get(name) or {}
    pct = b.get("commission_pct")
    return FeeSchedule(
        name,
        float(pct) if isinstance(pct, int | float) else None,
        float(b.get("commission_min_eur") or 0),
        float(b["commission_cap_pct"]) if isinstance(b.get("commission_cap_pct"), int | float) else None,
    )


def _account_options(inst: Instrument, config: LoadedConfig) -> list[str]:
    pea = config.params.pea or {}
    out = []
    eligible_prefix = inst.isin[:2] in (pea.get("eligible_countries") or [])
    # a "domicile" presumption stored before 2026-10-23 came from the exchange country (AMD on Xetra → DE):
    # it only counts when the ISIN prefix agrees, until the next universe refresh recomputes it
    from_listing = (inst.pea_source or "").endswith("domicile") and not eligible_prefix
    if (inst.pea_eligible and not from_listing) or (inst.pea_eligible is None and eligible_prefix):
        out.append("pea")
    out.append("cto_cash")
    if inst.srd_status in ("complet", "long_only") and inst.mic == "XPAR":
        out.append("cto_srd")
    return out


def _tax(config: LoadedConfig) -> dict[str, Any]:
    tax = config.params.tax or []
    today = date.today()
    for t in tax:
        eff = getattr(t, "effective_from", None)
        if eff is None or (eff if isinstance(eff, date) else date.fromisoformat(str(eff))) <= today:
            return t.model_dump() if hasattr(t, "model_dump") else dict(t)
    return {}


def evaluate(
    s: Session,
    config: LoadedConfig,
    inst: Instrument,
    setup: Setup,
    source: str,
    price: float,
    price_status: str,
    book: Book,
    gate: str | None,
    regime: str | None,
    srd_mode: str | None,
    now: datetime,
    feats: FeatureDaily | None,
    fundamentals: Any | None = None,
    sector_rs_pct: float | None = None,
    circuit: Any | None = None,
    level: float = 1.0,
    ref: Any | None = None,
) -> Evaluation:
    p = config.params
    risk = p.risk
    adm_p = p.admission or {}
    k = float(adm_p.get("min_reward_risk", 2.0))
    tax = _tax(config)
    ev = Evaluation(inst.isin, setup.detector, source, setup, "WATCH", price, price_status)
    if setup.detector in ((config.params.scoring or {}).get("watch_only_detectors") or []):
        # ADR-013 (user decision of 26/10/2026): measured negative at full cost — first, so that no early return
        # (a limit waiting for a pullback, a missing target…) can leave it orderable
        ev.blocked.append(
            "WATCH : espérance historique négative au coût complet (backtest n° 2, docs/27) — surveillance seulement "
            "(ADR-013)"
        )
    fx = fx_to_eur(s, inst.currency) or (1.0 if inst.currency == "EUR" else None)
    if fx is None:
        ev.blocked.append(f"BLOQUÉ : taux de change {inst.currency} indisponible (fx_refresh)")
        ev.action = "NO_TRADE"
        return ev
    if circuit is not None:  # docs/07 §7.3: losses of the week / month beyond the limit → no new entry,
        ev.details["coupe_circuit"] = circuit.as_dict()  # limits waiting for a pullback included
        if circuit.tripped:
            ev.blocked.append(f"BLOQUÉ : {circuit.reason}")
    else:
        ev.details["coupe_circuit"] = {"indisponible": True}
        ev.blocked.append("WATCH : coupe-circuit indisponible (mesure en échec) — aucune entrée sans ce contrôle")
    from app.services.weak_signals import is_pump
    from app.services.weak_signals import latest as radar_latest

    hit = radar_latest(s, inst.isin, now.astimezone(PARIS).date())
    if hit is not None:  # docs/26: a fact, a caution or a block — never a reason to buy
        ev.details["signaux_faibles"] = {"etiquette": hit.label, "texte": hit.text, "jour": hit.day.isoformat(),
                                         "attention_z": hit.attention_z, "volume_relatif": hit.rvol,
                                         "nouvelles_48h": hit.news_recent}  # fmt: skip
        if is_pump(hit):
            ev.blocked.append(
                "BLOQUÉ : emballement non corroboré — attention et volume anormaux sans information publiée (docs/26)"
            )
    from app.services.social_content import facts as content_facts
    from app.services.social_content import latest as content_latest

    content = content_latest(s, inst.isin, now)
    if content is not None:  # docs/30: what the chatter is about — a fact, a block, never a reason to buy
        ev.details["echanges_forum"] = {
            "faits": content_facts(content),
            "effet": content.effect,
            "solidite": (content.data or {}).get("solidite"),
            "lu_a": content.computed_at.isoformat(timespec="minutes"),
        }
        if content.effect == "emballement":
            ev.blocked.append(
                "BLOQUÉ : emballement — échanges portés par peu de comptes ou promotionnels sur une petite "
                "capitalisation (docs/30)"
            )
    # --- costs per account (R11, R13) ---
    from app.services.ttf import ttf_status

    ttf_due, ttf_basis = ttf_status(
        inst.isin, inst.market_cap_eur, float(tax.get("ttf_cap_threshold_eur") or 1e9), now.year
    )
    ftt, ftt_basis = foreign_tax((tax.get("ftt_by_country") or {}).get(inst.isin[:2]), inst.market_cap_eur)
    target = setup.targets[0] if setup.targets else None
    # what the intraday re-evaluation needs to recompute the admission with a fresh quote (intraday_reeval)
    ev.details["configuration"] = {
        "type": setup.kind.value,
        "niveau_bas": setup.level_low,
        "p_max_technique": setup.p_max_technical,
        "declencheur": setup.entry_level,
    }
    options = _account_options(inst, config)
    # size (docs/28): reference R × the day's risk level (market, portfolio, recent results, edge — already
    # in ``level``) × the line's own reductions (opening gaps, earnings, SRD restricted), floored, capped
    others: dict[str, float] = {}
    gr = getattr(risk, "gap_risk", None) or {}
    if gr.get("enabled", True):  # gap risk: the R a stop cannot bound (risk.gap_risk)
        gbars = load_bars(s, inst.isin, limit=int(gr.get("lookback_sessions", 250)) + 1)
        gm, gq = gap_risk_multiplier(
            [b.close for b in gbars[:-1]],
            [b.open for b in gbars[1:]],
            setup.p_max_technical,
            stop_exec_for(setup.stop, float(risk.slippage_pct)),
            float(gr.get("quantile", 0.95)),
            int(gr.get("min_sessions", 120)),
        )
        if gq is not None:
            ev.details["risque_de_saut"] = {"quantile_ecart_defavorable": round(gq, 4), "multiplicateur": round(gm, 3)}
        if gm < 1:
            others["risque de saut d'ouverture"] = gm
    if inst.earnings_next_date is not None:  # a publication inside the holding horizon
        days = (inst.earnings_next_date - now.date()).days
        if 0 <= days <= HOLDING_DAYS.get(setup.horizon, 20) * 7 // 5:
            others["publication de résultats avant l'horizon"] = float(
                getattr(risk, "earnings_in_horizon_multiplier", None) or 0.75
            )
    srd_factor = (
        float((p.srd.model_extra or {}).get("restricted_size_multiplier", 0.5) if p.srd else 0.5)
        if srd_mode == "restreint"
        else 1.0
    )
    floor = float(getattr(risk, "min_size_multiplier", None) or 0.5)
    line = max(floor, combine_size_factors({}, others))  # never below the floor: dilution, not immobility
    capital = float(ref.capital_eur) if ref is not None else float(p.capital.capital_pilote_eur)
    ref_pct = float(ref.risk_pct) if ref is not None else float(risk.max_risk_per_trade_pct)
    cap_pct = float(ref.max_trade_pct) if ref is not None else 0.01
    common = min(level * line, cap_pct / ref_pct) if ref_pct > 0 else 0.0
    if others or level != 1.0:
        ev.details["reductions_taille"] = {**others, "niveau du jour": round(level, 3), "total": round(common, 4)}
    budget = capital * ref_pct * common
    est_shares = int(budget / max(setup.p_max_technical - stop_exec_for(setup.stop, risk.slippage_pct), 1e-9))
    est_notional = max(est_shares, 1) * setup.p_max_technical * fx
    pea_open = (p.pea or {}).get("opening_date")
    pea_age = (now.date() - date.fromisoformat(str(pea_open))).days / 365.25 if pea_open else None
    per_account: dict[str, Any] = {}
    sized: dict[str, tuple[Any, Any, float]] = {}  # account → (sizing, cost params, P_max): net gain per target
    best: tuple[str, Any, Any, Any] | None = None
    alt_cfg = (p.universe or {}).get("alt_listing") or {}
    alt = (
        s.scalar(
            select(InstrumentAlias.alias)
            .where(InstrumentAlias.isin == inst.isin, InstrumentAlias.provider == "eodhd_alt")
            .order_by(InstrumentAlias.alias)
        )
        if fx != 1.0 and alt_cfg
        else None
    )
    for acc in options:
        fees = _fees(config, acc)
        cp = CostParams(
            fees,
            ttf_rate=float(tax.get("ttf_rate") or 0) if ttf_due else 0.0,
            ftt_rate=ftt,
            fx_fee_pct=float(((p.brokers or {}).get("saxo") or {}).get("fx_fee_pct") or 0) if fx != 1.0 else 0.0,
            srd_daily_rate=float((p.srd.crd_daily_rate if p.srd else 0) or 0) if acc == "cto_srd" else 0.0,
            holding_days=HOLDING_DAYS.get(setup.horizon, 20),
            tax_rate=envelope_tax_rate(
                acc,
                float(tax.get("pfu_total_rate") or 0),
                float(tax.get("pea_ps_rate_after_5y") or tax.get("social_contributions_rate") or 0),
                pea_age,
                bool((p.pea or {}).get("no_withdrawal_before_5y", False)),
            ),
            assume_loss_offset=bool(tax.get("assume_loss_offset", False)),
        )
        frac = round_trip_fee_fraction(est_notional, cp)
        adm = admit(
            AdmissionInput(price, target, setup.stop, setup.kind, setup.level_low, frac, k, setup.p_max_technical)
        )
        row: dict[str, Any] = {
            "frais_aller_retour_pct": round(frac, 5),
            "frais_courtage_connus": fees.known,
            "p_max": adm.p_max,
            "statut": adm.status.value,
        }
        if alt and cp.fx_fee_pct > 0:  # docs/32 §32.3: the same entry on the euro listing, without the change fee
            adm_eur = admit(
                AdmissionInput(price, target, setup.stop, setup.kind, setup.level_low,
                               round_trip_fee_fraction(est_notional, replace(cp, fx_fee_pct=0.0)), k,
                               setup.p_max_technical)
            )  # fmt: skip
            row["sans_change"] = {"p_max": adm_eur.p_max, "statut": adm_eur.status.value}
        if adm.p_max is not None and target is not None:
            sz = size_position(
                SizingInput(
                    capital_eur=capital,
                    max_risk_pct=ref_pct,
                    entry_level=adm.p_max,
                    stop_initial=setup.stop,
                    order_kind=EntryOrderKind.LIMIT,
                    slippage_pct=float(risk.slippage_pct),
                    fees=fees,
                    max_position_pct_of_capital=float(risk.max_position_pct_of_capital),
                    max_position_pct_of_adv=float(risk.max_position_pct_of_adv),
                    ttf_rate=cp.ttf_rate,
                    fx_rate=fx,
                    fx_haircut=float(risk.fx_haircut) if inst.currency != "EUR" else 0.0,
                    adv20_eur=inst.adv_eur_20,
                    size_multiplier=min(common * (srd_factor if acc == "cto_srd" else 1.0), cap_pct / ref_pct),
                )
            )
            if sz.shares > 0:
                sized[acc] = (sz, cp, adm.p_max)
                no = net_outcome(sz.shares, adm.p_max, target, sz.stop_exec, cp, fx)
                row.update(
                    {
                        "titres": sz.shares,
                        "montant_eur": round(sz.notional_max_eur, 2),
                        "risque_eur": round(sz.risk_if_filled_at_max_eur, 2),
                        "gain_net_eur": round(no.gain_net_eur, 2),
                        "gain_net_pct": round(no.gain_net_pct, 4),
                        "perte_nette_eur": round(no.loss_net_eur, 2),
                        "perte_nette_pct": round(no.loss_net_pct, 4),
                        "ratio_net": round(no.net_reward_risk, 3) if no.net_reward_risk is not None else None,
                        "impot_eur": round(no.tax_eur, 2),
                        "portage_srd_eur": round(no.carry_eur, 2),
                    }
                )
                # best net ratio; on a tie (±0.01) the order of ``options`` wins: PEA, then cash, then SRD
                if adm.ok and (best is None or (no.net_reward_risk or 0) > (best[3].net_reward_risk or 0) + 0.01):
                    best = (acc, adm, sz, no)
            else:
                row["blocage_taille"] = sz.blocked
        per_account[acc] = row
    ev.details["comptes"] = per_account
    if alt and target is not None:
        _alt_execution(ev, alt, best[0] if best else None, sized, per_account, target, fx, setup,
                       float(alt_cfg.get("min_share_of_gain", 0.10)))  # fmt: skip
    if "pea" in per_account and (p.pea or {}).get("no_withdrawal_before_5y") and (pea_age is None or pea_age < 5):
        ev.details["fiscalite_pea"] = (
            "PEA imposé aux seuls prélèvements sociaux : suppose aucun retrait avant 5 ans (choix du 25/10/2026)"
        )
    ev.details["ttf"] = {"due": ttf_due, "base": ttf_basis}
    if (tax.get("ftt_by_country") or {}).get(inst.isin[:2]) is not None:
        ev.details["taxe_etrangere"] = {"pays": inst.isin[:2], "taux": ftt, "base": ftt_basis}
    excluded = {}
    if "pea" not in options:
        excluded["pea"] = "valeur non éligible au PEA (siège hors UE/EEE ou fonds non éligible)"
    if "cto_srd" not in options:
        excluded["cto_srd"] = (
            "SRD réservé aux valeurs d'Euronext Paris"
            if inst.mic != "XPAR"
            else f"SRD {inst.srd_status} selon les règles"
        )
    ev.details["comptes_exclus"] = excluded
    # SRD (R13): only if better than cash, or cash short on the cash accounts; restricted on Brent (R12)
    if best and best[0] == "cto_srd":
        cash_row = per_account.get("cto_cash", {})
        adv_min = float((p.srd.model_extra or {}).get("min_net_advantage_pct", 0) if p.srd else 0)
        if srd_mode == "restreint":
            adv_min = float((p.srd.model_extra or {}).get("restricted_min_net_advantage_pct", 0.01) if p.srd else 0.01)
        better = (best[3].gain_net_pct or 0) - (cash_row.get("gain_net_pct") or 0) > adv_min
        short = (book.cash.get("cto_cash") is not None) and (book.cash.get("cto_cash") or 0) < (
            cash_row.get("montant_eur") or 0
        )
        if not (better or short):
            alt = [
                (a, per_account[a]) for a in ("pea", "cto_cash") if per_account.get(a, {}).get("ratio_net") is not None
            ]
            if alt:
                a, _ = max(alt, key=lambda x: x[1]["ratio_net"])
                best = None
                ev.passed.append("SRD écarté : pas d'avantage net sur le comptant (R13)")
                ev.account = a

    def objectives(account: str | None) -> list[dict[str, Any]]:
        """Each exploitable target from the limit: gross % and, for the account, net € / % at full cost."""
        if account not in sized:
            return []
        a_sz, a_cp, a_pmax = sized[account]
        out = []
        for lvl in setup.targets:
            o = net_outcome(a_sz.shares, a_pmax, lvl, a_sz.stop_exec, a_cp, fx)
            out.append(
                {
                    "niveau": lvl,
                    "brut_pct": round(lvl / a_pmax - 1, 4),
                    "gain_net_eur": round(o.gain_net_eur, 2),
                    "gain_net_pct": round(o.gain_net_pct, 4),
                }
            )
        return out

    def assess(p_max_v: float, net_rr: float | None) -> str:
        """Score thresholds (scoring) and the market gate (R10) at the limit ``p_max_v``: BUY or WATCH, the
        reasons appended to the evaluation. Also run for a limit waiting for a pullback, which the intraday
        re-evaluation may turn into an entry (user request of 28/10/2026)."""
        # --- score ---
        from app.services.short_positions import short_interest

        short_cfg = (getattr(p.data, "short_positions", None) or {}) if p.data else {}
        shorts = short_interest(s, inst.isin, now.date())
        if shorts.holders:
            ev.details["positions_courtes"] = shorts.as_dict()  # docs/23 S2: a floor (public from 0.5 % only)
        # --- media opinions (docs/23 §23.7): caution after a media-driven jump; confirmation only when measured
        from app.services.media import favourable_sources, media_params, pressure, recent

        mp = media_params(config)
        cited = recent(s, inst.isin, now, mp.reversal_window_days)
        if cited:
            ev.details["medias"] = [
                {"source": m.source, "date": f"{m.published_at:%d/%m/%Y}", "sens": m.stance, "intervenant": m.speaker,
                 "extrait": m.snippet}
                for m in cited[:6]
            ]  # fmt: skip
        jumped = pressure(cited, price, mp.jump_flag_pct)
        if jumped is not None and jumped.price_ref:
            ev.blocked.append(
                f"WATCH : hausse médiatique {price / jumped.price_ref - 1:+.1%} depuis la citation par "
                f"{jumped.source} le {jumped.published_at:%d/%m} — risque de retour sous "
                f"{mp.reversal_window_days} jours (docs/23 §23.7)"
            )
        if "media_favourable" not in s.info:
            usable = (
                {str(f.get("name")) for f in p.data.rss_feeds if f.get("usage") == "confirmation"} if p.data else set()
            )
            s.info["media_favourable"] = favourable_sources(s, mp) & usable  # measured AND declared for confirmation
        media_confirm = any(m.stance == "achat" and m.source in s.info["media_favourable"] for m in cited)
        bars = load_bars(s, inst.isin, limit=260)
        f = compute_daily_features(bars) if bars else None
        last = bars[-1] if bars else None
        sc = score(
            ScoreInput(
                rs_rank=feats.rs_rank if feats else None,
                above_mm50_mm200=bool(
                    f and last and f.mm50 and f.mm200 and last.close > f.mm50 and last.close > f.mm200
                ),
                mm50_above_mm200=bool(f and f.mm50 and f.mm200 and f.mm50 > f.mm200),
                above_rising_mm20=bool(f and last and f.mm20 and last.close > f.mm20),
                rvol=f.rvol if f else None,
                clean_trigger=setup.detector != "external",
                over_extended=bool(f and last and f.mm20 and f.atr14 and last.close > f.mm20 + 1.5 * f.atr14),
                resistance_within_3pct=bool(target and target < price * 1.03),
                stop_distance_adr=((p_max_v - setup.stop) / (f.adr20 * p_max_v)) if f and f.adr20 else None,
                earnings_within_10_days=(
                    None if inst.earnings_next_date is None else 0 <= (inst.earnings_next_date - now.date()).days <= 10
                ),
                adv_eur=inst.adv_eur_20,
                market_cap_eur=inst.market_cap_eur,
                gap_pct=(bars[-1].open / bars[-2].close - 1) if len(bars) >= 2 and bars[-2].close else None,
                non_eur=inst.currency != "EUR",
                regime=regime,
                same_sector_positions=book.sectors.get(inst.sector or "", 0),
                catalyst_strength=_catalyst(s, inst.isin, now)[0],
                catalyst_positive=_catalyst(s, inst.isin, now)[1],
                insider_buy_days=_insider_buy_days(
                    s, inst.isin, now, int((p.scoring or {}).get("insider_window_days", 30))
                ),
                insider_buy_points=float((p.scoring or {}).get("insider_buy_points", 5)),
                short_interest_pct=shorts.total_pct if shorts.holders else None,
                short_alert_pct=float(short_cfg.get("alert_pct", 0.02)),
                short_penalty=float(short_cfg.get("score_penalty", 3)),
                catalyst_age_days=_catalyst(s, inst.isin, now)[2],
                external_confirmation=bool(_letter_levels(s, inst.isin, now.date())) or media_confirm,
                small_cap=bool(inst.small_cap),
                fundamentals=fundamentals,
                sector_rs_pct=sector_rs_pct,
            ),
            list((p.scoring or {}).get("admission_blocks", {}).get(setup.detector, ["T", "R", "M"])),
            float((p.scoring or {}).get("c_bonus_cap", 5)),
            data_complete=price_status in ("realtime", "delayed", "eod"),
            f_min_available=int((p.scoring or {}).get("f_min_available_subcriteria", 2)),
        )
        ev.details["score"] = sc.as_dict()
        sp = p.scoring or {}
        min_ratio = float(sp.get("buy_min_ratio_orange" if regime == "orange" else "buy_min_ratio", 0.75))
        action = "BUY"
        if sc.admission_ratio < min_ratio:
            action = "WATCH"
            ev.blocked.append(f"WATCH : ratio d'admission {sc.admission_ratio:.2f} < {min_ratio:.2f} (scoring)")
        if sc.confidence < int(sp.get("buy_min_confidence", 3)):
            action = "WATCH"
            ev.blocked.append(f"WATCH : confiance {sc.confidence}/5 < {sp.get('buy_min_confidence', 3)}")
        if sc.risk == "eleve":
            action = "WATCH"
            ev.blocked.append("WATCH : risque élevé (distance du stop, liquidité, petite capitalisation, devise)")
        if any(b.startswith("WATCH") for b in ev.blocked):
            action = "WATCH"
        # --- market gate (R10) ---
        mg = getattr(p, "market_gate", None) or {}
        ev.details["portail"] = gate
        if gate == "restreint" and action == "BUY":
            if (net_rr or 0) < float(mg.get("restricted_min_net_rr", 2.5)) or sc.total < float(
                mg.get("restricted_min_score", 75)
            ):
                action = "WATCH"
                ev.blocked.append("WATCH : portail restreint — ratio net ≥ 2,5 et score ≥ 75 exigés (R10)")
            else:
                ev.passed.append(
                    "portail restreint : sélection exigeante satisfaite (R10) ; taille selon le niveau du jour"
                )
        return action

    primary = best[1] if best else None
    first_row = per_account.get(best[0] if best else (ev.account or options[0]), {})
    # --- admission outcome ---
    if setup.warnings:
        ev.blocked += [f"WATCH : {w}" for w in setup.warnings]
    if target is None:
        ev.details["admission"] = "non_testable"
        ev.blocked.append("WATCH : pas d'objectif exploitable (aucune résistance ni niveau publié au-dessus)")
        ev.action = "WATCH"
        return ev
    any_adm = admit(
        AdmissionInput(
            price,
            target,
            setup.stop,
            setup.kind,
            setup.level_low,
            first_row.get("frais_aller_retour_pct", 0.0),
            k,
            setup.p_max_technical,
        )
    )
    ev.details["admission"] = any_adm.status.value
    ev.details["p_max"] = primary.p_max if primary else any_adm.p_max
    ev.details["ratio_brut"] = any_adm.gross_reward_risk
    if any_adm.status == Status.DEGRADED:
        ev.blocked += any_adm.reasons
        ev.action = "NO_TRADE"
        return ev
    if any_adm.status == Status.REFUSED and best is None:
        # the limit order waits at P_max: account and quantity sized at that limit (rule 7), cash before SRD
        fit = [a for a in options if per_account[a].get("titres") and per_account[a].get("ratio_net") is not None]
        cash = [a for a in fit if a != "cto_srd"] or fit
        if cash:
            ev.account = max(cash, key=lambda a: (per_account[a]["ratio_net"], -options.index(a)))
        if ev.account in sized:
            # the waiting limit may become an entry intraday: the score, the gate (R10) and the events a stop
            # does not cover (R14) are tested at that limit now, as for a BUY — the waiting reasons come after,
            # so that they do not short-circuit the gate
            w_sz, _, w_pmax = sized[ev.account]
            assess(w_pmax, per_account[ev.account].get("ratio_net"))
            ev.blocked += _events_blocks(s, config, inst, w_pmax, w_sz.stop_exec, now, ev)
        ev.blocked += [f"WATCH : {r} — limite posée en attente d'un repli" for r in any_adm.reasons]
        ev.action = "WATCH"
        ev.details["objectifs"] = objectives(ev.account)
        risk_wait = (per_account.get(ev.account or "", {}) or {}).get("risque_eur")
        if risk_wait:  # the waiting limit is an entry order too: the portfolio caps apply before it is placed
            ev.blocked += _portfolio_caps(
                s, config, inst, book, float(risk_wait), ev.account or "", gate, capital * ref_pct, ev
            )
        return ev
    if best is None:
        ev.blocked.append("WATCH : aucune taille possible sur les comptes (budget de risque, frais)")
        return ev
    acc, adm, sz, no = best
    ev.admitted = True
    ev.account, ev.shares = acc, sz.shares
    ev.details["objectifs"] = objectives(acc)
    ev.size_eur, ev.risk_eur = sz.notional_max_eur, sz.risk_if_filled_at_max_eur
    ev.net_rr, ev.net_gain_pct = no.net_reward_risk, no.gain_net_pct
    ev.passed.append(f"prix maximal {adm.p_max:.4g} (ratio gain/risque ≥ {k:g} net de frais, R4)")
    # the admission ratio is net of *fees* (framework §5.2); the after-tax ratio is shown and ranks, never blocks
    action = assess(adm.p_max, no.net_reward_risk)
    # --- caps (R8, R9) and override (R21) ---
    caps = _portfolio_caps(s, config, inst, book, sz.risk_if_filled_at_max_eur, acc, gate, capital * ref_pct, ev)
    ev.blocked += caps
    ev.details["blocages_plafonds"] = list(caps)
    # --- events a stop does not cover (R14) ---
    ev.blocked += _events_blocks(s, config, inst, adm.p_max, sz.stop_exec, now, ev)
    ev.action = action
    ev.details["entree"] = {"declencheur": setup.entry_level, "limite": adm.p_max, "type": "limite au prix maximal"}
    der = getattr(p, "derivatives", None) or {}
    if der.get("enabled") and inst.kind == "stock":
        from app.domain.derivatives import turbo_criteria

        tc = turbo_criteria(
            adm.p_max,
            sz.stop_exec,
            budget,
            float(der.get("barrier_buffer_pct", 0.01)),
            float(der.get("max_leverage", 5)),
            fx,
        )
        ev.details["produit_levier"] = (
            tc.__dict__ if tc else {"note": "aucun produit compatible avec le stop et le levier maximal"}
        )
    return ev


def _alt_execution(
    ev: Evaluation,
    alt: str,
    acc: str | None,
    sized: dict[str, tuple[Any, Any, float]],
    per_account: dict[str, Any],
    target: float,
    fx: float,
    setup: Setup,
    min_share: float,
) -> None:
    """docs/32 §32.3 (user decision of 26/10/2026): the primary venue first; a euro listing (Xetra) is shown as an
    execution alternative, recommended only when the change fee it avoids weighs in the net gain — or when the
    change fee alone makes the entry fail its admission (information only: the proposal stays refused)."""
    warn = (
        "liquidité et fourchette de la cotation en euros non vérifiées : à contrôler avant la saisie ; "
        "niveaux convertis au change du jour, à recaler sur son cours"
    )
    acc = acc or next(iter(sized), None)
    if acc is not None:
        sz, cp, p_max = sized[acc]
        al = alt_listing_saving(
            alt, sz.notional_max_eur, sz.shares * target * fx, per_account[acc]["gain_net_eur"], cp.fx_fee_pct,
            min_share,
        )  # fmt: skip
        if al is None:
            return
        ev.details["execution_alternative"] = {
            "cotation": al.listing,
            "compte": acc,
            "change_evite_eur": round(al.fx_saving_eur, 2),
            "part_du_gain_net": round(al.share_of_gain, 4) if al.share_of_gain is not None else None,
            "seuil": min_share,
            "recommandee": al.recommended,
            "niveaux_indicatifs_eur": {"limite": round(p_max * fx, 2), "stop": round(setup.stop * fx, 2)},
            "avertissement": warn,
        }
        return
    for name, row in per_account.items():  # nothing sized: refused on the primary venue — because of the change?
        eur = row.get("sans_change") or {}
        if eur.get("statut") == Status.ADMITTED.value and row.get("statut") != Status.ADMITTED.value:
            ev.details["execution_alternative"] = {
                "cotation": alt,
                "compte": name,
                "recommandee": True,
                "admise_seulement_sans_change": True,
                "seuil": min_share,
                "niveaux_indicatifs_eur": {"limite": round(eur["p_max"] * fx, 2), "stop": round(setup.stop * fx, 2)},
                "avertissement": warn + " ; proposition refusée sur la place principale : à réévaluer sur la "
                "cotation en euros avant toute saisie",
            }
            return


def _portfolio_caps(
    s: Session,
    config: LoadedConfig,
    inst: Instrument,
    book: Book,
    risk_eur: float,
    acc: str,
    gate: str | None,
    r_unit: float,
    ev: Any,
) -> list[str]:
    """Open-risk caps by gate and per line (R8, R9), restricted+ gate, correlated cluster."""
    risk = config.params.risk
    heat = HeatParams(
        float(config.params.capital.capital_pilote_eur),
        dict(getattr(risk, "max_open_risk_pct_by_gate", None) or {"ouvert": 0.05, "restreint": 0.035}),
        float(getattr(risk, "max_line_risk_pct_of_cap", 0.30)),
        float(getattr(risk, "reserved_signal_budget_pct", 0.0075)),
        int(getattr(risk, "reserved_budget_age_sessions", 10)),
        float(getattr(risk, "srd_risk_weight", 1.25)),
        float(getattr(risk, "heat_a_soft_cap_pct", 0.075)),
    )
    gate_key = "ouvert" if gate == "ouvert" else "restreint"
    st = heat_state(book.lines, 0.0, gate_key, heat)
    caps = check_new_line(st, risk_eur, heat, srd=(acc == "cto_srd"))
    ev.details["plafonds"] = {"plafond_eur": round(st.cap_eur, 2), "risque_ouvert_eur": round(st.heat_b_eur, 2)}
    if gate == "restreint_plus":
        caps.append(
            "BLOQUÉ : portail restreint + (risque conjoncturel élevé ou régime rouge) — dérogation seulement (R10, R21)"
        )
    caps += _cluster_cap(s, config, inst, book, risk_eur, r_unit, heat, ev)
    return caps


def _closes(s: Session, isin: str, n: int) -> dict[date, float]:
    key = f"closes:{isin}:{n}"
    if key not in s.info:
        rows = s.execute(
            select(PriceEod.day, PriceEod.close).where(PriceEod.isin == isin).order_by(PriceEod.day.desc()).limit(n)
        ).all()
        s.info[key] = {r.day: float(r.close) for r in rows}
    return s.info[key]


def _cluster_cap(
    s: Session, config: LoadedConfig, inst: Instrument, book: Book, risk_eur: float, r_unit: float, heat: Any, ev: Any
) -> list[str]:
    """Risk of the open lines correlated with the candidate (ρ ≥ threshold over 120 sessions) + the candidate
    ≤ ``max_cluster_risk_r`` × the nominal R (contre-expertise du 25/10/2026; ``factor_groups`` is empty, so
    the factor cap never applied). A cap: R21 may cover it."""
    from app.domain.heat import heat_a, heat_b
    from app.domain.risk import aligned_correlation

    cl = getattr(config.params.risk, "correlation_cluster", None) or {}
    if not cl.get("enabled", True) or not book.lines:
        return []
    n = int(cl.get("lookback_sessions", 120)) + 1
    thr, lim_r = float(cl.get("threshold", 0.6)), float(cl.get("max_cluster_risk_r", 1.5))
    cand = _closes(s, inst.isin, n)
    linked, risk = [], risk_eur
    for ln in book.lines:
        if ln.isin == inst.isin:
            continue
        rho = aligned_correlation(cand, _closes(s, ln.isin, n))
        if rho is not None and rho >= thr:
            linked.append({"isin": ln.isin, "correlation": round(rho, 2)})
            risk += max(heat_a(ln), heat_b(ln, float(heat.srd_weight)))  # a joint drop to the stops, gains included
    if not linked:
        return []
    ev.details["grappe_correlee"] = {
        "lignes": linked,
        "risque_eur": round(risk, 2),
        "plafond_eur": round(lim_r * r_unit, 2),
    }
    if risk > lim_r * r_unit:
        return [
            f"BLOQUÉ : grappe corrélée ({len(linked)} ligne(s) ρ ≥ {thr:g}) — risque {risk:.0f} € > {lim_r:g} R "
            f"({lim_r * r_unit:.0f} €, risk.correlation_cluster)"
        ]
    return []


def _events_blocks(
    s: Session, config: LoadedConfig, inst: Instrument, p_max: float, stop_exec: float, now: datetime, ev: Evaluation
) -> list[str]:
    e = getattr(config.params, "events", None) or {}
    ep = EventParams(
        int(e.get("history_count", 8)),
        int(e.get("min_history", 4)),
        float(e.get("gap_quantile", 0.75)),
        float(e.get("max_gap_loss_r", 1.5)),
    )
    out: list[str] = []
    hours = float(config.params.risk.earnings_blackout_hours)
    if inst.earnings_next_date is not None:
        delta = datetime.combine(inst.earnings_next_date, datetime.min.time(), tzinfo=UTC) - now
        if timedelta(0) <= delta <= timedelta(hours=hours + 24):
            out.append(
                f"BLOQUÉ : publication de résultats le {inst.earnings_next_date:%d/%m} — un stop ne protège pas "
                "d'un gap ; exposition historique inconnue (R14)"
            )
    keywords = list(e.get("macro_keywords") or MACRO_KEYWORDS_DEFAULT)
    soon = s.scalars(
        select(MacroEvent).where(
            MacroEvent.ts >= now, MacroEvent.ts <= now + timedelta(hours=30), MacroEvent.impact == "High"
        )
    )
    for m in soon:
        if not any(kw.lower() in m.title.lower() for kw in keywords):
            continue
        past_days = sorted(
            {
                (x.ts.replace(tzinfo=UTC) if x.ts.tzinfo is None else x.ts).astimezone(PARIS).date()
                for x in s.scalars(select(MacroEvent).where(MacroEvent.title == m.title, MacroEvent.ts < now))
            }
        )
        prevs, opens = [], []
        for d in past_days[-ep.history_count :]:
            rows = list(
                s.scalars(
                    select(PriceEod)
                    .where(PriceEod.isin == inst.isin, PriceEod.day <= d + timedelta(days=1))
                    .order_by(PriceEod.day.desc())
                    .limit(2)
                )
            )
            if len(rows) == 2:
                prevs.append(rows[1].close)
                opens.append(rows[0].open)
        exp = event_exposure(abs_gaps(prevs, opens), p_max, stop_exec, ep)
        ev.details.setdefault("annonces", []).append(
            {"titre": m.title, "devise": m.currency, "ts": m.ts, "exposition": exp.text}
        )
        if not exp.low:
            out.append(f"BLOQUÉ : {m.currency} {m.title} dans les 30 h — {exp.text} ; gel de l'entrée (R14)")
    return out


# --- scan ---------------------------------------------------------------------------------------


@dataclass
class ScanReport:
    scanned: int = 0
    setups: int = 0
    created: int = 0
    skipped_existing: int = 0
    by_action: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    by_market: dict[str, list[int]] = field(default_factory=dict)  # MIC → [scanned, setups, proposals]

    def count(self, mic: str | None, k: int) -> None:
        self.by_market.setdefault(mic or "?", [0, 0, 0])[k] += 1

    def note(self) -> str:
        acts = ", ".join(f"{k} {v}" for k, v in sorted(self.by_action.items()))
        err = f" · {len(self.errors)} valeur(s) en erreur : {', '.join(self.errors[:3])}" if self.errors else ""
        places = " ; ".join(
            f"{m} {v[0]}/{v[1]}/{v[2]}" for m, v in sorted(self.by_market.items(), key=lambda kv: -kv[1][0])
        )
        return f"{self.scanned} valeurs, {self.setups} configurations, {self.created} propositions ({acts}){err}" + (
            f" · par place (valeurs/configurations/propositions) : {places}" if places else ""
        )


def _candidates(s: Session, isins: list[str] | None) -> list[Instrument]:
    q = select(Instrument).where(Instrument.active.is_(True))
    if isins:
        q = q.where(Instrument.isin.in_(isins))
    else:
        q = q.where(Instrument.in_universe.is_(True))
    return list(s.scalars(q))


def _letter_setups(s: Session, inst: Instrument, day: date, ctx: DetectorContext) -> list[tuple[Setup, str]]:
    from app.domain.zones import exploitable_targets

    out = []
    for lv in _letter_levels(s, inst.isin, day):
        levels = lv["levels"]
        stop, target = levels.get("stop"), levels.get("objectif")
        if stop is None or target is None:
            continue  # R15: not testable — the letter's WATCH stays as recorded at reception
        entry = levels.get("achat") or levels.get("support")
        if entry is None:
            continue
        support = levels.get("support")
        # R4: the target published by the letter is the exploitable one; detected resistances above
        # the entry are shown as the next levels, never substituted to the published target
        nxt = [t for t in exploitable_targets(float(entry), ctx.zones, []) if t > float(target)]
        out.append(
            (
                Setup(
                    "external",
                    SetupKind.PULLBACK if support else SetupKind.BREAKOUT,
                    float(entry),
                    float(entry) * 1.01,
                    float(stop),
                    "lettre",
                    float(support or stop),
                    [float(target), *nxt[:1]],
                    "swing_2_6w",
                    {"lettre": lv["source"], "item_id": lv["item_id"], "niveaux": levels},
                ),
                lv["source"],
            )
        )
    return out[:1]


def scan(
    s: Session,
    config: LoadedConfig,
    day: date,
    isins: list[str] | None = None,
    now: datetime | None = None,
    quotes: dict[str, Any] | None = None,
    buzz_isins: set[str] | None = None,
) -> ScanReport:
    from app.services.letters_review import published_gate
    from app.services.macro import market_context
    from app.services.outcomes import comparable_history

    now = now or datetime.now(UTC)
    rep = ScanReport()
    if not enabled(config):
        return rep
    p = config.params
    expire_proposals(s, now)
    published, published_why = published_gate(s, config, day)
    ctxm = market_context(s, config, day, published)  # Europe: the risk level of the day (docs/28)
    ctx_by_market: dict[str, Any] = {}

    def ctx_for(mic: str | None) -> Any:  # docs/32: each value is gated by the regime of its own venue
        key = mic or "EU"
        if key not in ctx_by_market:
            ctx_by_market[key] = ctxm if key == "EU" else market_context(s, config, day, published, key)
        return ctx_by_market[key]

    book = _book(s)
    from app.services.circuit import circuit_state

    try:
        with s.begin_nested():  # a failed query never poisons the scan's transaction
            circuit = circuit_state(s, config, now)
    except Exception as exc:  # noqa: BLE001 — never a silent pass: WATCH « indisponible » on each proposal
        log.warning("coupe-circuit : mesure impossible (%s)", exc.__class__.__name__, exc_info=True)
        circuit = None
        rep.errors.append(f"coupe-circuit: {exc.__class__.__name__}")
    zp = getattr(p, "zones", None) or {}
    stop_p = p.risk.stop
    wick = float(getattr(stop_p, "wick_buffer_pct", None) or 0.005)
    evals: list[Evaluation] = []
    from app.services.rotation import sector_strength

    sectors = sector_strength(s, day)
    # phase 1 — detection on the whole universe (cheap): the day's opportunities feed the risk level
    staged: list[dict[str, Any]] = []
    for inst in _candidates(s, isins):
        try:
            rep.scanned += 1
            rep.count(inst.mic, 0)
            bars = load_bars(s, inst.isin, end=day, limit=int(zp.get("lookback_sessions", 250)) + 10)
            if len(bars) < 60:
                continue
            feats = s.scalar(
                select(FeatureDaily)
                .where(FeatureDaily.isin == inst.isin, FeatureDaily.day <= day)
                .order_by(FeatureDaily.day.desc())
            )
            f = compute_daily_features(bars)
            zones = detect_zones(
                bars[-int(zp.get("lookback_sessions", 250)) :],
                f.atr14 or 0.0,
                int(zp.get("pivot_window", 5)),
                float(zp.get("merge_atr_mult", 0.5)),
                int(zp.get("min_touches", 2)),
            )
            from app.services.fundamentals import analyst_target
            from app.services.fundamentals import facts as fundamental_facts

            ff, shown = fundamental_facts(s, inst)
            d7 = (p.detectors or {}).get("d7_trend_quality") or {}
            at = analyst_target(shown, int(d7.get("min_analysts", 3)), inst.currency) if shown else None
            ctx = DetectorContext(
                feats.rs_rank if feats else None,
                zones,
                wick,
                float(stop_p.buffer_atr_mult),
                [at] if at else [],
                buzz=bool(buzz_isins and inst.isin in buzz_isins),
            )
            found = [
                (x, "radar" if x.detector == "d8_buzz_precoce" else "moteur")
                for x in run_detectors(p.detectors or {}, bars, ctx)
            ]
            found += _letter_setups(s, inst, day, ctx)
            if found:
                staged.append({"inst": inst, "bars": bars, "feats": feats, "f": f, "zones": zones, "ff": ff,
                               "shown": shown, "at": at, "found": found})  # fmt: skip
        except Exception as exc:  # noqa: BLE001 — one bad instrument never stops the scan
            rep.errors.append(f"{inst.isin}: {exc.__class__.__name__}")
            log.warning("moteur : %s ignorée (%s)", inst.isin, exc.__class__.__name__, exc_info=True)
    # expected R per (detector, source): live history first, the backtest's control period otherwise
    from app.services.backtest import prior_expected_r

    k_shr = int(getattr(getattr(p, "learning", None), "shrinkage_k", 20) or 20)
    expect: dict[tuple[str, str], tuple[float | None, Any, Any]] = {}
    for st_ in staged:
        for setup, source in st_["found"]:
            key = (setup.detector, source)
            if key not in expect:
                track = comparable_history(s, config, SimpleNamespace(detector=setup.detector, source=source), now)
                er = track.shrunk_r if track is not None and track.verdict != "insuffisant" else None
                prior = prior_expected_r(s, setup.detector, k_shr) if er is None else None
                expect[key] = (er if er is not None else (round(prior[0], 3) if prior else None), track, prior)
    # the day's risk level (docs/28): market, portfolio, recent results, measured edge, opportunities
    from app.domain.risk_dial import conviction
    from app.services.risk_level import compute as compute_level
    from app.services.risk_level import dial_params, reference

    strong = sum(
        1
        for st_ in staged
        for setup, _ in st_["found"]
        if setup.targets
        and setup.p_max_technical > setup.stop
        and (setup.targets[0] - setup.p_max_technical) / (setup.p_max_technical - setup.stop) >= 2.5
    )
    edge = any(v[0] is not None and v[0] > 0 for v in expect.values())
    ref = reference(s, config, now)
    dparams = dial_params(config)
    from app.services.risk_level import today as level_today

    existing_level = level_today(s, day) if isins else None
    try:
        if existing_level is not None:  # a targeted scan (radar, letter) keeps the day's level as recommended
            level, level_row = existing_level.applied, existing_level
        else:
            with s.begin_nested():
                level, level_row = compute_level(s, config, day, ctxm.regime, ctxm.level, circuit, strong, edge, now)
    except Exception as exc:  # noqa: BLE001 — without a level, the normal size applies (× 1), said on the cards
        log.warning("niveau de risque : calcul impossible (%s)", exc.__class__.__name__, exc_info=True)
        level, level_row = 1.0, None
    level_info = {
        "niveau_applique": level,
        "niveau_conseille": level_row.recommended if level_row is not None else None,
        "modifie_par_vous": level_row is not None and level_row.override is not None,
        "r_reference_eur": round(ref.r_eur, 2),
        "source_reference": ref.source,
    }
    # phase 2 — evaluation at the day's level
    for st_ in staged:
        inst = st_["inst"]
        try:
            bars, feats, f, zones, ff, shown, at = (
                st_[x] for x in ("bars", "feats", "f", "zones", "ff", "shown", "at")
            )
            q = (quotes or {}).get(inst.isin)
            live = q is not None and getattr(q, "last", None) is not None
            price = float(q.last) if live else bars[-1].close
            status = q.data_status.value if live else "eod"
            last_day = s.scalar(
                select(PriceEod.day)
                .where(PriceEod.isin == inst.isin, PriceEod.day <= day)
                .order_by(PriceEod.day.desc())
            )
            price_source = q.source if live else "clôture officielle stockée"
            price_ts = q.market_timestamp if live else last_day
            cm = ctx_for(inst.mic)
            for setup, source in st_["found"]:
                rep.setups += 1
                rep.count(inst.mic, 1)
                er, track, prior = expect.get((setup.detector, source), (None, None, None))
                conv, conv_why = conviction(er, dparams)
                line_level = (
                    min(level * conv, dparams.max_level) if level_row is None or level_row.override is None else level
                )
                ev = evaluate(
                    s,
                    config,
                    inst,
                    setup,
                    source,
                    price,
                    status,
                    book,
                    cm.gate,
                    cm.regime,
                    cm.srd_mode,
                    now,
                    feats,
                    ff,
                    (sectors.get(inst.sector or "") or {}).get("centile"),
                    circuit,
                    level=line_level,
                    ref=ref,
                )
                ev.details["niveau_risque"] = {**level_info, "niveau_ligne": round(line_level, 3),
                                               "conviction": conv_why or None}  # fmt: skip
                ev.details["source_cours"], ev.details["horodatage_cours"] = price_source, price_ts
                for o in ev.details.get("objectifs") or []:  # docs/22 §22.2: each target with its origin
                    o["origine"] = _target_origin(o["niveau"], setup, at, f.high_52w)
                if shown:
                    ev.details["fondamentaux"] = {
                        **shown,
                        "objectif_retenu": at is not None and at in (setup.targets or []),
                    }
                ev.details["contexte_marche"] = {
                    "marche": cm.market,
                    "regime": cm.regime,
                    "regime_de": cm.regime_source,  # EU when the venue has no regime of its own
                    "risque_conjoncturel": cm.level,
                    "composantes": cm.components,
                    "portail": cm.gate,
                    "raison_portail": cm.gate_reason,
                    "publication_lettres": published_why,
                    "mode_srd": cm.srd_mode,
                }
                ev.details["zones"] = [
                    {"bas": z.low, "haut": z.high, "contacts": z.touches, "type": z.kind} for z in zones[-8:]
                ]
                if ev.admitted:
                    ev.expected_r = er
                    ev.details["historique"] = track.as_dict() if track is not None else None
                    if prior is not None and er is not None:  # docs/27 §27.5: control-period prior
                        ev.details["esperance_backtest"] = {"r_net_retreci": round(prior[0], 3), "n": prior[1]}
                evals.append(ev)
        except Exception as exc:  # noqa: BLE001 — one bad instrument never stops the scan
            rep.errors.append(f"{inst.isin}: {exc.__class__.__name__}")
            log.warning("moteur : %s ignorée (%s)", inst.isin, exc.__class__.__name__, exc_info=True)

    _persist(s, config, day, now, evals, rep)
    return rep


def rescan_today(s: Session, config: LoadedConfig, day: date, now: datetime | None = None) -> int:
    """docs/28 — after the user changed the day's risk level: the day's open proposals without any decision
    are withdrawn (soft delete, snapshots kept) and the engine runs again at the new level."""
    from app.db.models import Decision

    now = now or datetime.now(UTC)
    start = datetime.combine(day, datetime.min.time(), tzinfo=PARIS).astimezone(UTC)
    decided = {d.proposal_id for d in s.scalars(select(Decision))}
    for p_ in s.scalars(
        select(Proposal).where(Proposal.created_at >= start, Proposal.deleted_at.is_(None), Proposal.status == "open")
    ):
        if p_.id not in decided:
            p_.deleted_at = now
    s.flush()
    return scan(s, config, day, now=now).created


@lru_cache(maxsize=1)
def _calendars() -> Any:
    try:
        return load_market_calendars()
    except (OSError, ValueError, KeyError):  # no calendar file: the expiry falls back to calendar days, said so
        return None


def _expiry(s: Session, cals: Any, e: Evaluation, day: date, now: datetime, sessions: int) -> datetime:
    """Expiry of the order: the close of the ``sessions``-th session of the value's own venue after the signal
    day (user decision of 27/10/2026: an order with an expiry date). The date to type at the broker is stored in
    ``details["validite"]``. Without a calendar for the venue, the former calendar-day rule, marked as such."""
    inst = s.get(Instrument, e.isin)
    mic = inst.mic if inst is not None else None
    try:
        venue = cals.get(mic) if cals is not None and mic else None
        last = order_expiry_session(venue, day, sessions) if venue is not None else None
        close = venue.session(last).close_utc() if venue is not None and last is not None else None
    except (KeyError, ValueError):
        last, close = None, None
    if last is None or close is None:
        e.details["validite"] = {"seances": sessions, "jusqu_au": None, "place": mic, "approximation": True}
        return now + timedelta(days=sessions + 2)
    e.details["validite"] = {"seances": sessions, "jusqu_au": last.isoformat(), "place": mic}
    return close


def _persist(
    s: Session, config: LoadedConfig, day: date, now: datetime, evals: list[Evaluation], rep: ScanReport
) -> None:
    p = config.params
    start = datetime.combine(day, datetime.min.time(), tzinfo=PARIS).astimezone(UTC)
    replaced = {  # signals whose proposal was withdrawn by a rescan at another risk level (docs/28)
        x.signal_id
        for x in s.scalars(select(Proposal).where(Proposal.created_at >= start, Proposal.deleted_at.is_not(None)))
    }
    existing = {
        (x.isin, x.detector)
        for x in s.scalars(select(Signal).where(Signal.ts >= start, Signal.detector != "external"))
        if x.id not in replaced
    } | {
        (x.isin, "external")
        for x in s.scalars(select(Signal).where(Signal.ts >= start, Signal.source == "moteur_lettre"))
        if x.id not in replaced
    }
    ranked = rank(
        [
            RankItem(f"{e.isin}:{e.detector}", e.net_rr, e.net_gain_pct, expected_r=e.expected_r)
            for e in evals
            if e.admitted and not any(b.startswith("WATCH : pas") for b in e.blocked)
        ]
    )
    ranks = {it.key: (r, er) for r, it, er in ranked}
    o = (p.risk.model_extra or {}).get("override") if hasattr(p.risk, "model_extra") else None
    op = OverrideParams(o["min_score"], o["min_confidence"], o["min_net_rr"], o["max_multiplier"]) if o else None
    expiry_sessions = int((getattr(p, "entry", None) or {}).get("expiry_sessions", 3))
    cals = _calendars()
    for e in evals:
        key = (e.isin, e.detector)
        if key in existing:
            rep.skipped_existing += 1
            continue
        try:
            with s.begin_nested():  # savepoint: a row the database rejects never aborts the others
                r, er = ranks.get(f"{e.isin}:{e.detector}", (None, None))
                e.details["rang"] = r
                e.details["esperance_nette_r"] = er
                caps_only = set(e.details.get("blocages_plafonds") or [])  # R21 covers the caps only
                blocked_caps = [b for b in e.blocked if b.startswith("BLOQUÉ") and b in caps_only]
                other_blocks = [b for b in e.blocked if b.startswith("BLOQUÉ") and b not in caps_only]
                if op is not None and r is not None and blocked_caps and not other_blocks and e.action == "BUY":
                    sc = e.details.get("score") or {}
                    ok, missing = override_eligible(
                        r, float(sc.get("total") or 0), int(sc.get("confiance") or 0), e.net_rr, op
                    )
                    cap = (e.details.get("plafonds") or {}).get("plafond_eur") or 0
                    e.details["derogation"] = {
                        "proposable": ok and override_fits(float(e.risk_eur or 0), float(cap), op),
                        "manque": missing,
                        "rappel": "jamais appliquée sans l'accord explicite de l'utilisateur (R21)",
                    }
                sig = record_signal(
                    s,
                    e.isin,
                    e.detector,
                    "moteur_lettre" if e.detector == "external" else "moteur",
                    now,
                    "eod",
                    e.price_status,
                    p.version,
                    evidence={**e.setup.evidence, "stop_base": e.setup.stop_basis},
                    entry_low=e.setup.entry_level,
                    entry_high=e.details.get("p_max"),
                    stop_initial=e.setup.stop,
                    targets=e.setup.targets,
                    horizon=e.setup.horizon,
                )
                prop = create_proposal(
                    s,
                    sig,
                    e.action,
                    {
                        e.isin: {
                            "last": e.price,
                            "data_status": e.price_status,
                            "source": e.details.get("source_cours"),
                            "market_timestamp": e.details.get("horodatage_cours"),
                            "releve_a": now,
                        }
                    },
                    {"passed": e.passed, "blocked": e.blocked},
                    p.version,
                    to_verify=e.price_status not in ("realtime", "delayed", "eod"),
                    expires_at=_expiry(s, cals, e, day, now, expiry_sessions),
                    config=config,
                    account_recommended=e.account,
                    entry_zone=e.details.get("entree"),
                    stop=e.setup.stop,
                    targets=e.setup.targets,
                    horizon=e.setup.horizon,
                    size_eur=e.size_eur,
                    shares=e.shares or None,
                    risk_eur=e.risk_eur,
                    cost_estimate=e.details.get("comptes"),
                )
                prop.rank = r
                prop.details = _json(e.details)
                rep.created += 1
                rep.by_action[prop.action] = rep.by_action.get(prop.action, 0) + 1
                inst_e = s.get(Instrument, e.isin)
                rep.count(inst_e.mic if inst_e else None, 2)
        except Exception as exc:  # noqa: BLE001
            rep.errors.append(f"{e.isin}: {exc.__class__.__name__}")
            log.warning("moteur : enregistrement de %s refusé (%s)", e.isin, exc.__class__.__name__, exc_info=True)
    s.flush()


def _target_origin(level: float, setup: Setup, analysts: float | None, high_52w: float | None) -> str:
    def same(x: Any) -> bool:
        return isinstance(x, int | float) and abs(level - float(x)) <= 1e-6 * max(1.0, abs(level))

    if setup.detector == "external" and level == setup.targets[0]:
        return "objectif publié par la lettre"
    if same(analysts):
        return "objectif moyen des analystes"
    if same(high_52w):
        return "plus haut sur 52 semaines"
    if same(setup.evidence.get("mesure_base")):
        return "hauteur de la base reportée"
    return "résistance (zone de prix)"


def _json(obj: Any) -> Any:
    import json

    return json.loads(json.dumps(obj, default=str))


def todays_ranked(s: Session, day: date) -> list[Proposal]:
    start = datetime.combine(day, datetime.min.time(), tzinfo=PARIS).astimezone(UTC)
    rows = list(s.scalars(select(Proposal).where(Proposal.created_at >= start, Proposal.deleted_at.is_(None))))
    return sorted(rows, key=lambda x: (x.rank is None, x.rank or 0, x.id))


ACTION_LABEL = {"BUY": "ACHAT", "WATCH": "À SURVEILLER", "NO_TRADE": "PAS DE TRADE"}


def notify_top(s: Session, config: LoadedConfig, alerts: Any, mode: str, day: date) -> int:
    """P2 for the top ``ranking.notify_top_n`` unblocked BUY of the day (docs/22 §22.4): ≤ 12 lines,
    decision buttons; deduplicated per value, kind and day by the alert service."""
    from app.notify.base import Event
    from app.notify.telegram import proposal_buttons
    from app.services.instruments import name_of

    top_n = int((getattr(config.params, "ranking", None) or {}).get("notify_top_n", 3))
    sent = 0
    for p in todays_ranked(s, day):
        if sent >= top_n:
            break
        blocked = [b for b in (p.gates or {}).get("blocked", []) if b.startswith("BLOQUÉ")]
        if p.action != "BUY" or p.rank is None or blocked or p.sent_at is not None:
            continue
        d = p.details or {}
        acc = (d.get("comptes") or {}).get(p.account_recommended or "", {})
        th = (p.thesis or {}).get("arguments") or []
        lines = [
            f"Compte : {p.account_recommended} · {acc.get('titres')} titres · limite {d.get('p_max'):.4g}"
            if d.get("p_max")
            else f"Compte : {p.account_recommended}",
            f"Stop {p.stop:.4g} · objectif {(p.targets or [None])[0]}",
            f"Gain net {(acc.get('gain_net_pct') or 0) * 100:.1f} % · perte nette "
            f"{(acc.get('perte_nette_pct') or 0) * 100:.1f} % · ratio net {acc.get('ratio_net')}",
            f"Portail {d.get('portail')} · horizon {p.horizon}",
        ]
        lv = d.get("niveau_risque") or {}
        if lv.get("niveau_applique") is not None:  # docs/28: the day's risk level, explained on /risque
            lines.append(
                f"Niveau de risque × {lv['niveau_applique']:.2f}"
                + (" (modifié par vous)" if lv.get("modifie_par_vous") else " (conseillé)")
                + " · pourquoi : /risque"
            )
        if th:
            lines.append("Thèse : " + th[0]["texte"][:180])
        lines.append("Pas un ordre : à saisir vous-même · fiche complète sur /opportunites")
        ok = alerts.emit(
            s,
            Event(
                "P2",
                "proposal_buy",
                f"#{p.rank} {name_of(p.isin)} — {ACTION_LABEL.get(p.action, p.action)}",
                lines,
                proposal_buttons(p.id),
                isin=p.isin,
            ),
            mode,
        )
        if ok:
            p.sent_at, p.channels = datetime.now(UTC), ["telegram"]
            sent += 1
    s.flush()
    return sent
