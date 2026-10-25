"""docs/24 v2 — investment committee in **observation mode**. Two LLM calls on a pseudonymised dossier of
numbered facts and Python base rates: a pre-mortem (« the trade failed: why? ») and a chair's bounded
verdict. Python checks every element (fact ids, numbers), computes the effect the verdict *would* have
(never above the engine's proposal) and stores it in ``committee_runs``; the proposal is never changed.
The verdicts are measured against the outcomes before any ADR may apply them."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import LoadedConfig
from app.db.models import CommitteeRun, Instrument, LlmCall, Proposal, SignalOutcome
from app.domain.timeutil import PARIS
from app.services.assistant import unsupported_numbers
from app.services.thesis import _detector, facts_for

log = logging.getLogger("bourse.committee")

VERDICTS = ("maintenir", "reduire_un_cran", "reduire_deux_crans", "attendre_repli", "attendre_evenement", "rejeter")
CAUTION = {v: k for k, v in enumerate(VERDICTS)}  # higher = more cautious


@dataclass
class CommitteeSettings:
    enabled: bool = True
    mode: str = "observation"  # the only mode until an ADR (docs/24 §24.13)
    top_n: int = 5
    daily_budget_usd: float = 0.5
    reduce_step: float = 0.75
    premortem_model: str | None = None  # default: llm.write_model
    chair_model: str | None = None
    max_tokens: int = 1200
    est_input_tokens: int = 6000  # budget pre-reservation per call (prices of the model)
    est_output_tokens: int = 900


def settings(config: LoadedConfig) -> CommitteeSettings:
    raw = (config.raw or {}).get("committee") or {}
    st = CommitteeSettings()
    for k in st.__dataclass_fields__:
        if k in raw and raw[k] is not None:
            setattr(st, k, type(getattr(st, k))(raw[k]) if getattr(st, k) is not None else raw[k])
    st.mode = "observation"  # never applied without an ADR, whatever params.yaml says
    return st


# --- dossier ----------------------------------------------------------------------------------------


def pseudonymise(facts: list[dict[str, str]], inst: Instrument | None, isin: str) -> list[dict[str, str]]:
    """The company is masked (name, ISIN, tickers) so that the models judge the facts, not a memory of it."""
    names = [n for n in (inst.name if inst else None, inst.ticker_local if inst else None) if n and len(n) >= 3]
    pats = [re.compile(rf"(?<![\w]){re.escape(n)}(?![\w])", re.IGNORECASE) for n in names]
    out = []
    for f in facts:
        t = f["texte"].replace(isin, "ISIN masqué")
        for p in pats:
            t = p.sub("la société", t)
        out.append({"id": f["id"], "texte": t})
    return out


def base_rates(s: Session, p: Proposal) -> list[str]:
    """Python base rates: the detector in the latest backtest (production segment) and the live history."""
    from app.db.models import BacktestRun

    out: list[str] = []
    det = _detector(s, p)
    run = s.scalar(select(BacktestRun).order_by(BacktestRun.id.desc()))
    seg = ((run.summary or {}).get("segments") or {}).get(f"production:{det}") if run else None
    if seg and seg.get("n"):
        ci = (
            f", intervalle à 95 % [{seg['ci_low']:+.2f} ; {seg['ci_high']:+.2f}] R"
            if seg.get("ci_low") is not None
            else ""
        )
        out.append(
            f"Taux de base (backtest {run.start:%Y}–{run.end:%Y}, coût complet, biais de survie : optimiste) : "
            f"{seg['n']} trades, R net moyen {seg['mean_r']:+.2f}, gagnants {seg['hit_rate'] * 100:.0f} %{ci}"
        )
    else:
        out.append("Taux de base du détecteur au backtest : inconnu (aucun backtest enregistré)")
    hist = (p.details or {}).get("historique") or {}
    if hist.get("n"):
        r = hist.get("shrunk_r")
        shrunk = f"{r:+.2f} R" if r is not None else "non calculée"
        out.append(
            f"Historique réel de propositions comparables : {hist['n']} cas, espérance rétrécie {shrunk} "
            f"({hist.get('verdict')})"
        )
    else:
        out.append("Historique réel de propositions comparables : insuffisant")
    return out


def dossier(s: Session, p: Proposal, now: datetime) -> list[dict[str, str]]:
    facts = facts_for(s, p, now)
    extra = base_rates(s, p)
    facts += [{"id": f"F{len(facts) + k + 1}", "texte": t} for k, t in enumerate(extra)]
    return pseudonymise(facts, s.get(Instrument, p.isin), p.isin)


def dossier_hash(facts: list[dict[str, str]], prompt_versions: str) -> str:
    return hashlib.sha256(
        (prompt_versions + json.dumps(facts, ensure_ascii=False, sort_keys=True)).encode()
    ).hexdigest()


# --- effect (Python, bounded) -------------------------------------------------------------------------


def bounded_effect(p: Proposal, action: str, st: CommitteeSettings) -> dict[str, Any]:
    """What the verdict would change if it were applied: only ever more cautious than the engine."""
    shares = int(p.shares or 0)
    if action == "maintenir":
        return {"action": p.action, "titres": shares, "note": "proposition inchangée"}
    if action in ("reduire_un_cran", "reduire_deux_crans"):
        m = min(1.0, max(0.0, st.reduce_step)) if action == "reduire_un_cran" else 0.5  # never above the engine
        new = int(math.floor(shares * m))
        return {"action": p.action if new > 0 else "WATCH", "titres": new, "multiplicateur": m}
    if action in ("attendre_repli", "attendre_evenement"):
        return {"action": "WATCH" if p.action == "BUY" else p.action, "titres": shares, "limite": "conservée"}
    return {"action": "NO_TRADE", "titres": 0}


# --- guards -------------------------------------------------------------------------------------------


def _check(items: list[Any], facts: list[dict[str, str]], what: str, rejected: list[dict[str, str]]) -> list[dict]:
    ids = {f["id"] for f in facts}
    pack = {"faits": facts}
    kept = []
    for it in items:
        d = it.model_dump()
        used = [x for x in d.get("faits", []) if x in ids]
        bad = unsupported_numbers(d["texte"], pack)
        if not used:
            rejected.append({"element": f"{what} : {d['texte'][:80]}", "motif": "sans fait du dossier"})
        elif bad:
            rejected.append(
                {"element": f"{what} : {d['texte'][:80]}", "motif": "chiffres absents : " + ", ".join(bad[:4])}
            )
        else:
            kept.append({**d, "faits": used})
    return kept


def _clean(text: str, facts: list[dict[str, str]], what: str, rejected: list[dict[str, str]]) -> str:
    bad = unsupported_numbers(text, {"faits": facts})
    if bad:
        rejected.append({"element": what, "motif": "chiffres absents : " + ", ".join(bad[:4])})
        return ""
    return text


# --- budget -------------------------------------------------------------------------------------------


def spent_today(s: Session, now: datetime) -> float:
    day0 = now.astimezone(PARIS).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
    return float(
        s.scalar(
            select(func.coalesce(func.sum(LlmCall.cost_usd), 0.0)).where(
                LlmCall.ts >= day0, LlmCall.purpose.like("committee%")
            )
        )
        or 0.0
    )


def reservation(gateway: Any, st: CommitteeSettings, models: list[str]) -> float | None:
    """Estimated cost of one deliberation before any call (None = a price is unknown: no call)."""
    total = 0.0
    for m in models:
        price = gateway.settings.prices.get(m)
        if not price:
            return None
        total += st.est_input_tokens * price["input"] / 1e6 + st.est_output_tokens * price["output"] / 1e6
    return total


# --- deliberation -------------------------------------------------------------------------------------


def deliberate(
    s: Session, config: LoadedConfig, gateway: Any, p: Proposal, now: datetime | None = None
) -> CommitteeRun:
    from app.llm.gateway import LlmUnavailable, load_prompt
    from app.llm.schemas import ChairOut, PreMortemOut

    now = now or datetime.now(UTC)
    st = settings(config)
    facts = dossier(s, p, now)
    pdir = gateway.settings.prompts_dir if gateway is not None else None
    versions = (
        f"premortem_v{load_prompt(pdir, 'committee_premortem')[0]}|chair_v{load_prompt(pdir, 'committee_chair')[0]}"
        if pdir
        else "regles"
    )
    h = dossier_hash(facts, versions)
    existing = s.scalar(select(CommitteeRun).where(CommitteeRun.proposal_id == p.id, CommitteeRun.dossier_hash == h))
    if existing is not None and existing.status in ("ok", "quorum"):
        return existing  # idempotent: same proposal, same dossier, same prompts → same deliberation
    run = existing or CommitteeRun(proposal_id=p.id, dossier_hash=h)  # a run without verdict is retried
    run.day, run.mode, run.status, run.created_at = now.astimezone(PARIS).date(), st.mode, "indisponible", now
    run.dossier = {"faits": facts, "versions": versions}
    s.add(run)
    if gateway is None or not getattr(gateway, "enabled", False):
        run.verdict = {"motif": "IA non configurée : comité non réuni"}
        s.flush()
        return run
    m1 = st.premortem_model or gateway.settings.write_model
    m2 = st.chair_model or gateway.settings.write_model
    est = reservation(gateway, st, [m1, m2])
    if est is None or spent_today(s, now) + est > st.daily_budget_usd:
        run.status = "budget"
        run.verdict = {"motif": f"plafond du comité ({st.daily_budget_usd} $ / jour) : comité non réuni"}
        s.flush()
        return run
    rejected: list[dict[str, str]] = []
    user = "<faits>\n" + json.dumps(facts, ensure_ascii=False, indent=1) + "\n</faits>"
    cost = 0.0
    try:
        pm, u1 = gateway.parse(s, "committee_premortem", "committee_premortem", user, PreMortemOut, model=m1,
                               max_tokens=st.max_tokens)  # fmt: skip
        cost += u1.cost_usd
        causes = _check(pm.causes, facts, "cause", rejected)
        run.premortem = {
            "causes": causes,
            "signal_contraire_le_plus_fort": _clean(
                pm.signal_contraire_le_plus_fort, facts, "signal contraire", rejected
            ),
            "donnee_manquante_decisive": _clean(pm.donnee_manquante_decisive, facts, "donnée manquante", rejected),
        }
        if not causes:  # quorum: no valid contradiction → no verdict (never a « maintenir » by default)
            run.status, run.rejected, run.cost_usd, run.models = "quorum", rejected, cost, [m1]
            run.verdict = {"motif": "pré-mortem sans cause valide : comité sans quorum"}
            s.flush()
            return run
        user2 = user + "\n<premortem>\n" + json.dumps(run.premortem, ensure_ascii=False, indent=1) + "\n</premortem>"
        ch, u2 = gateway.parse(s, "committee_chair", "committee_chair", user2, ChairOut, model=m2,
                               max_tokens=st.max_tokens)  # fmt: skip
        cost += u2.cost_usd
    except LlmUnavailable as exc:
        run.status, run.cost_usd = "indisponible", cost
        run.verdict = {"motif": f"IA indisponible : {exc}"}
        s.flush()
        return run
    motifs = _check(ch.motifs, facts, "motif", rejected)
    action, confidence = ch.action_comite, ch.confiance_comite
    if not motifs:
        action, confidence = "maintenir", "faible"
        rejected.append({"element": "verdict", "motif": "aucun motif valide : verdict neutralisé (maintenir, faible)"})
    run.verdict = {
        "action_comite": action,
        "confiance_comite": confidence,
        "motifs": motifs,
        "dissidence_retenue_ou_ecartee": _clean(ch.dissidence_retenue_ou_ecartee, facts, "dissidence", rejected),
        "condition_de_revision": _clean(ch.condition_de_revision, facts, "condition de révision", rejected),
    }
    run.status, run.action_comite, run.confiance_comite = "ok", action, confidence
    run.effect = {**bounded_effect(p, action, st), "applique": False, "mode": st.mode}
    run.rejected, run.cost_usd, run.models = rejected, cost, [m1, m2]
    s.flush()
    return run


def candidates(s: Session, day: date, top_n: int) -> list[Proposal]:
    """The day's ranked BUY (top N) and every proposal eligible to an override (R21)."""
    start = datetime.combine(day, datetime.min.time(), tzinfo=PARIS).astimezone(UTC)
    rows = list(
        s.scalars(
            select(Proposal).where(
                Proposal.created_at >= start,
                Proposal.created_at < start + timedelta(days=1),
                Proposal.deleted_at.is_(None),
            )
        )
    )
    # one deliberation per setup (contre-expertise du 25/10/2026): a value + detector already deliberated
    # in the last 7 days is not counted again — repeated cases would inflate the measure
    recent = {
        (pp.isin, _detector(s, pp))
        for r in s.scalars(
            select(CommitteeRun).where(
                CommitteeRun.status == "ok", CommitteeRun.day >= day - timedelta(days=7), CommitteeRun.day < day
            )
        )
        if (pp := s.get(Proposal, r.proposal_id)) is not None
    }
    fresh = [p for p in rows if (p.isin, _detector(s, p)) not in recent]
    buys = sorted((p for p in fresh if p.action == "BUY" and p.rank is not None), key=lambda p: p.rank or 999)[:top_n]
    r21 = [p for p in fresh if ((p.details or {}).get("derogation") or {}).get("proposable") and p not in buys]
    return buys + r21


def run_day(
    s: Session, config: LoadedConfig, gateway: Any, day: date, now: datetime | None = None
) -> list[CommitteeRun]:
    st = settings(config)
    if not st.enabled:
        return []
    if now is None:  # a replay of a past day never reads information published after that morning
        today = datetime.now(UTC).astimezone(PARIS).date()
        now = (
            datetime.now(UTC)
            if day >= today
            else datetime.combine(day, datetime.min.time(), tzinfo=PARIS).replace(hour=8, minute=10).astimezone(UTC)
        )
    out = []
    for p in candidates(s, day, st.top_n):
        try:
            with s.begin_nested():
                out.append(deliberate(s, config, gateway, p, now))
        except Exception as exc:  # noqa: BLE001 — one deliberation failing never stops the others
            log.warning("comité %s en échec : %s", p.id, exc.__class__.__name__, exc_info=True)
    return out


def latest_for(s: Session, proposal_id: int) -> CommitteeRun | None:
    return s.scalar(
        select(CommitteeRun).where(CommitteeRun.proposal_id == proposal_id).order_by(CommitteeRun.id.desc())
    )


def measure(s: Session) -> dict[str, dict[str, Any]]:
    """docs/24 §24.6 — outcomes of the proposals per committee verdict (final outcomes only, observation
    mode: the verdict changed nothing, so the comparison is clean)."""
    from app.domain.backtest import summarize

    by: dict[str, list[float]] = {}
    for run in s.scalars(select(CommitteeRun).where(CommitteeRun.status == "ok")):
        o = s.scalar(select(SignalOutcome).where(SignalOutcome.proposal_id == run.proposal_id))
        if o is None or not o.final or o.r_net is None:
            continue
        by.setdefault(f"{run.action_comite}:{run.confiance_comite}", []).append(o.r_net)
        by.setdefault(str(run.action_comite), []).append(o.r_net)
    return {k: summarize(v).as_dict() for k, v in sorted(by.items())}
